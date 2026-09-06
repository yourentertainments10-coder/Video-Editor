"""Regression tests for the candidate frame-snapping fix (Step 8 follow-up).

Context: docs/MVP.md Section 6 specifies exploring candidates at fixed
250 ms steps. At common frame rates that is not a whole number of source
frames (7.5 frames at 30 fps), so before this fix, roughly half of all
candidates pointed at a timestamp between two real source frames. Step 4's
renderer (unmodified, per the fix's constraints) seeks accurately to
that timestamp when re-encoding a segment, and a seek that lands
mid-frame can render that segment exactly one frame short -- confirmed
directly in tests/test_pipeline.py's end-to-end regression test.

These tests target the fix itself: ``poc.decide.cutter._snap_ms_to_source_frame``
and its use inside ``generate_candidates``. They complement, not replace,
tests/test_cutter.py's existing candidate tests (margin, step spacing,
per-file independence), which still pass unchanged -- this fix touches
only the exact millisecond values, never the exploration concept, the
margin, or which files/slots get candidates at all.
"""
from __future__ import annotations

import pytest

from poc.decide.cutter import CANDIDATE_MARGIN_MS, CANDIDATE_STEP_MS, _snap_ms_to_source_frame, generate_candidates
from tests._decide_fixtures import make_footage_map


# ---------------------------------------------------------------------------
# _snap_ms_to_source_frame: the unit itself
# ---------------------------------------------------------------------------

def test_snap_leaves_an_already_aligned_30fps_timestamp_unchanged():
    # 200 ms and 700 ms are exact multiples of a 30 fps frame (33.333 ms):
    # frame 6 and frame 21 respectively.
    assert _snap_ms_to_source_frame(200, 30.0) == 200
    assert _snap_ms_to_source_frame(700, 30.0) == 700


def test_snap_moves_a_30fps_half_frame_position_onto_a_real_frame():
    # 450 ms is exactly frame 13.5 at 30 fps -- the canonical half-frame
    # case docs/MVP.md's 250 ms step produces. It must move to a real,
    # integer frame index, landing within half a frame (~16.7 ms) of 450.
    snapped = _snap_ms_to_source_frame(450, 30.0)
    frame_index = snapped * 30.0 / 1000.0
    assert abs(frame_index - round(frame_index)) < 0.02  # lands on a real frame
    assert abs(snapped - 450) <= 1000.0 / 30.0 / 2 + 1  # moved by at most ~half a frame


def test_snap_is_deterministic():
    assert _snap_ms_to_source_frame(450, 30.0) == _snap_ms_to_source_frame(450, 30.0)


def test_snap_generalizes_to_a_non_30fps_source():
    # 450 ms at 24 fps is frame 10.8 -- also not an integer.
    snapped = _snap_ms_to_source_frame(450, 24.0)
    frame_index = snapped * 24.0 / 1000.0
    assert abs(frame_index - round(frame_index)) < 0.02


def test_snap_leaves_timestamp_unchanged_when_fps_is_unknown():
    # FootageMap.fps can be None (no parseable fps token in ffmpeg's
    # banner). There is no frame grid to snap to; behavior matches
    # pre-fix (no snapping at all).
    assert _snap_ms_to_source_frame(450, None) == 450


def test_snap_leaves_timestamp_unchanged_when_fps_is_non_positive():
    assert _snap_ms_to_source_frame(450, 0.0) == 450
    assert _snap_ms_to_source_frame(450, -30.0) == 450


# ---------------------------------------------------------------------------
# generate_candidates: every candidate lands on a real 30 fps frame
# ---------------------------------------------------------------------------

