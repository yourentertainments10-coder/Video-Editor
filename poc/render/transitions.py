"""Offline, frame-centered video transitions with source-handle validation."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from poc.ingest.normalize import (
    FFMPEG_EXE,
    FFMPEG_NETWORK_SAFE,
    build_normalize_filter,
    normalize_asset,
)
from poc.render.aspect import build_fit_filter
from poc.timeline import MediaAsset


_KINDS = {"cut", "crossfade", "fade_black", "wipe_left", "slide_left"}


@dataclass(frozen=True)
class Segment:
    path: Path
    src_in_ms: int
    src_out_ms: int


@dataclass(frozen=True)
class Join:
    kind: str
    duration_ms: int


def _seconds(value: float) -> str:
    return f"{value:.12g}"


def _validate(segments, joins, width: int, height: int, fps: int):
    if len(joins) != len(segments) - 1:
        raise ValueError("joins must contain exactly one fewer item than segments")
    if not segments:
        raise ValueError("at least one segment is required")
    if min(width, height, fps) <= 0 or width % 2 or height % 2:
        raise ValueError("width, height, and fps must be positive; dimensions must be even")

    media = []
    base_frames = []
    for segment in segments:
        if not isinstance(segment.path, Path):
            segment_path = Path(segment.path)
        else:
            segment_path = segment.path
        if segment.src_in_ms < 0 or segment.src_out_ms <= segment.src_in_ms:
            raise ValueError(f"invalid source range for clip {segment_path}")
        asset = MediaAsset(
            asset_id=f"transition-{len(media)}", kind="video",
            uri=str(segment_path), duration_ms=segment.src_out_ms - segment.src_in_ms,
        )
        normalized = normalize_asset(asset)
        if normalized.duration_ms <= 0:
            raise ValueError(f"unknown source duration for clip {segment_path}")
        if segment.src_out_ms > normalized.duration_ms:
            raise ValueError(f"source range exceeds clip {segment_path}")
        media.append((asset, normalized))
        base_frames.append(round((segment.src_out_ms - segment.src_in_ms) * fps / 1000))

    join_frames = []
    for index, join in enumerate(joins):
        if join.kind not in _KINDS:
            raise ValueError(f"unknown transition kind: {join.kind}")
        if join.duration_ms < 0:
            raise ValueError("transition duration must be nonnegative")
        if join.kind == "cut":
            if join.duration_ms != 0:
                raise ValueError("cut duration must be zero")
            join_frames.append(0)
            continue
        if join.duration_ms == 0:
            raise ValueError(f"{join.kind} duration must be positive")
        shorter_ms = min(
            segments[index].src_out_ms - segments[index].src_in_ms,
            segments[index + 1].src_out_ms - segments[index + 1].src_in_ms,
        )
        if join.duration_ms > shorter_ms / 2:
            raise ValueError("transition duration exceeds half the shorter adjacent clip")
        frames = 2 * round(join.duration_ms * fps / 2000)
        if frames <= 0:
            raise ValueError("transition rounds to zero frames")
        join_frames.append(frames)

    # Validate left handles against source start and right handles against file end.
    for index, segment in enumerate(segments):
        left_h = (join_frames[index - 1] // 2) if index > 0 else 0
        right_h = (join_frames[index] // 2) if index < len(joins) else 0
        if left_h and segment.src_in_ms - left_h * 1000 / fps < 0:
            raise ValueError(f"clip {segment.path} lacks the left transition handle")
        if right_h and segment.src_out_ms + right_h * 1000 / fps > media[index][1].duration_ms:
            raise ValueError(f"clip {segment.path} lacks the right transition handle")
    return media, base_frames, join_frames


def build_sequence_graph(segments, joins, *, width: int, height: int, fps: int) -> str:
    """Create an FFmpeg filter graph for frame-centered joins."""
    media, base_frames, join_frames = _validate(segments, joins, width, height, fps)
    graph = []
    for index, (segment, (asset, normalized)) in enumerate(zip(segments, media)):
        left_h = join_frames[index - 1] // 2 if index > 0 else 0
        right_h = join_frames[index] // 2 if index < len(joins) else 0
        clip_frames = left_h + base_frames[index] + right_h
        source_start_ms = segment.src_in_ms - left_h * 1000 / fps
        source_end_ms = segment.src_out_ms + right_h * 1000 / fps
        norm = build_normalize_filter(normalized)
        fit = build_fit_filter(
            normalized.width, normalized.height, width, height, "cover", (0.5, 0.5)
        )
        fit = fit.replace("[0:v]", f"[norm{index}]", 1).replace("[outv]", f"[fit{index}]")
        graph.append(
            f"[{index}:v]trim=start={_seconds(source_start_ms / 1000)}:"
            f"end={_seconds(source_end_ms / 1000)},setpts=PTS-STARTPTS,{norm}"
            f"[norm{index}];{fit};[fit{index}]fps={fps},format=yuv420p,"
            f"setsar=1,settb=1/{fps},tpad=stop_mode=clone:stop_duration=1,"
            f"trim=end_frame={clip_frames}[v{index}]"
        )

    current = "v0"
    elapsed_frames = base_frames[0]
    for index, join in enumerate(joins):
        next_label = f"v{index + 1}"
        out_label = f"seq{index}"
        if join.kind == "cut":
            graph.append(f"[{current}][{next_label}]concat=n=2:v=1:a=0[{out_label}]")
        elif join.kind == "fade_black":
            half = join_frames[index] // 2
            fade_start = _seconds((elapsed_frames - half) / fps)
            fade_duration = _seconds(half / fps)
            graph.append(
                f"[{current}]trim=end_frame={elapsed_frames},"
                f"fade=t=out:st={fade_start}:d={fade_duration},"
                f"setpts=PTS-STARTPTS[fadeout{index}];"
                f"[{next_label}]trim=start_frame={half},setpts=PTS-STARTPTS,"
                f"fade=t=in:st=0:d={fade_duration}[fadein{index}];"
                f"[fadeout{index}][fadein{index}]concat=n=2:v=1:a=0[{out_label}]"
            )
        else:
            frames = join_frames[index]
            half = frames // 2
            offset_frames = elapsed_frames - half
            duration = _seconds(frames / fps)
            offset = _seconds(offset_frames / fps)
            if join.kind == "crossfade":
                transition = "fade"
                opts = ""
            elif join.kind == "wipe_left":
                transition = "wipeleft"
                opts = ""
            elif join.kind == "slide_left":
                transition = "slideleft"
                opts = ""
            graph.append(
                f"[{current}][{next_label}]xfade=transition={transition}:"
                f"duration={duration}:offset={offset}{opts}[{out_label}]"
            )
        current = out_label
        elapsed_frames += base_frames[index + 1]
    graph.append(f"[{current}]null[outv]")
    return ";".join(graph)


def render_sequence_to_file(
    segments,
    joins,
    out_path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    audio_path: Path | None = None,
) -> Path:
    """Render a deterministic silent H.264 sequence with atomic replacement."""
    graph = build_sequence_graph(segments, joins, width=width, height=height, fps=fps)
    _, base_frames, _ = _validate(segments, joins, width, height, fps)
    total_frames = sum(base_frames)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}")
    cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:]]
    for segment in segments:
        cmd.extend(["-i", str(segment.path)])
    if audio_path is not None:
        cmd.extend(["-i", str(audio_path)])
    audio_args = ["-an"] if audio_path is None else [
        "-map", f"{len(segments)}:a:0", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2",
    ]
    cmd.extend([
        "-filter_complex", graph, "-map", "[outv]", *audio_args,
        "-frames:v", str(total_frames), "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv",
        "-movflags", "+faststart", str(partial),
    ])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"FFmpeg transition render failed: {result.stderr}")
        os.replace(partial, out_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return out_path
