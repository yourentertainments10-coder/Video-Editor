"""poc/execute/render.py: Timeline Document -> playable MP4.

The whole job of this module is docs/MVP.md Section 11's "renderer notes":
three FFmpeg invocations, no filter graphs across clips.

  1. One invocation per clip: trim the source at its in-point, normalize to
     the timeline's fixed fps/resolution, encode to an intermediate H.264
     segment with no audio.
  2. One invocation to concatenate all segments in timeline order via the
     concat demuxer with ``-c copy`` (exact, because every segment already
     shares the same codec/fps/resolution -- no re-encode, no drift).
  3. One invocation to mux the trimmed, faded music track onto the
     concatenated video and produce the final ``+faststart`` MP4.

This is deliberately the smallest renderer that satisfies docs/MVP.md: no
transitions, no speed ramps, no effects, no captions, no per-segment cache,
no parallel rendering, no GPU. FFmpeg comes from ``imageio-ffmpeg``'s bundled
static binary, so no system-wide install is required -- but that package
bundles only ``ffmpeg``, not ``ffprobe``, so media probing here runs
``ffmpeg -i <file>`` with no output and parses its stderr banner for
Duration/Video/Audio lines, the standard fallback when ffprobe is absent.

Path contract (agreed after Step 3): Timeline documents store media paths as
relative, POSIX-style strings and never absolute, machine-specific paths.
This module is where those strings become real filesystem paths -- resolved
against the Timeline JSON file's parent directory. Neither the schema
(Step 2) nor the validator (Step 3) does any path resolution; this is where
that contract is actually implemented and enforced.

The Timeline is always validated -- with real, probed media durations
whenever real media is available -- before any FFmpeg process is started.
A timeline with any violation raises ``TimelineInvalidError`` and touches
FFmpeg not at all.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import imageio_ffmpeg

from poc.timeline import Clip, Music, Settings, Timeline, Violation, frames_to_seconds, validate

__all__ = [
    "RenderResult",
    "MediaProbe",
    "TimelineInvalidError",
    "FFmpegError",
    "MediaProbeError",
    "resolve_media_path",
    "probe",
    "probe_duration_ms",
    "render",
    "render_from_file",
]


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class TimelineInvalidError(Exception):
    """Raised instead of rendering when ``validate()`` returns violations.

    Carries the full, unfiltered list so a caller can report every problem
    at once rather than fixing one and re-running to find the next.
    """

    def __init__(self, violations: list[Violation]):
        self.violations = violations
        lines = "\n".join(f"  {v}" for v in violations)
        super().__init__(f"timeline failed validation ({len(violations)} violation(s)):\n{lines}")


class FFmpegError(RuntimeError):
    """An FFmpeg subprocess exited non-zero during segment/concat/mux."""

    def __init__(self, args: list[str], returncode: int, output: str):
        self.args_ = args
        self.returncode = returncode
        self.output = output
        super().__init__(f"ffmpeg exited {returncode}: {' '.join(args)}\n{output}")


class MediaProbeError(RuntimeError):
    """FFmpeg produced no parseable Duration line for a media file."""

    def __init__(self, path: Path, output: str):
        self.path = path
        self.output = output
        super().__init__(f"could not determine duration of {path} from ffmpeg output:\n{output}")


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------


def resolve_media_path(rel_path: str, base_dir: Path) -> Path:
    """Resolve a Timeline-stored relative POSIX path against ``base_dir``.

    ``base_dir`` is the Timeline JSON file's parent directory, per the
    path-resolution contract agreed after Step 3. Rejects absolute paths
    (POSIX ``/...`` or a Windows drive letter) since a Timeline document must
    never store a machine-specific absolute path.
    """
    posix = PurePosixPath(rel_path)
    if posix.is_absolute() or re.match(r"^[A-Za-z]:", rel_path):
        raise ValueError(
            f"media path {rel_path!r} must be relative; Timeline documents must not "
            "store absolute, machine-specific paths"
        )
    return (Path(base_dir) / rel_path).resolve()


# ---------------------------------------------------------------------------
# media probing (no ffprobe available -- parse `ffmpeg -i` stderr)
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_FPS_RE = re.compile(r"([\d.]+)\s*fps")
_VIDEO_STREAM_RE = re.compile(r"Stream #\d+:\d+[^\n]*:\s*Video:")
_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+[^\n]*:\s*Audio:")


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int
    fps: float | None
    has_video: bool
    has_audio: bool


def probe(path: Path, ffmpeg_exe: str) -> MediaProbe:
    """Probe one media file by parsing ``ffmpeg -i <path>`` output.

    ``ffmpeg -i`` with no output file always exits non-zero (it is not given
    anything to encode); that is expected and the exit code is ignored here
    -- only the informational banner on stderr is read.
    """
    proc = subprocess.run(
        [ffmpeg_exe, "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    text = proc.stdout or ""
    match = _DURATION_RE.search(text)
    if not match:
        raise MediaProbeError(path, text)
    hours, minutes, seconds = match.groups()
    total_s = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    fps_match = _FPS_RE.search(text)
    return MediaProbe(
        duration_ms=int(round(total_s * 1000)),
        fps=float(fps_match.group(1)) if fps_match else None,
        has_video=bool(_VIDEO_STREAM_RE.search(text)),
        has_audio=bool(_AUDIO_STREAM_RE.search(text)),
    )


def probe_duration_ms(path: Path, ffmpeg_exe: str) -> int:
    return probe(path, ffmpeg_exe).duration_ms


# ---------------------------------------------------------------------------
# ffmpeg command construction (pure -- no subprocess, so these are directly
# unit-testable without a real ffmpeg binary or media files)
# ---------------------------------------------------------------------------


def _seconds_arg(frames: int, fps: int) -> str:
    """Format a frame count as a fixed-precision seconds string for FFmpeg's
    ``-t``/``-ss``, which take seconds, not frames."""
    return f"{frames_to_seconds(frames, fps):.6f}"


def _segment_command(
    ffmpeg_exe: str, source_path: Path, clip: Clip, settings: Settings, seg_path: Path
) -> list[str]:
    in_s = f"{clip.source.in_ms / 1000.0:.6f}"
    duration_s = _seconds_arg(clip.frames, settings.fps)
    vf = (
        f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase,"
        f"crop={settings.width}:{settings.height},setsar=1"
    )
    return [
        ffmpeg_exe,
        "-y",
        "-ss", in_s,
        "-i", str(source_path),
        "-t", duration_s,
        "-r", str(settings.fps),
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(seg_path),
    ]


def _concat_command(ffmpeg_exe: str, list_file: Path, out_path: Path) -> list[str]:
    return [
        ffmpeg_exe,
        "-y",
        "-f", "concat",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]


def _mux_command(
    ffmpeg_exe: str,
    video_path: Path,
    music_path: Path,
    music: Music,
    duration_ms: int,
    out_path: Path,
) -> list[str]:
    duration_s = duration_ms / 1000.0
    music_in_s = f"{music.in_ms / 1000.0:.6f}"
    fade_out_s = music.fade_out_ms / 1000.0
    fade_start_s = max(0.0, duration_s - fade_out_s)
    af = f"volume={music.gain_db}dB,afade=t=out:st={fade_start_s:.6f}:d={fade_out_s:.6f}"
    return [
        ffmpeg_exe,
        "-y",
        "-i", str(video_path),
        "-ss", music_in_s,
        "-i", str(music_path),
        "-t", f"{duration_s:.6f}",
        # Explicit stream mapping (a disclosed addition beyond the MVP.md
        # reference command): the video input has no audio (segments were
        # encoded with -an) and the music input has no video, so default
        # stream selection would already do the right thing, but naming the
        # mapping removes any ambiguity about which stream feeds which.
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-af", af,
        "-c:v", "copy",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out_path),
    ]


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise FFmpegError(args, proc.returncode, proc.stdout or "")


# ---------------------------------------------------------------------------
# render pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderResult:
    out_path: Path
    duration_frames: int
    duration_ms: int
    clip_count: int


def render(
    tl: Timeline,
    base_dir: Path,
    out_path: Path,
    *,
    ffmpeg_exe: str | None = None,
) -> RenderResult:
    """Validate ``tl`` against real media, then render it to ``out_path``.

    ``base_dir`` is the directory every relative path in ``tl`` is resolved
    against (the Timeline JSON file's parent directory, per contract).
    Raises ``TimelineInvalidError`` -- without invoking FFmpeg for
    rendering -- if the timeline does not validate against the real,
    probed durations of its referenced media.
    """
    ffmpeg_exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()
    base_dir = Path(base_dir)
    out_path = Path(out_path)

    resolved_sources = {
        source_path: resolve_media_path(source_path, base_dir)
        for source_path in {c.source.path for c in tl.clips}
    }
    resolved_music = resolve_media_path(tl.music.path, base_dir)

    source_durations_ms = {
        source_path: probe_duration_ms(resolved, ffmpeg_exe)
        for source_path, resolved in resolved_sources.items()
    }
    music_duration_ms = probe_duration_ms(resolved_music, ffmpeg_exe)

    violations = validate(tl, source_durations_ms=source_durations_ms, music_duration_ms=music_duration_ms)
    if violations:
        raise TimelineInvalidError(violations)

    with tempfile.TemporaryDirectory(prefix="poc_render_") as tmp:
        tmp_dir = Path(tmp)
        segment_paths: list[Path] = []
        for i, clip in enumerate(tl.sorted_clips()):
            seg_path = tmp_dir / f"seg_{i:03d}.mp4"
            _run_ffmpeg(
                _segment_command(ffmpeg_exe, resolved_sources[clip.source.path], clip, tl.settings, seg_path)
            )
            segment_paths.append(seg_path)

        # Concat demuxer entries are resolved relative to the list file's
        # own directory regardless of process cwd, so bare filenames here
        # (list file lives alongside the segments) avoid needing -safe 0.
        list_file = tmp_dir / "concat_list.txt"
        list_file.write_text("".join(f"file '{p.name}'\n" for p in segment_paths), encoding="utf-8")

        concat_video = tmp_dir / "concat_video.mp4"
        _run_ffmpeg(_concat_command(ffmpeg_exe, list_file, concat_video))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(_mux_command(ffmpeg_exe, concat_video, resolved_music, tl.music, tl.duration_ms, out_path))

    return RenderResult(
        out_path=out_path,
        duration_frames=tl.duration_frames,
        duration_ms=tl.duration_ms,
        clip_count=len(tl.clips),
    )


def render_from_file(
    timeline_path: Path, out_path: Path, *, ffmpeg_exe: str | None = None
) -> RenderResult:
    """Convenience wrapper: load a Timeline JSON file and render it.

    ``base_dir`` for path resolution is taken from ``timeline_path``'s
    parent directory, matching the agreed contract exactly.
    """
    timeline_path = Path(timeline_path)
    tl = Timeline.load(timeline_path)
    return render(tl, timeline_path.parent, out_path, ffmpeg_exe=ffmpeg_exe)
