"""Offline, frame-centered video transitions with source-handle validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from poc.ingest.normalize import (
    FFMPEG_EXE,
    FFMPEG_NETWORK_SAFE,
    build_normalize_filter,
    normalize_asset,
)
from poc.render.aspect import build_fit_filter
from poc.timeline import MediaAsset
from poc.render._process import run_ffmpeg_atomic


# join kind -> FFmpeg xfade transition (frame-centred overlap of the two clips)
XFADE = {
    "crossfade": "fade", "wipe_left": "wipeleft", "slide_left": "slideleft",
    "wipe_right": "wiperight", "wipe_up": "wipeup", "wipe_down": "wipedown",
    "slide_right": "slideright", "slide_up": "slideup", "slide_down": "slidedown",
    "dissolve": "dissolve", "fade_white": "fadewhite", "zoom_through": "zoomin", "blur_through": "hblur",
    "circle_open": "circleopen", "circle_close": "circleclose", "radial": "radial", "pixelize": "pixelize",
    "smooth_left": "smoothleft", "smooth_right": "smoothright", "squeeze": "squeezeh", "slice": "hlslice",
    "wind": "hlwind", "diagonal": "diagtl", "fade_mono": "fadegrays",
}
_KINDS = {"cut", "fade_black", *XFADE}


@dataclass(frozen=True)
class Segment:
    path: Path
    src_in_ms: int
    src_out_ms: int
    vfilter: str = ""


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


PREROLL_S = 1.0     # how far before a cut frames are read, to bridge gaps in variable-frame-rate video


def _preroll_frames(start_ms: float, fps: int) -> int:
    """Whole output frames read before the cut (up to PREROLL_S, never before the file start)."""
    return max(0, min(round(PREROLL_S * fps), int(start_ms * fps / 1000 + 1e-6)))


def build_sequence_graph(segments, joins, *, width: int, height: int, fps: int,
                         input_seeking: bool = False,
                         final_filter: str | None = None) -> str:
    """Create an FFmpeg filter graph for frame-centered joins."""
    media, base_frames, join_frames = _validate(segments, joins, width, height, fps)
    graph = []
    for index, (segment, (asset, normalized)) in enumerate(zip(segments, media)):
        left_h = join_frames[index - 1] // 2 if index > 0 else 0
        right_h = join_frames[index] // 2 if index < len(joins) else 0
        clip_frames = left_h + base_frames[index] + right_h
        source_start_ms = segment.src_in_ms - left_h * 1000 / fps
        # Phone videos have variable frame rates and can skip frames for half a second or more.
        # A short cut inside such a gap would decode no frames at all (and an empty segment ends
        # every transition after it). So frames are put on the output grid first, from a little
        # before the cut (the last real frame fills the gap), and only then is the cut taken.
        if input_seeking:
            first = _preroll_frames(source_start_ms, fps)          # the input starts this much early
        else:
            first = round(source_start_ms * fps / 1000)
        norm = build_normalize_filter(normalized)
        fit = build_fit_filter(
            normalized.width, normalized.height, width, height, "cover", (0.5, 0.5)
        )
        fit = fit.replace("[0:v]", f"[norm{index}]", 1).replace("[outv]", f"[fit{index}]")
        graph.append(
            f"[{index}:v]fps={fps}:start_time=0,trim=start_frame={first}:end_frame={first + clip_frames},"
            f"setpts=PTS-STARTPTS,{norm}[norm{index}];{fit};[fit{index}]fps={fps},"
            f"{segment.vfilter + ',' if segment.vfilter else ''}format=yuv420p,"
            f"setsar=1,settb=1/{fps},tpad=stop_mode=clone:stop_duration=1,"
            f"trim=end_frame={clip_frames}[v{index}]"
        )

    current = "v0"
    elapsed_frames = base_frames[0]
    for index, join in enumerate(joins):
        next_label = f"v{index + 1}"
        out_label = f"seq{index}"
        if join.kind == "cut":
            graph.append(f"[{current}][{next_label}]concat=n=2:v=1:a=0,settb=1/{fps}[{out_label}]")
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
                f"[fadeout{index}][fadein{index}]concat=n=2:v=1:a=0,settb=1/{fps}[{out_label}]"
            )
        else:
            frames = join_frames[index]
            half = frames // 2
            offset_frames = elapsed_frames - half
            duration = _seconds(frames / fps)
            offset = _seconds(offset_frames / fps)
            transition = XFADE[join.kind]
            opts = ""
            graph.append(
                f"[{current}][{next_label}]xfade=transition={transition}:"
                f"duration={duration}:offset={offset}{opts}[{out_label}]"
            )
        current = out_label
        elapsed_frames += base_frames[index + 1]
    final_chain = f"{final_filter}," if final_filter else ""
    graph.append(
        f"[{current}]{final_chain}setparams=range=limited:color_primaries=bt709:"
        "color_trc=bt709:colorspace=bt709[outv]"
    )
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
    crf: int = 23,
    preset: str = "medium",
    audio_bitrate: str = "192k",
    input_seeking: bool = False,
    final_filter: str | None = None,
    cwd: Path | None = None,
    cancel: Callable[[], bool] | None = None,
    max_kbps: int | None = None,
) -> Path:
    """Render a deterministic silent H.264 sequence with atomic replacement."""
    graph = build_sequence_graph(segments, joins, width=width, height=height, fps=fps,
                                 input_seeking=input_seeking, final_filter=final_filter)
    media, base_frames, join_frames = _validate(segments, joins, width, height, fps)
    total_frames = sum(base_frames)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}")
    cmd = [FFMPEG_EXE, "-y", "-fflags", "+bitexact", *FFMPEG_NETWORK_SAFE[1:]]
    for index, segment in enumerate(segments):
        if input_seeking:
            left_h = join_frames[index - 1] // 2 if index > 0 else 0
            right_h = join_frames[index] // 2 if index < len(joins) else 0
            start_ms = segment.src_in_ms - left_h * 1000 / fps
            end_ms = segment.src_out_ms + right_h * 1000 / fps
            early_ms = _preroll_frames(start_ms, fps) * 1000 / fps
            # read from a little earlier and a little longer (see build_sequence_graph)
            cmd.extend(["-ss", _seconds(max(0.0, start_ms - early_ms) / 1000),
                        "-t", _seconds((end_ms - start_ms + early_ms) / 1000 + PREROLL_S)])
        cmd.extend(["-i", str(segment.path)])
    if audio_path is not None:
        cmd.extend(["-i", str(audio_path)])
    audio_args = ["-an"] if audio_path is None else [
        "-map", f"{len(segments)}:a:0", "-c:a", "aac", "-b:a", audio_bitrate,
        "-ar", "48000", "-ac", "2",
    ]
    cmd.extend([
        "-filter_complex", graph, "-map", "[outv]", *audio_args,
        "-frames:v", str(total_frames), "-r", str(fps),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        *(["-maxrate", f"{max_kbps}k", "-bufsize", f"{2 * max_kbps}k"] if max_kbps else []),
        "-flags:v", "+bitexact", "-map_metadata", "-1",
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv",
        "-movflags", "+faststart",
    ])
    if audio_path is not None:
        cmd.extend(["-flags:a", "+bitexact"])
    cmd.append(str(partial))
    return run_ffmpeg_atomic(cmd, out_path, cancel=cancel, label="transition render", cwd=cwd)
