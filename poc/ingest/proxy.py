"""Cached low-resolution proxies and timeline thumbnails."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from poc.ingest.normalize import (
    FFMPEG_EXE,
    FFMPEG_NETWORK_SAFE,
    _validate_no_network_paths,
    build_normalize_filter,
    normalize_asset,
)
from poc.timeline import MediaAsset


PROXY_HEIGHT = 720
PROXY_FPS = 30
PROXY_PRESET = "veryfast"
PROXY_CRF = 28
# A keyframe every half second: the timeline preview seeks at almost every cut, and a seek has
# to decode from the previous keyframe (x264's default is one every 250 frames, ~8 s).
PROXY_GOP = 15


@lru_cache(maxsize=1)
def _ffmpeg_version() -> str:
    result = subprocess.run(
        [FFMPEG_EXE, *FFMPEG_NETWORK_SAFE[1:], "-version"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()[0] if result.stdout else "unknown"


def _source_path(asset: MediaAsset) -> Path:
    if asset.uri is None:
        raise ValueError("Media asset must have a local URI")
    _validate_no_network_paths(asset.uri)
    path = Path(asset.uri)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path



# Cached media is built on demand from server threads, and the browser often asks for the
# same file twice at once (two preview players, Range requests). Each output therefore gets
# one build lock, a unique partial file, and a decode check before it becomes the cache.
# CACHE_VERSION 2 discards caches built before this (some could be corrupt).
# CACHE_VERSION 3 rebuilds proxies with short keyframe intervals (fast seeking).
CACHE_VERSION = 3
_BUILD_LOCKS: dict[str, threading.Lock] = {}
_BUILD_LOCKS_GUARD = threading.Lock()


@contextmanager
def _building(out_path: Path):
    with _BUILD_LOCKS_GUARD:
        lock = _BUILD_LOCKS.setdefault(str(out_path), threading.Lock())
    with lock:
        yield


def _partial(out_path: Path, suffix: str) -> Path:
    """A unique partial file next to ``out_path``. Call inside ``_building(out_path)``: it also
    removes leftovers of earlier interrupted builds of the same output."""
    for stale in out_path.parent.glob(f"{out_path.stem}.*.partial{suffix}"):
        stale.unlink(missing_ok=True)
    return out_path.with_name(f"{out_path.stem}.{os.getpid()}.{uuid.uuid4().hex[:10]}.partial{suffix}")


def _check_media(path: Path, *, video: bool = False, audio: bool = False) -> None:
    """Raise if ``path`` cannot be opened and decoded (first frame of each wanted stream)."""
    import av
    try:
        with av.open(str(path)) as container:
            for kind, wanted in (("video", video), ("audio", audio)):
                if not wanted:
                    continue
                streams = getattr(container.streams, kind)
                if not streams:
                    raise RuntimeError(f"no {kind} stream")
                container.seek(0)
                next(container.decode(**{kind: 0}))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"generated media is not playable: {exc}") from exc

def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_key(asset: MediaAsset, path: Path, params: dict) -> str:
    content_hash = asset.metadata.get("content_hash")
    if content_hash is None:
        content_hash = _content_hash(path)
    elif not isinstance(content_hash, str) or len(content_hash) != 64:
        raise ValueError("asset content_hash must be a 64-character SHA-256 hex digest")
    else:
        try:
            int(content_hash, 16)
        except ValueError as exc:
            raise ValueError("asset content_hash must be a 64-character SHA-256 hex digest") from exc
    payload = {
        "content_sha256": content_hash.lower(),
        "params": params,
        "ffmpeg_version": _ffmpeg_version(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _duration_seconds(asset: MediaAsset, normalized) -> float:
    duration_ms = (
        (asset.duration_ms or normalized.duration_ms)
        if asset.kind == "image" else normalized.duration_ms
    )
    if not duration_ms:
        raise ValueError("Media asset duration is required to make a proxy or thumbnails")
    return duration_ms / 1000.0


def _input_args(asset: MediaAsset, duration: float, *, still_loop: bool) -> list[str]:
    args = list(FFMPEG_NETWORK_SAFE[1:])
    if asset.kind == "image" and still_loop:
        args.append("-noautorotate")
        args.extend(["-loop", "1", "-framerate", str(PROXY_FPS), "-t", str(duration)])
    args.extend(["-i", str(asset.uri)])
    return args


def make_proxy(asset: MediaAsset, cache_dir: Path) -> Path:
    """Create a cached 720-pixel-high-or-smaller H.264/AAC proxy."""
    source = _source_path(asset)
    normalized = normalize_asset(asset)
    if normalized.width <= 0 or normalized.height <= 0:
        raise ValueError("Cannot create a video proxy from an audio-only asset")
    duration = _duration_seconds(asset, normalized)
    params = {
        "type": "proxy",
        "cache_version": CACHE_VERSION,
        "duration_ms": int(duration * 1000),
        "normalize_filter": build_normalize_filter(normalized),
        "scale": f"-2:min({PROXY_HEIGHT},ih)",
        "fps": PROXY_FPS,
        "vcodec": "libx264",
        "preset": PROXY_PRESET,
        "crf": PROXY_CRF,
        "gop": PROXY_GOP,
        "audio": ["aac", "128k", 48000, 2] if normalized.has_audio else None,
        "pixel_format": "yuv420p",
        "color": ["bt709", "bt709", "bt709", "tv"],
    }
    key = _cache_key(asset, source, params)
    out_path = Path(cache_dir) / "proxies" / f"{key[:16]}.mp4"
    if out_path.is_file():
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with _building(out_path):
        if out_path.is_file():
            return out_path
        return _build_proxy(asset, normalized, duration, params, out_path)


def _build_proxy(asset: MediaAsset, normalized, duration: float, params: dict, out_path: Path) -> Path:
    partial_path = _partial(out_path, ".mp4")

    filters = [
        params["normalize_filter"],
        f"scale=-2:min({PROXY_HEIGHT}\\,ih)",
        f"fps={PROXY_FPS}",
    ]
    cmd = [FFMPEG_EXE, "-y", *_input_args(asset, duration, still_loop=True)]
    cmd.extend([
        "-t", str(duration),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", PROXY_PRESET, "-crf", str(PROXY_CRF),
        "-g", str(PROXY_GOP), "-keyint_min", str(PROXY_GOP), "-sc_threshold", "0",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv",
    ])
    if normalized.has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"])
    cmd.extend(["-movflags", "+faststart", str(partial_path)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg proxy encoding failed: {result.stderr}")
        _check_media(partial_path, video=True, audio=normalized.has_audio)
        os.replace(partial_path, out_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        raise
    return out_path


def make_thumbnails(
    asset: MediaAsset, cache_dir: Path, count: int = 10, width: int = 320
) -> list[Path]:
    """Create cached JPEG thumbnails at evenly spaced points in the asset."""
    if count <= 0 or width <= 0:
        raise ValueError("Thumbnail count and width must be positive")
    source = _source_path(asset)
    normalized = normalize_asset(asset)
    if normalized.width <= 0 or normalized.height <= 0:
        raise ValueError("Cannot create thumbnails from an audio-only asset")
    duration = _duration_seconds(asset, normalized)
    normalize_filter = build_normalize_filter(normalized)
    params = {
        "type": "thumbnails",
        "cache_version": CACHE_VERSION,
        "duration_ms": int(duration * 1000),
        "normalize_filter": normalize_filter,
        "count": count,
        "width": width,
        "height": "-2",
        "format": "jpeg",
    }
    key = _cache_key(asset, source, params)
    thumb_dir = Path(cache_dir) / "thumbnails" / key[:16]
    thumbs = [thumb_dir / f"thumb_{index:03d}.jpg" for index in range(count)]
    if all(path.is_file() for path in thumbs):
        return thumbs
    thumb_dir.mkdir(parents=True, exist_ok=True)
    with _building(thumb_dir):
        _build_thumbnails(asset, duration, normalize_filter, width, count, thumbs)
    return thumbs


def _build_thumbnails(asset: MediaAsset, duration: float, normalize_filter: str, width: int,
                      count: int, thumbs: list[Path]) -> None:
    for index, thumb in enumerate(thumbs):
        if thumb.is_file():
            continue
        timestamp = duration * (index + 0.5) / count
        partial_path = _partial(thumb, ".jpg")
        cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:]]
        if asset.kind == "image":
            cmd.extend(["-noautorotate", "-i", str(asset.uri)])
        else:
            cmd.extend(["-ss", f"{timestamp:.6f}", "-i", str(asset.uri)])
        cmd.extend([
            "-vf", f"{normalize_filter},scale={width}:-2",
            "-frames:v", "1", "-q:v", "2", str(partial_path),
        ])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg thumbnail extraction failed: {result.stderr}")
            os.replace(partial_path, thumb)
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise


def make_audio_preview(asset: MediaAsset, cache_dir: Path) -> Path:
    """Create a deterministic cached 48 kHz stereo AAC preview."""
    source = _source_path(asset)
    normalized = normalize_asset(asset)
    if not normalized.has_audio:
        raise ValueError("Media asset has no audio stream")
    params = {
        "type": "audio_preview", "cache_version": CACHE_VERSION, "codec": "aac", "bitrate": "128k",
        "sample_rate": 48000, "channels": 2, "format": "m4a",
        "bitexact": True, "map_metadata": -1,
    }
    key = _cache_key(asset, source, params)
    out_path = Path(cache_dir) / "audio" / f"{key[:16]}.m4a"
    if out_path.is_file():
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with _building(out_path):
        if out_path.is_file():
            return out_path
        return _build_audio_preview(source, out_path)


def _build_audio_preview(source: Path, out_path: Path) -> Path:
    partial_path = _partial(out_path, ".m4a")
    cmd = [FFMPEG_EXE, "-y", "-fflags", "+bitexact", *FFMPEG_NETWORK_SAFE[1:],
           "-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "128k",
           "-ar", "48000", "-ac", "2", "-flags:a", "+bitexact", "-map_metadata", "-1",
           "-movflags", "+faststart", str(partial_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio preview encoding failed: {result.stderr}")
        _check_media(partial_path, audio=True)
        os.replace(partial_path, out_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        raise
    return out_path
