"""Integration tests for poc.execute.render (Step 4), section B/C.

These generate small synthetic media with FFmpeg's built-in ``lavfi`` test
sources (no external/copyrighted media needed, no network access), run the
real render pipeline end to end, and probe the actual output file. Slower
than the unit tests, but they are the only tests that prove FFmpeg is
actually installed correctly, the three invocations chain together, and the
result is a genuinely playable file with both a video and an audio stream.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from poc.execute import probe, render, render_from_file
from poc.timeline import Clip, Music, Settings, Source, Timeline

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
HAND_TIMELINE = EXAMPLES_DIR / "hand_timeline.json"

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


def _make_synthetic_video(path: Path, duration_s: float, fps: int = 30, size: str = "320x240") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG_EXE, "-y",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}",
            "-t", str(duration_s),
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _make_synthetic_mp3(path: Path, duration_s: float, freq: int = 440) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG_EXE, "-y",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration_s}",
            "-c:a", "libmp3lame",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


# ---------------------------------------------------------------------------
# The hand-written fixture as the first real contract test.
# ---------------------------------------------------------------------------

def test_render_hand_timeline_end_to_end(tmp_path: Path):
    # Lay the fixture's own JSON down in a project directory alongside
    # synthetic media at exactly the relative paths it references, so this
    # exercises the real path-resolution contract, not a rewritten one.
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copy(HAND_TIMELINE, project_dir / "hand_timeline.json")

    # Durations sized comfortably above what each clip actually consumes
    # (computed from the fixture: see tests/test_timeline_validate.py's
    # matching SOURCE_RANGE test for the exact numbers).
    _make_synthetic_video(project_dir / "footage" / "A.mp4", duration_s=11.0)
    _make_synthetic_video(project_dir / "footage" / "B.mp4", duration_s=3.0)
    _make_synthetic_video(project_dir / "footage" / "C.mp4", duration_s=6.5)
    _make_synthetic_video(project_dir / "footage" / "D.mp4", duration_s=2.5)
    _make_synthetic_mp3(project_dir / "music.mp3", duration_s=9.0)

    out_path = project_dir / "out" / "out.mp4"
    result = render_from_file(project_dir / "hand_timeline.json", out_path)

    assert result.out_path == out_path
    assert out_path.exists()
    assert result.duration_frames == 240
    assert result.clip_count == 5

    media = probe(out_path, FFMPEG_EXE)
    assert media.has_video
    assert media.has_audio
    assert media.fps == pytest.approx(30.0, abs=0.1)
    assert media.duration_ms == pytest.approx(8000, abs=100)  # 240 frames at 30 fps

    n_frames, decoded_secs = imageio_ffmpeg.count_frames_and_secs(str(out_path))
    assert n_frames == 240
    assert decoded_secs == pytest.approx(8.0, abs=0.1)


# ---------------------------------------------------------------------------
# A minimal from-scratch timeline through the render() entry point directly
# (not render_from_file), at a smaller resolution for speed.
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_project(tmp_path: Path) -> tuple[Timeline, Path]:
    _make_synthetic_video(tmp_path / "a.mp4", duration_s=3.0, size="320x240")
    _make_synthetic_video(tmp_path / "b.mp4", duration_s=3.0, size="320x240")
    _make_synthetic_mp3(tmp_path / "music.mp3", duration_s=6.0)

    tl = Timeline(
        settings=Settings(fps=30, width=320, height=240),
        duration_frames=60,  # 2 seconds
        music=Music(path="music.mp3", in_ms=0, gain_db=-1.0, fade_out_ms=1000),
        beats=[{"frame": 0, "downbeat": True, "energy": 0.5}, {"frame": 30, "downbeat": False, "energy": 0.5}],
        clips=[
            Clip(id="c1", start_frame=0, end_frame=30, source=Source(path="a.mp4", in_ms=0)),
            Clip(id="c2", start_frame=30, end_frame=60, source=Source(path="b.mp4", in_ms=500)),
        ],
    )
    return tl, tmp_path


def test_render_minimal_timeline_end_to_end(minimal_project: tuple[Timeline, Path]):
    tl, base_dir = minimal_project
    out_path = base_dir / "out.mp4"

    result = render(tl, base_dir, out_path)

    assert out_path.exists()
    assert result.duration_frames == 60
    assert result.clip_count == 2

    media = probe(out_path, FFMPEG_EXE)
    assert media.has_video
    assert media.has_audio
    assert media.fps == pytest.approx(30.0, abs=0.1)
    assert media.duration_ms == pytest.approx(2000, abs=100)

    n_frames, _ = imageio_ffmpeg.count_frames_and_secs(str(out_path))
    assert n_frames == 60


def test_render_creates_missing_output_directory(minimal_project: tuple[Timeline, Path]):
    tl, base_dir = minimal_project
    out_path = base_dir / "nested" / "dir" / "out.mp4"

    assert not out_path.parent.exists()
    render(tl, base_dir, out_path)
    assert out_path.exists()
