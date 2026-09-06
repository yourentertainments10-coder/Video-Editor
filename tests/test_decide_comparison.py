"""Section F: the comparison test.

On a controlled synthetic footage set with one clearly higher-quality file
and one clearly lower-quality file, the scoring cutter must predictably
prefer the higher-quality one, while the random baseline must show no such
preference. This is the smallest possible demonstration of the actual
thing Step 9 needs to measure later: does intelligent selection beat
random selection on the same grid?
"""
from __future__ import annotations

from collections import Counter

import pytest

from poc.decide.baseline import cut_baseline
from poc.decide.cutter import cut
from tests._decide_fixtures import make_footage_map, make_music_map

GOOD_PATH = "good.mp4"
BAD_PATH = "bad.mp4"


def _footage_set(n_samples: int = 80):
    # "good": high sharpness, zero clipping, motion in the middle of the
    # range both moods' target_motion formulas land in.
    good = make_footage_map(n_samples, motion=0.4, sharpness=3000.0, clip_frac=0.0, duration_ms=25_000)
    # "bad": heavily blurred (low sharpness) and mostly blown out (high
    # clip_frac) -- exactly the two terms the Appendix's quality formula
    # penalizes, with motion identical to "good" so motion_match cannot
    # explain any difference in score.
    bad = make_footage_map(n_samples, motion=0.4, sharpness=20.0, clip_frac=0.9, duration_ms=25_000)
    return {GOOD_PATH: good, BAD_PATH: bad}


def _music(n_beats: int = 8):
    beats = [1.0 + 1.0 * i for i in range(n_beats)]  # 60 BPM, 1s/beat
    return make_music_map(beats, beat_energy_db=[-15.0] * n_beats)


def test_cutter_prefers_the_higher_quality_candidate_on_every_slot():
    mm = _music()
    footage_maps = _footage_set()
    tl = cut(mm, "music.mp3", footage_maps, "calm", duration_s=6.0)
    picks = [c.source.path for c in tl.sorted_clips()]
    assert picks.count(GOOD_PATH) > picks.count(BAD_PATH)
    # the very first pick, before any reuse/coverage penalty exists, must
    # go to the higher-quality file -- this isolates the quality term.
    assert tl.sorted_clips()[0].source.path == GOOD_PATH


def test_cutter_first_pick_quality_score_reflects_the_real_difference():
    mm = _music()
    footage_maps = _footage_set()
    # calm mood at uniform (rank 0.5) energy needs 4 beats/cut = 4s at
    # this 60 BPM grid; ask for enough duration to complete exactly one slot.
    tl = cut(mm, "music.mp3", footage_maps, "calm", duration_s=5.0)
    first = tl.sorted_clips()[0]
    assert first.source.path == GOOD_PATH
    # Hand-computed expectation: quality = 0.6*sharpness_pct + 0.4*(1-clip_frac).
    # With exactly two footage files, all of "good"'s samples are tied at
    # the top sharpness value and all of "bad"'s at the bottom; percentile
    # rank uses the *average* rank convention (poc.decide.grid.percentile_rank),
    # so an 80/80 tie split does not reach the extremes 0 and 1 -- "good"'s
    # 80 samples occupy sorted positions 80..159 of 160, averaging to
    # (80+159)/2 / 159 = 0.751572..., and clip_frac is 0.0 (no clipping):
    # quality = 0.6*0.751572 + 0.4*1.0 = 0.850943.
    assert first.rationale["quality"] == pytest.approx(0.850943, abs=1e-5)
    # "bad" at the same slot would score far lower: sharpness_pct=0.248427,
    # clip_frac=0.9 -> quality = 0.6*0.248427 + 0.4*0.1 = 0.189056. The
    # margin (0.851 vs 0.189) is large enough that no plausible
    # motion_match difference could flip the choice (motion is identical
    # between the two files here, so motion_match is identical too).


def test_baseline_shows_no_quality_preference_across_many_seeds():
    mm = _music()
    footage_maps = _footage_set()
    counts = Counter()
    n_seeds = 60
    for seed in range(n_seeds):
        tl = cut_baseline(mm, "music.mp3", footage_maps, "calm", duration_s=6.0, seed=seed)
        for clip in tl.sorted_clips():
            counts[clip.source.path] += 1

    total = counts[GOOD_PATH] + counts[BAD_PATH]
    good_fraction = counts[GOOD_PATH] / total
    # With no quality signal at all, the split across many independent
    # seeds should be close to even -- nowhere near the cutter's clear,
    # consistent preference for the good file.
    assert 0.35 < good_fraction < 0.65


def test_cutter_and_baseline_diverge_on_the_same_inputs():
    mm = _music()
    footage_maps = _footage_set()
    cutter_tl = cut(mm, "music.mp3", footage_maps, "calm", duration_s=6.0)
    cutter_good_fraction = sum(
        1 for c in cutter_tl.clips if c.source.path == GOOD_PATH
    ) / len(cutter_tl.clips)

    baseline_good_fractions = []
    for seed in range(20):
        baseline_tl = cut_baseline(mm, "music.mp3", footage_maps, "calm", duration_s=6.0, seed=seed)
        baseline_good_fractions.append(
            sum(1 for c in baseline_tl.clips if c.source.path == GOOD_PATH) / len(baseline_tl.clips)
        )
    mean_baseline_good_fraction = sum(baseline_good_fractions) / len(baseline_good_fractions)

    # The cutter's preference for the good file must be substantially
    # stronger than the baseline's average, on identical inputs.
    assert cutter_good_fraction > mean_baseline_good_fraction + 0.3
