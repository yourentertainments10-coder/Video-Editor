"""Cached low-resolution proxies and timeline thumbnails."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
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


@lru_cache(maxsize=1)
def _ffmpeg_version() -> str:
    result = subprocess.run(
        [FFMPEG_EXE, "-version"], capture_output=True, text=True, check=True
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
        asset.duration_ms or normalized.duration_ms
        if asset.kind == "image"
        else normalized.duration_ms
    )
    if not duration_ms:
        raise ValueError("Media asset duration is required to make a proxy or thumbnails")
    return duration_ms / 1000.0


def _input_args(asset: MediaAsset, duration: float, *, still_loop: bool) -> list[str]:
    args = list(FFMPEG_NETWORK_SAFE[1:])
    if asset.kind == "image" and still_loop:
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
        "duration_ms": int(duration * 1000),
        "normalize_filter": build_normalize_filter(normalized),
        "scale": f"-2:min({PROXY_HEIGHT},ih)",
        "fps": PROXY_FPS,
        "vcodec": "libx264",
        "preset": PROXY_PRESET,
        "crf": PROXY_CRF,
        "audio": ["aac", "128k", 48000, 2] if normalized.has_audio else None,
        "pixel_format": "yuv420p",
        "color": ["bt709", "bt709", "bt709", "tv"],
    }
    key = _cache_key(asset, source, params)
    out_path = Path(cache_dir) / "proxies" / f"{key[:16]}.mp4"
    if out_path.is_file():
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = out_path.with_suffix(".partial.mp4")

    filters = [params["normalize_filter"], r"scale=-2:min(720\,ih)", "fps=30"]
    cmd = [FFMPEG_EXE, "-y", *_input_args(asset, duration, still_loop=True)]
    cmd.extend([
        "-t", str(duration),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", PROXY_PRESET, "-crf", str(PROXY_CRF),
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

    for index, thumb in enumerate(thumbs):
        if thumb.is_file():
            continue
        timestamp = duration * (index + 0.5) / count
        partial_path = thumb.with_suffix(".partial.jpg")
        cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:]]
        if asset.kind == "image":
            cmd.extend(["-i", str(asset.uri)])
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
    return thumbs