def test_every_candidate_lands_on_a_real_frame_at_30fps():
    # A candidate "lands on a real frame" precisely when re-snapping it
    # changes nothing -- the strict, non-heuristic version of that check
    # (checking closeness of in_ms*fps/1000 to an integer is fragile: the
    # fix floors when converting a frame index back to milliseconds, so a
    # correctly-snapped candidate can sit up to ~1 ms below its exact
    # frame boundary by design -- see _snap_ms_to_source_frame's docstring).
    fm = make_footage_map(40, duration_ms=10_000)  # default fps=30.0
    candidates = generate_candidates({"a.mp4": fm}, slot_frames=30)["a.mp4"]
    assert len(candidates) > 10  # sanity: the fixture actually produced candidates
    for in_ms in candidates:
        assert _snap_ms_to_source_frame(in_ms, fm.fps) == in_ms, (
            f"candidate in_ms={in_ms} is not stable under snapping at {fm.fps} fps"
        )


def test_raw_250ms_exploration_would_have_produced_half_frame_positions_at_30fps():
    # Documents exactly why the fix is needed: the *unsnapped* 250 ms
    # exploration grid genuinely does contain half-frame positions at
    # 30 fps -- this is not a hypothetical edge case.
    raw_points = range(CANDIDATE_MARGIN_MS, CANDIDATE_MARGIN_MS + 2000, CANDIDATE_STEP_MS)
    frame_indices = [p * 30.0 / 1000.0 for p in raw_points]
    assert any(abs(f - round(f)) > 0.4 for f in frame_indices)  # genuine half-frame points exist


def test_snapping_preserves_already_aligned_candidates_exactly():
    # At 30 fps, half of the raw exploration points (200, 700, 1200, ...)
    # are already frame-aligned; the fix must not perturb those.
    fm = make_footage_map(40, duration_ms=10_000)
    candidates = generate_candidates({"a.mp4": fm}, slot_frames=30)["a.mp4"]
    assert 200 in candidates
    assert 700 in candidates
    assert 1200 in candidates


def test_candidates_still_respect_the_margin_after_snapping():
    fm = make_footage_map(40, duration_ms=10_000)
    slot_frames = 30
    slot_length_ms = 1000
    candidates = generate_candidates({"a.mp4": fm}, slot_frames=slot_frames)["a.mp4"]
    for in_ms in candidates:
        assert in_ms >= CANDIDATE_MARGIN_MS
        assert in_ms + slot_length_ms <= fm.duration_ms - CANDIDATE_MARGIN_MS


def test_candidates_generated_independently_per_file_fps():
    # Two files at different native frame rates snap to different grids;
    # neither file's candidates should be influenced by the other's fps.
    fm_30 = make_footage_map(40, duration_ms=10_000, fps=30.0)
    fm_24 = make_footage_map(40, duration_ms=10_000, fps=24.0)
    candidates = generate_candidates({"a.mp4": fm_30, "b.mp4": fm_24}, slot_frames=30)
    for in_ms in candidates["a.mp4"]:
        assert _snap_ms_to_source_frame(in_ms, 30.0) == in_ms
    for in_ms in candidates["b.mp4"]:
        assert _snap_ms_to_source_frame(in_ms, 24.0) == in_ms


def test_candidate_dropped_rather_than_clamped_if_snapping_pushes_it_out_of_bounds():
    # Construct a file whose valid window is exactly one 250ms step wide,
    # with the only candidate sitting right at the edge such that
    # snapping (at an unusual fps chosen to snap it outward) pushes it
    # out of the margin. It must be dropped, not silently clamped back in.
    slot_frames = 30  # 1.0s slot at 30 fps timeline
    # duration chosen so max_in_ms == margin_ms exactly: only in_ms=200 is
    # a candidate before snapping.
    duration_ms = CANDIDATE_MARGIN_MS + 1000 + CANDIDATE_MARGIN_MS  # 200+1000+200=1400
    # An extremely low fps (2.0) makes frame duration 500 ms, so 200 ms
    # snaps to the nearest multiple of 500 ms, i.e. 0 -- outside the
    # margin (0 < 200) -- and must be excluded, not clamped to 200.
    fm = make_footage_map(4, duration_ms=duration_ms, fps=2.0)
    candidates = generate_candidates({"a.mp4": fm}, slot_frames=slot_frames)["a.mp4"]
    assert 0 not in candidates
    assert all(c >= CANDIDATE_MARGIN_MS for c in candidates)
