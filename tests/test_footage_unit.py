"""Focused unit tests for poc.perceive.footage (Step 6), section A.

Nothing here decodes a real video: every test calls the private
per-frame metric function directly on hand-built numpy grayscale arrays,
so motion/sharpness/exposure formulas are verified in isolation and fast.
Sampling behavior against real (synthetic) video files is covered in
tests/test_footage_integration.py.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from poc.perceive.footage import (
    FootageProbeError,
    _CLIP_HIGH_THRESHOLD,
    _CLIP_LOW_THRESHOLD,
    _frame_metrics,
    _probe_video_metadata,
)


def _solid(value: int, shape: tuple[int, int] = (64, 64)) -> np.ndarray:
    return np.full(shape, value, dtype=np.uint8)


def _checkerboard(shape: tuple[int, int] = (64, 64), block: int = 4) -> np.ndarray:
    y, x = np.indices(shape)
    pattern = ((x // block) + (y // block)) % 2
    return (pattern * 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# motion: distinguishes static from changing footage
# ---------------------------------------------------------------------------

def test_motion_is_zero_for_the_first_frame_with_no_predecessor():
    motion, *_ = _frame_metrics(_solid(100), None)
    assert motion == 0.0


def test_motion_is_zero_for_identical_consecutive_frames():
    frame = _solid(100)
    motion, *_ = _frame_metrics(frame, frame.copy())
    assert motion == 0.0


def test_motion_is_maximal_for_a_full_black_to_white_transition():
    motion, *_ = _frame_metrics(_solid(255), _solid(0))
    assert motion == pytest.approx(1.0)


def test_motion_distinguishes_static_from_changing_content():
    static_motion, *_ = _frame_metrics(_solid(100), _solid(100))
    changing_motion, *_ = _frame_metrics(_solid(200), _solid(100))
    assert changing_motion > static_motion


def test_motion_scales_with_magnitude_of_change():
    small_change, *_ = _frame_metrics(_solid(110), _solid(100))
    large_change, *_ = _frame_metrics(_solid(200), _solid(100))
    assert large_change > small_change


# ---------------------------------------------------------------------------
# sharpness: distinguishes sharp from blurred footage
# ---------------------------------------------------------------------------

def test_sharpness_distinguishes_sharp_from_blurred():
    sharp = _checkerboard()
    blurred = cv2.GaussianBlur(sharp, (15, 15), 0)
    _, sharp_val, _, _ = _frame_metrics(sharp, None)
    _, blurred_val, _, _ = _frame_metrics(blurred, None)
    assert sharp_val > blurred_val


def test_sharpness_is_zero_for_a_perfectly_flat_frame():
    _, sharpness, _, _ = _frame_metrics(_solid(128), None)
    assert sharpness == 0.0


def test_sharpness_is_never_negative():
    _, sharpness, _, _ = _frame_metrics(_checkerboard(), None)
    assert sharpness >= 0.0


# ---------------------------------------------------------------------------
# exposure (luma + clip_frac): underexposed / normal / overexposed
# ---------------------------------------------------------------------------

def test_underexposed_frame_has_low_luma_and_full_clip_fraction():
    _, _, luma, clip_frac = _frame_metrics(_solid(2), None)
    assert luma < 10.0
    assert clip_frac == 1.0


def test_overexposed_frame_has_high_luma_and_full_clip_fraction():
    _, _, luma, clip_frac = _frame_metrics(_solid(253), None)
    assert luma > 245.0
    assert clip_frac == 1.0


def test_normal_exposure_frame_has_mid_luma_and_no_clipping():
    rng = np.random.default_rng(0)
    frame = np.clip(128 + rng.normal(0, 10, size=(64, 64)), 10, 245).astype(np.uint8)
    _, _, luma, clip_frac = _frame_metrics(frame, None)
    assert 100.0 < luma < 160.0
    assert clip_frac == 0.0


def test_clip_high_threshold_is_strictly_above_250():
    assert _CLIP_HIGH_THRESHOLD == 250
    _, _, _, cf_at_250 = _frame_metrics(_solid(250), None)
    _, _, _, cf_at_251 = _frame_metrics(_solid(251), None)
    assert cf_at_250 == 0.0
    assert cf_at_251 == 1.0


def test_clip_low_threshold_is_strictly_below_5():
    assert _CLIP_LOW_THRESHOLD == 5
    _, _, _, cf_at_5 = _frame_metrics(_solid(5), None)
    _, _, _, cf_at_4 = _frame_metrics(_solid(4), None)
    assert cf_at_5 == 0.0
    assert cf_at_4 == 1.0


def test_luma_is_the_plain_mean_gray_value():
    frame = np.array([[0, 100], [150, 255]], dtype=np.uint8)
    _, _, luma, _ = _frame_metrics(frame, None)
    assert luma == pytest.approx((0 + 100 + 150 + 255) / 4)


# ---------------------------------------------------------------------------
# probing failure mode
# ---------------------------------------------------------------------------

def test_probe_raises_footage_probe_error_on_a_non_video_file(tmp_path):
    import imageio_ffmpeg

    bogus = tmp_path / "not_a_video.txt"
    bogus.write_text("hello, this is not a video file")
    with pytest.raises(FootageProbeError):
        _probe_video_metadata(bogus, imageio_ffmpeg.get_ffmpeg_exe())
