"""Offline aspect-ratio fitting and platform safe-area helpers."""

from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

from poc.ingest.normalize import (
    FFMPEG_EXE,
    FFMPEG_NETWORK_SAFE,
    _validate_no_network_paths,
    normalize_asset,
)
from poc.timeline import MediaAsset


def _validate_fit_args(src_w: int, src_h: int, width: int, height: int,
                       mode: str, focus: tuple[float, float]) -> tuple[float, float]:
    if min(src_w, src_h, width, height) <= 0:
        raise ValueError("Source and output dimensions must be positive")
    if width % 2 or height % 2:
        raise ValueError("Output dimensions must be even for yuv420p")
    if mode not in ("cover", "contain_blur", "subject_crop"):
        raise ValueError(f"Unknown fit mode: {mode}")
    if len(focus) != 2:
        raise ValueError("focus must contain normalized x and y coordinates")
    fx, fy = focus
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (fx, fy)):
        raise ValueError("focus coordinates must be between 0 and 1")
    return float(fx), float(fy)


def build_fit_filter(src_w: int, src_h: int, width: int, height: int,
                     mode: str, focus: tuple[float, float]) -> str:
    """Return a filter-complex graph for fitting one input into the frame."""
    fx, fy = _validate_fit_args(src_w, src_h, width, height, mode, focus)
    scale = (
        "scale=iw*sar:ih,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase:"
        "force_divisible_by=2"
    )
    if mode == "cover":
        return (
            f"[0:v]{scale},crop={width}:{height}:(iw-{width})/2:(ih-{height})"
            "/2[outv]"
        )
    if mode == "subject_crop":
        x = f"'min(max(iw*{fx:.12g}-{width}/2\\,0)\\,iw-{width})'"
        y = f"'min(max(ih*{fy:.12g}-{height}/2\\,0)\\,ih-{height})'"
        return (
            f"[0:v]{scale},crop={width}:{height}:{x}:{y}[outv]"
        )

    background = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:"
        f"force_divisible_by=2,crop={width}:{height},boxblur=20:2[blur]"
    )
    foreground = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2[fit]"
    )
    return (
        f"[0:v]scale=iw*sar:ih,split=2[bg][fg];[bg]{background};[fg]{foreground};"
        "[blur][fit]overlay=(W-w)/2:(H-h)/2[outv]"
    )


def fit_to_frame_to_file(
    asset: MediaAsset,
    out_path: Path,
    width: int = 1080,
    height: int = 1920,
    mode: str = "cover",
    focus: tuple[float, float] = (0.5, 0.5),
) -> Path:
    """Encode a fitted video with optional blurred bands or subject framing."""
    if asset.uri is None:
        raise ValueError("Media asset must have a local URI")
    _validate_no_network_paths(asset.uri)
    normalized = normalize_asset(asset)
    _validate_fit_args(normalized.width, normalized.height, width, height, mode, focus)
    graph = build_fit_filter(normalized.width, normalized.height, width, height, mode, focus)
    if asset.kind == "image":
        duration_ms = asset.duration_ms or 5000
        input_args = ["-loop", "1", "-framerate", "30", "-t", str(duration_ms / 1000),
                      "-i", asset.uri]
        fps = 30
    else:
        duration_ms = normalized.duration_ms
        input_args = ["-i", asset.uri]
        fps = max(1, round(normalized.fps))
    if duration_ms <= 0:
        raise ValueError("Media asset duration must be known")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = out_path.with_name(
        f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}"
    )
    cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:], *input_args]
    cmd.extend([
        "-filter_complex", graph, "-map", "[outv]", "-map", "0:a:0?",
        "-t", str(duration_ms / 1000), "-r", str(fps),
        "-aspect", f"{width}:{height}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv",
    ])
    if normalized.has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"])
    cmd.extend(["-movflags", "+faststart", str(partial_path)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg aspect fitting failed: {result.stderr}")
        os.replace(partial_path, out_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        raise
    return out_path


def safe_area_warnings(
    boxes: list[tuple[float, float, float, float]],
    width: int = 1080,
    height: int = 1920,
    profile: str = "reels_9x16",
) -> list[str]:
    """Return one warning for each box that touches a Reels unsafe zone."""
    if profile != "reels_9x16":
        raise ValueError(f"Unknown safe-area profile: {profile}")
    if width <= 0 or height <= 0:
        raise ValueError("Frame dimensions must be positive")

    warnings = []
    top_edge = height * 0.14
    bottom_edge = height * 0.80
    right_edge = width * 0.88
    for index, (x, y, box_w, box_h) in enumerate(boxes):
        if y < top_edge or y + box_h > bottom_edge or x + box_w > right_edge:
            warnings.append(
                f"Box {index} touches an unsafe zone for profile {profile}"
            )
    return warnings
