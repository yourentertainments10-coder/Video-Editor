"""Focused tests for poc.decide.cutter (Step 7), sections C and D.

Candidate generation (C) and the greedy cutter itself (D). Everything
here uses hand-built MusicMap/FootageMap objects (tests/_decide_fixtures.py)
-- no audio or video decoding, no FFmpeg.
"""
from __future__ import annotations

import pytest

from poc.decide.cutter import (
    CANDIDATE_MARGIN_MS,
    CANDIDATE_STEP_MS,
    CutterError,
    cut,
    generate_candidates,
    score_candidate,
)
from poc.timeline.validate import validate
from tests._decide_fixtures import make_footage_map, make_music_map


# ---------------------------------------------------------------------------
# C: candidate windows
# ---------------------------------------------------------------------------

def test_candidates_stay_inside_source_duration_with_margin():
    fm = make_footage_map(40, duration_ms=10_000)  # 10s file
    candidates = generate_candidates({"a.mp4": fm}, slot_frames=30)  # 1.0s slot
    in_points = candidates["a.mp4"]
    slot_length_ms = 1000
    for in_ms in in_points:
        assert in_ms >= CANDIDATE_MARGIN_MS
        assert in_ms + slot_length_ms <= fm.duration_ms - CANDIDATE_MARGIN_MS


def test_candidates_are_spaced_by_approximately_the_step_size():
    # Since the fix (candidates are snapped to the source's real frame
    # grid via floor-rounding -- see test_candidate_snapping.py), spacing
    # is no longer exactly CANDIDATE_STEP_MS: each point can move by up
    # to half a source frame to find its nearest frame, then floor by up
    # to another whole frame when converted back to an integer
    # millisecond. At the fixture's default 30 fps (~33.3 ms/frame) that
    # bounds the deviation to a little under one full frame duration.
    fm = make_footage_map(40, duration_ms=10_000)  # default fps=30.0
    candidates = generate_candidates({"a.mp4": fm}, slot_frames=30)
    in_points = candidates["a.mp4"]
    spacings = [b - a for a, b in zip(in_points, in_points[1:])]
    frame_duration_ms = 1000.0 / fm.fps
    assert all(abs(s - CANDIDATE_STEP_MS) <= frame_duration_ms + 1 for s in spacings)


def test_file_too_short_for_slot_yields_no_candidates():
    fm = make_footage_map(4, duration_ms=1000)  # 1s file
    candidates = generate_candidates({"a.mp4": fm}, slot_frames=60)  # 2.0s slot: too long
    assert candidates["a.mp4"] == []


def test_candidates_generated_independently_per_file():
    short_fm = make_footage_map(4, duration_ms=1000)
    long_fm = make_footage_map(40, duration_ms=10_000)
    candidates = generate_candidates({"short.mp4": short_fm, "long.mp4": long_fm}, slot_frames=30)
    assert candidates["short.mp4"] == []
    assert len(candidates["long.mp4"]) > 0


def test_score_candidate_excludes_a_window_with_no_footage_samples():
    fm = make_footage_map(4, duration_ms=2000)  # samples only cover [0, 1.0)s
    result = score_candidate(fm, in_ms=5000, slot_length_ms=500, motion_pct_by_index={}, sharpness_pct_by_index={}, target_motion=0.5, reuse=0.0, coverage=0.0)
    assert result is None


# ---------------------------------------------------------------------------
# D: the greedy cutter
# ---------------------------------------------------------------------------

def _simple_inputs(n_beats: int = 20, n_samples: int = 60):
    beats = [1.0 + 0.5 * i for i in range(n_beats)]
    mm = make_music_map(beats, beat_energy_db=[-15.0] * n_beats)
    footage_maps = {
        "a.mp4": make_footage_map(n_samples, motion=0.4, sharpness=800.0, clip_frac=0.02),
        "b.mp4": make_footage_map(n_samples, motion=0.3, sharpness=900.0, clip_frac=0.03),
        "c.mp4": make_footage_map(n_samples, motion=0.5, sharpness=700.0, clip_frac=0.01),
    }
    return mm, footage_maps


def test_cutter_output_validates_successfully():
    mm, footage_maps = _simple_inputs()
    tl = cut(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0)
    source_durations_ms = {p: fm.duration_ms for p, fm in footage_maps.items()}
    violations = validate(tl, source_durations_ms=source_durations_ms, music_duration_ms=int(mm.duration_s * 1000))
    assert violations == []


def test_cutter_clips_tile_the_full_output_duration():
    mm, footage_maps = _simple_inputs()
    tl = cut(mm, "music.mp3", footage_maps, "calm", duration_s=8.0)
    clips = tl.sorted_clips()
    assert clips[0].start_frame == 0
    for a, b in zip(clips, clips[1:]):
        assert a.end_frame == b.start_frame
    assert clips[-1].end_frame == tl.duration_frames


def test_cutter_selected_source_ranges_are_within_bounds():
    mm, footage_maps = _simple_inputs()
    tl = cut(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0)
    for clip in tl.clips:
        fm = footage_maps[clip.source.path]
        out_ms = clip.source.in_ms + clip.frames * 1000 // tl.settings.fps
        assert clip.source.in_ms >= 0
        assert out_ms <= fm.duration_ms


def test_cutter_is_deterministic_for_identical_inputs():
    mm, footage_maps = _simple_inputs()
    tl_a = cut(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0)
    tl_b = cut(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0)
    assert tl_a == tl_b


def test_cutter_never_reuses_overlapping_source_ranges():
    mm, footage_maps = _simple_inputs()
    tl = cut(mm, "music.mp3", footage_maps, "energetic", duration_s=10.0)
    by_path: dict[str, list[tuple[int, int]]] = {}
    for clip in tl.sorted_clips():
        out_ms = clip.source.in_ms + clip.frames * 1000 // tl.settings.fps
        by_path.setdefault(clip.source.path, []).append((clip.source.in_ms, out_ms))
    for path, ranges in by_path.items():
        ranges.sort()
        for (a_in, a_out), (b_in, b_out) in zip(ranges, ranges[1:]):
            assert b_in >= a_out  # no overlap


def test_cutter_populates_score_and_rationale_for_every_clip():
    mm, footage_maps = _simple_inputs()
    tl = cut(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0)
    for clip in tl.clips:
        assert clip.score is not None
        assert set(clip.rationale) == {"quality", "motion_match", "reuse", "coverage"}


def test_cutter_provenance_records_generator_and_params():
    mm, footage_maps = _simple_inputs()
    tl = cut(mm, "music.mp3", footage_maps, "calm", duration_s=6.0)
    assert tl.provenance.generator == "cutter-v0"
    assert tl.provenance.params["mood"] == "calm"


def test_cutter_raises_cutter_error_when_footage_set_too_small():
    beats = [0.5 * i for i in range(20)]
    mm = make_music_map(beats)
    # a single, very short file: cannot supply even one candidate window
    footage_maps = {"tiny.mp4": make_footage_map(4, duration_ms=600)}
    with pytest.raises(CutterError):
        cut(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0)
