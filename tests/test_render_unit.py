"""Focused unit tests for poc.execute.render (Step 4), section A.

Nothing here invokes a real FFmpeg subprocess or touches real media files:
path resolution, frame-to-duration formatting, and FFmpeg command
construction are pure functions and tested as such. The
"invalid timeline stops before rendering" requirement is tested here too,
by mocking the FFmpeg-invoking helper and the media prober so the whole
test runs in milliseconds while still proving no FFmpeg process would have
been started for encoding, concatenation, or muxing.

Real end-to-end rendering with real (synthetic) media lives in
tests/test_render_integration.py.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from importlib import import_module

from poc.execute import TimelineInvalidError, resolve_media_path

# poc/execute/__init__.py does `from poc.execute.render import render`, which
# rebinds the package attribute `poc.execute.render` to the *function*,
# shadowing the submodule of the same name (a standard Python gotcha, not a
# bug in the library). `import_module` resolves via sys.modules and is
# unaffected by that shadowing, so it reliably gets the actual module object
# whose globals `_run_ffmpeg`/`probe_duration_ms` need to be patched in.
render_mod = import_module("poc.execute.render")
from poc.execute.render import _concat_command, _mux_command, _seconds_arg, _segment_command
from poc.timeline import Clip, Music, Settings, Source, Timeline


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------

def test_resolve_media_path_joins_relative_path_to_base_dir(tmp_path: Path):
    resolved = resolve_media_path("footage/A.mp4", tmp_path)
    assert resolved == (tmp_path / "footage" / "A.mp4").resolve()


def test_resolve_media_path_handles_nested_relative_paths(tmp_path: Path):
    resolved = resolve_media_path("a/b/c/clip.mp4", tmp_path)
    assert resolved == (tmp_path / "a" / "b" / "c" / "clip.mp4").resolve()


def test_resolve_media_path_rejects_posix_absolute_path(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_media_path("/etc/passwd", tmp_path)


def test_resolve_media_path_rejects_windows_drive_absolute_path(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_media_path("C:/footage/A.mp4", tmp_path)


# ---------------------------------------------------------------------------
# frame -> duration (seconds) formatting for FFmpeg -t/-ss arguments
# ---------------------------------------------------------------------------

def test_seconds_arg_exact_second_at_30fps():
    assert _seconds_arg(30, 30) == "1.000000"


def test_seconds_arg_half_second_at_30fps():
    assert _seconds_arg(15, 30) == "0.500000"


def test_seconds_arg_zero_frames():
    assert _seconds_arg(0, 30) == "0.000000"


def test_seconds_arg_respects_custom_fps():
    assert _seconds_arg(60, 60) == "1.000000"


# ---------------------------------------------------------------------------
# FFmpeg command construction (pure functions, no subprocess)
# ---------------------------------------------------------------------------

def _sample_clip() -> Clip:
    return Clip(id="c001", start_frame=0, end_frame=30, source=Source(path="footage/A.mp4", in_ms=2000))


def test_segment_command_has_expected_shape():
    settings = Settings(fps=30, width=1920, height=1080)
    cmd = _segment_command("ffmpeg", Path("/media/footage/A.mp4"), _sample_clip(), settings, Path("/tmp/seg_000.mp4"))

    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == str(Path("/media/footage/A.mp4"))
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "2.000000"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "1.000000"  # 30 frames at 30 fps
    assert "-r" in cmd and cmd[cmd.index("-r") + 1] == "30"
    assert "-an" in cmd
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1920:1080" in vf
    assert "crop=1920:1080" in vf
    assert cmd[-1] == str(Path("/tmp/seg_000.mp4"))


def test_segment_command_uses_timeline_resolution_not_a_hardcoded_one():
    settings = Settings(fps=30, width=640, height=360)
    cmd = _segment_command("ffmpeg", Path("a.mp4"), _sample_clip(), settings, Path("seg.mp4"))
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=640:360" in vf
    assert "crop=640:360" in vf


def test_concat_command_has_expected_shape():
    cmd = _concat_command("ffmpeg", Path("/tmp/list.txt"), Path("/tmp/concat.mp4"))
    assert cmd == ["ffmpeg", "-y", "-f", "concat", "-i", str(Path("/tmp/list.txt")), "-c", "copy", str(Path("/tmp/concat.mp4"))]


def test_mux_command_has_expected_shape():
    music = Music(path="music.mp3", in_ms=1000, gain_db=-1.0, fade_out_ms=1000)
    cmd = _mux_command("ffmpeg", Path("video.mp4"), Path("music.mp3"), music, duration_ms=8000, out_path=Path("out.mp4"))

    assert "-i" in cmd
    assert cmd[cmd.index("-ss") + 1] == "1.000000"  # music.in_ms
    assert cmd[cmd.index("-t") + 1] == "8.000000"  # duration_ms
    assert "-map" in cmd
    assert cmd.count("-map") == 2
    assert "0:v:0" in cmd
    assert "1:a:0" in cmd
    af = cmd[cmd.index("-af") + 1]
    assert "volume=-1.0dB" in af
    assert "afade=t=out:st=7.000000:d=1.000000" in af  # fade starts at 8s - 1s
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "+faststart" in cmd
    assert cmd[-1] == str(Path("out.mp4"))


def test_mux_command_clamps_fade_start_when_fade_longer_than_duration():
    music = Music(path="music.mp3", in_ms=0, gain_db=-1.0, fade_out_ms=5000)
    cmd = _mux_command("ffmpeg", Path("video.mp4"), Path("music.mp3"), music, duration_ms=2000, out_path=Path("out.mp4"))
    af = cmd[cmd.index("-af") + 1]
    assert "st=0.000000" in af  # clamped to 0, never negative


# ---------------------------------------------------------------------------
# invalid timeline stops before any FFmpeg encode/concat/mux call
# ---------------------------------------------------------------------------

def _gappy_timeline() -> Timeline:
    # c2 starts at 20, c1 ends at 10: a CONTIGUOUS gap. Deliberately invalid.
    return Timeline(
        duration_frames=30,
        music=Music(path="music.mp3"),
        clips=[
            Clip(id="c1", start_frame=0, end_frame=10, source=Source(path="a.mp4", in_ms=0)),
            Clip(id="c2", start_frame=20, end_frame=30, source=Source(path="b.mp4", in_ms=0)),
        ],
    )


def test_invalid_timeline_raises_and_never_invokes_ffmpeg_rendering(tmp_path: Path):
    tl = _gappy_timeline()

    with patch.object(render_mod, "probe_duration_ms", return_value=60_000), patch.object(
        render_mod, "_run_ffmpeg"
    ) as mock_run_ffmpeg:
        with pytest.raises(TimelineInvalidError) as exc_info:
            render_mod.render(tl, tmp_path, tmp_path / "out.mp4", ffmpeg_exe="ffmpeg")

    assert any(v.code == "CONTIGUOUS" for v in exc_info.value.violations)
    mock_run_ffmpeg.assert_not_called()
    assert not (tmp_path / "out.mp4").exists()


def test_invalid_timeline_error_message_lists_every_violation(tmp_path: Path):
    tl = _gappy_timeline()
    with patch.object(render_mod, "probe_duration_ms", return_value=60_000), patch.object(render_mod, "_run_ffmpeg"):
        with pytest.raises(TimelineInvalidError) as exc_info:
            render_mod.render(tl, tmp_path, tmp_path / "out.mp4", ffmpeg_exe="ffmpeg")
    message = str(exc_info.value)
    assert "CONTIGUOUS" in message
    for v in exc_info.value.violations:
        assert str(v) in message
