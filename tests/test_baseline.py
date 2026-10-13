"""Focused tests for poc.decide.baseline (Step 7), section E.

The random control: deterministic given a seed, valid on its own terms,
same timing/output constraints as the cutter, and genuinely different
selections across different seeds.
"""
from __future__ import annotations

import pytest

from poc.decide.baseline import cut_baseline
from poc.decide.cutter import CutterError, cut
from poc.timeline.validate import validate
from tests._decide_fixtures import make_footage_map, make_music_map


def _simple_inputs(n_beats: int = 20, n_samples: int = 60):
    beats = [1.0 + 0.5 * i for i in range(n_beats)]
    mm = make_music_map(beats, beat_energy_db=[-15.0] * n_beats)
    footage_maps = {
        "a.mp4": make_footage_map(n_samples, motion=0.4, sharpness=800.0, clip_frac=0.02),
        "b.mp4": make_footage_map(n_samples, motion=0.3, sharpness=900.0, clip_frac=0.03),
        "c.mp4": make_footage_map(n_samples, motion=0.5, sharpness=700.0, clip_frac=0.01),
    }
    return mm, footage_maps


def test_baseline_deterministic_with_fixed_seed():
    mm, footage_maps = _simple_inputs()
    tl_a = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0, seed=42)
    tl_b = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0, seed=42)
    ignore = {"provenance": {"created_at"}}
    assert tl_a.model_dump(exclude=ignore) == tl_b.model_dump(exclude=ignore)


def test_baseline_produces_a_valid_timeline():
    mm, footage_maps = _simple_inputs()
    tl = cut_baseline(mm, "music.mp3", footage_maps, "calm", duration_s=8.0, seed=7)
    source_durations_ms = {p: fm.duration_ms for p, fm in footage_maps.items()}
    violations = validate(tl, source_durations_ms=source_durations_ms, music_duration_ms=int(mm.duration_s * 1000))
    assert violations == []


def test_baseline_shares_the_same_grid_as_the_cutter():
    mm, footage_maps = _simple_inputs()
    cutter_tl = cut(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0)
    baseline_tl = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0, seed=1)
    assert cutter_tl.duration_frames == baseline_tl.duration_frames
    assert cutter_tl.music.in_ms == baseline_tl.music.in_ms
    cutter_bounds = [(c.start_frame, c.end_frame) for c in cutter_tl.sorted_clips()]
    baseline_bounds = [(c.start_frame, c.end_frame) for c in baseline_tl.sorted_clips()]
    assert cutter_bounds == baseline_bounds


def test_baseline_never_reuses_overlapping_source_ranges():
    mm, footage_maps = _simple_inputs()
    tl = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=10.0, seed=3)
    by_path: dict[str, list[tuple[int, int]]] = {}
    for clip in tl.sorted_clips():
        out_ms = clip.source.in_ms + clip.frames * 1000 // tl.settings.fps
        by_path.setdefault(clip.source.path, []).append((clip.source.in_ms, out_ms))
    for path, ranges in by_path.items():
        ranges.sort()
        for (a_in, a_out), (b_in, b_out) in zip(ranges, ranges[1:]):
            assert b_in >= a_out


def test_baseline_provenance_records_generator_and_seed():
    mm, footage_maps = _simple_inputs()
    tl = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0, seed=99)
    assert tl.provenance.generator == "baseline-v0"
    assert tl.provenance.seed == 99


def test_baseline_clips_have_no_score_or_rationale():
    mm, footage_maps = _simple_inputs()
    tl = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0, seed=1)
    for clip in tl.clips:
        assert clip.score is None
        assert clip.rationale == {}


def test_baseline_selection_differs_when_seed_changes():
    mm, footage_maps = _simple_inputs()
    tl_a = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=10.0, seed=1)
    tl_b = cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=10.0, seed=2)
    sources_a = [(c.source.path, c.source.in_ms) for c in tl_a.sorted_clips()]
    sources_b = [(c.source.path, c.source.in_ms) for c in tl_b.sorted_clips()]
    assert sources_a != sources_b


def test_baseline_raises_cutter_error_when_footage_set_too_small():
    beats = [0.5 * i for i in range(20)]
    mm = make_music_map(beats)
    footage_maps = {"tiny.mp4": make_footage_map(4, duration_ms=600)}
    with pytest.raises(CutterError):
        cut_baseline(mm, "music.mp3", footage_maps, "energetic", duration_s=6.0, seed=1)
