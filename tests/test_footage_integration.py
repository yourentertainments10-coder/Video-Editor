"""Integration tests for poc.perceive.footage (Step 6), sections B/C.

Generates small, fully controlled synthetic videos with FFmpeg's lavfi
sources -- a solid color (zero change by construction), a frame that
alternates black/white exactly on the 4 Hz grid (maximal, deterministic
motion), a high-frequency test pattern and a heavily blurred copy of it
(sharp vs. blurred), and non-multiple-of-0.25s durations for the sampling
edge case. No external or copyrighted media needed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from poc.perceive.footage import FootageMap, analyze_footage

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


def _make_color_video(path: Path, duration_s: float, color: str = "gray", size: str = "320x240", fps: int = 30) -> None:
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:size={size}:rate={fps}:d={duration_s}", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _make_alternating_video(path: Path, duration_s: float, size: str = "320x240", fps: int = 30) -> None:
    """Luminance flips fully black/white on every 0.25s boundary -- a
    deterministic, maximal 4 Hz motion signal by construction."""
    vf = r"geq=lum='if(mod(floor(T*4)\,2)\,255\,0)':cb=128:cr=128"
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c=black:size={size}:rate={fps}:d={duration_s}", "-vf", vf, str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _make_testsrc_video(path: Path, duration_s: float, size: str = "320x240", fps: int = 30) -> None:
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate={fps}", "-t", str(duration_s), str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _blur_video(src: Path, dst: Path, sigma: int = 25) -> None:
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-i", str(src), "-vf", f"gblur=sigma={sigma}", str(dst)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# B: sampling -- 4 Hz behavior, timestamp correctness, non-multiple duration
# ---------------------------------------------------------------------------

def test_default_sample_rate_is_4hz():
    from poc.perceive.footage import DEFAULT_SAMPLE_RATE_HZ

    assert DEFAULT_SAMPLE_RATE_HZ == 4.0


def test_consecutive_samples_are_spaced_by_the_sampling_interval(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=3.0)
    mm = analyze_footage(video)
    spacings = [b.t_s - a.t_s for a, b in zip(mm.samples, mm.samples[1:])]
    assert all(s == pytest.approx(0.25) for s in spacings)


def test_timestamps_are_exact_multiples_of_the_sample_interval(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=3.0)
    mm = analyze_footage(video)
    for i, sample in enumerate(mm.samples):
        assert sample.t_s == pytest.approx(i / mm.sample_rate_hz)


def test_exact_multiple_duration_yields_the_expected_sample_count(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=3.0)  # 3.0 * 4 Hz = 12 exactly
    mm = analyze_footage(video)
    assert len(mm.samples) == 12


def test_non_multiple_duration_does_not_crash_and_stays_within_bounds(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=2.6)  # not a multiple of 0.25s
    mm = analyze_footage(video)
    duration_s = mm.duration_ms / 1000.0
    expected = int(duration_s * mm.sample_rate_hz)
    assert abs(len(mm.samples) - expected) <= 1
    # every sample must land inside (or right at the edge of) real content
    assert mm.samples[-1].t_s < duration_s + (1.0 / mm.sample_rate_hz)


def test_custom_sample_rate_is_respected(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=2.0)
    mm = analyze_footage(video, sample_rate_hz=2.0)
    assert mm.sample_rate_hz == 2.0
    assert mm.samples[1].t_s == pytest.approx(0.5)
    assert len(mm.samples) == pytest.approx(4, abs=1)


# ---------------------------------------------------------------------------
# C: integration -- static, high-motion, blurred, round-trip, scalars
# ---------------------------------------------------------------------------

def test_static_video_has_zero_motion_at_every_sample(tmp_path: Path):
    video = tmp_path / "static.mp4"
    _make_color_video(video, duration_s=3.0, color="gray")
    mm = analyze_footage(video)
    assert all(s.motion == 0.0 for s in mm.samples)


def test_high_motion_video_has_high_mean_motion(tmp_path: Path):
    video = tmp_path / "highmotion.mp4"
    _make_alternating_video(video, duration_s=3.0)
    mm = analyze_footage(video)
    assert _mean([s.motion for s in mm.samples]) > 0.8


def test_static_and_high_motion_footage_are_clearly_separated(tmp_path: Path):
    static_video = tmp_path / "static.mp4"
    hm_video = tmp_path / "highmotion.mp4"
    _make_color_video(static_video, duration_s=3.0)
    _make_alternating_video(hm_video, duration_s=3.0)

    static_mm = analyze_footage(static_video)
    hm_mm = analyze_footage(hm_video)

    static_motion = _mean([s.motion for s in static_mm.samples])
    hm_motion = _mean([s.motion for s in hm_mm.samples])
    assert hm_motion > static_motion + 0.5


def test_blurred_footage_has_much_lower_sharpness_than_its_sharp_source(tmp_path: Path):
    sharp_video = tmp_path / "sharp.mp4"
    blurred_video = tmp_path / "blurred.mp4"
    _make_testsrc_video(sharp_video, duration_s=2.0)
    _blur_video(sharp_video, blurred_video)

    sharp_mm = analyze_footage(sharp_video)
    blurred_mm = analyze_footage(blurred_video)

    sharp_sharpness = _mean([s.sharpness for s in sharp_mm.samples])
    blurred_sharpness = _mean([s.sharpness for s in blurred_mm.samples])
    assert sharp_sharpness > blurred_sharpness * 5  # clearly, not marginally


def test_footage_map_round_trips_through_json(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=2.0)
    mm = analyze_footage(video)
    out_path = tmp_path / "footage_map.json"
    mm.save(out_path)
    reloaded = FootageMap.load(out_path)
    assert reloaded == mm


def test_duration_and_dimensions_match_the_generated_video(tmp_path: Path):
    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=2.0, size="640x360", fps=25)
    mm = analyze_footage(video)
    assert mm.duration_ms == pytest.approx(2000, abs=50)
    assert mm.width == 640
    assert mm.height == 360
    assert mm.fps == pytest.approx(25.0, abs=0.1)


def test_analysis_width_is_recorded_and_defaults_to_320(tmp_path: Path):
    from poc.perceive.footage import DEFAULT_ANALYSIS_WIDTH

    video = tmp_path / "v.mp4"
    _make_color_video(video, duration_s=1.0)
    mm = analyze_footage(video)
    assert DEFAULT_ANALYSIS_WIDTH == 320
    assert mm.analysis_width == 320
