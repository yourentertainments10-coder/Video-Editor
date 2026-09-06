"""Focused tests for poc.decide.grid (Step 7), section A.

Beat-aligned slots, low-energy handling, boundary conditions. Everything
here uses hand-built MusicMap objects (tests/_decide_fixtures.py) --
no audio decoding.
"""
from __future__ import annotations

import numpy as np
import pytest

from poc.decide.grid import GridError, build_grid, percentile_rank
from poc.decide.style import style_for_mood
from poc.timeline.schema import ms_to_frame
from poc.timeline.validate import validate
from tests._decide_fixtures import make_music_map


# ---------------------------------------------------------------------------
# percentile_rank
# ---------------------------------------------------------------------------

def test_percentile_rank_orders_values_0_to_1():
    ranks = percentile_rank(np.array([10.0, 30.0, 20.0]))
    assert ranks[0] == pytest.approx(0.0)  # smallest
    assert ranks[2] == pytest.approx(0.5)  # middle
    assert ranks[1] == pytest.approx(1.0)  # largest


def test_percentile_rank_single_value_is_midpoint():
    assert percentile_rank(np.array([42.0])) == pytest.approx([0.5])


def test_percentile_rank_empty_is_empty():
    assert len(percentile_rank(np.array([]))) == 0


def test_percentile_rank_is_deterministic():
    values = np.array([5.0, 1.0, 3.0, 3.0, 9.0])
    assert list(percentile_rank(values)) == list(percentile_rank(values))


# ---------------------------------------------------------------------------
# beat-aligned slots
# ---------------------------------------------------------------------------

def test_slots_are_beat_aligned_energetic():
    # 120 BPM: beats every 0.5s, starting at t=1.0s. All at moderate,
    # uniform energy -> energetic's "otherwise" branch -> 2 beats per cut.
    beats = [1.0 + 0.5 * i for i in range(20)]
    mm = make_music_map(beats, beat_energy_db=[-15.0] * 20)
    params = style_for_mood("energetic")
    grid = build_grid(mm, params, duration_s=6.0)

    # Every slot boundary must land exactly on a beat (converted to frames).
    beat_frames = {ms_to_frame((b - beats[0]) * 1000.0) for b in beats}
    for slot in grid.slots:
        assert slot.start_frame in beat_frames
        assert slot.end_frame in beat_frames


def test_first_slot_starts_at_frame_zero():
    beats = [2.0 + 0.5 * i for i in range(20)]
    mm = make_music_map(beats)
    params = style_for_mood("calm")
    grid = build_grid(mm, params, duration_s=6.0)
    assert grid.slots[0].start_frame == 0


def test_music_in_ms_matches_the_window_start_beat():
    beats = [3.0 + 0.5 * i for i in range(20)]
    mm = make_music_map(beats)
    grid = build_grid(mm, style_for_mood("energetic"), duration_s=4.0)
    assert grid.music_in_ms == 3000


def test_start_beat_index_shifts_the_window():
    beats = [0.5 * i for i in range(30)]
    mm = make_music_map(beats)
    grid = build_grid(mm, style_for_mood("energetic"), duration_s=4.0, start_beat_index=5)
    assert grid.music_in_ms == int(round(beats[5] * 1000))


def test_slots_tile_the_full_duration_contiguously():
    beats = [0.5 * i for i in range(40)]
    mm = make_music_map(beats)
    grid = build_grid(mm, style_for_mood("calm"), duration_s=10.0)
    assert grid.slots[0].start_frame == 0
    for a, b in zip(grid.slots, grid.slots[1:]):
        assert a.end_frame == b.start_frame
    assert grid.slots[-1].end_frame == grid.duration_frames


def test_duration_snaps_to_a_beat_not_the_exact_requested_seconds():
    # 120 BPM (0.5s/beat), calm mood, uniform mid energy -> 2 beats/cut
    # (0.5 is not <= 0.5? it IS <=0.5 -> 4 beats/cut). Use energy that maps
    # to the "otherwise" branch (2 beats/cut = 1.0s slots) so a duration
    # that is not a whole number of seconds must snap down.
    beats = [0.5 * i for i in range(40)]
    mm = make_music_map(beats, beat_energy_db=[-5.0] * 40)  # high energy -> calm "otherwise" -> 2/cut
    grid = build_grid(mm, style_for_mood("calm"), duration_s=5.3)
    # slots are 1.0s (30 frames) each; 5.3s does not divide evenly.
    assert grid.duration_frames % 30 == 0
    assert grid.duration_frames <= ms_to_frame(5300)


# ---------------------------------------------------------------------------
# low-energy handling: available as data, still deterministic per the
# documented beats-per-cut mechanism
# ---------------------------------------------------------------------------

def test_low_energy_and_silent_flags_are_carried_onto_slots():
    beats = [0.5 * i for i in range(10)]
    low_energy = [True, False, False, False, False, False, False, False, False, False]
    silent = [False] * 10
    mm = make_music_map(beats, beat_energy_db=[-40.0] + [-10.0] * 9, low_energy=low_energy, silent=silent)
    grid = build_grid(mm, style_for_mood("energetic"), duration_s=3.0)
    assert grid.slots[0].low_energy is True
    assert all(not s.low_energy for s in grid.slots[1:])


def test_energetic_uses_fewer_beats_per_cut_at_high_energy():
    # Low dB value ranks lowest (0.0), high dB ranks highest (1.0) after
    # percentile-rank normalization across these beats.
    beats = [0.5 * i for i in range(10)]
    energy_db = [-40.0] * 5 + [-5.0] * 5  # first half quiet, second half loud
    mm = make_music_map(beats, beat_energy_db=energy_db)
    grid = build_grid(mm, style_for_mood("energetic"), duration_s=100.0)
    # Slots starting in the quiet half should be longer (2 beats/cut) than
    # slots starting in the loud half (1 beat/cut), since energetic mode
    # cuts more often exactly when energy is high.
    quiet_slot = grid.slots[0]
    # find a slot whose start beat is in the loud half
    loud_slot = next(s for s in grid.slots if s.start_frame >= ms_to_frame(2.5 * 1000))
    assert quiet_slot.frames > loud_slot.frames


def test_grid_never_cuts_on_every_single_beat_at_uniform_mid_energy():
    # Uniform energy across all beats ranks every one of them at 0.5
    # (percentile_rank's tie behavior). At e=0.5: energetic's "otherwise"
    # branch fires (2 beats/cut, e < 0.6) and calm's "e <= 0.5" branch
    # fires (4 beats/cut) -- neither mood cuts on every single beat.
    beats = [0.5 * i for i in range(20)]
    mm = make_music_map(beats, beat_energy_db=[-15.0] * 20)  # uniform -> all rank 0.5
    one_beat_frames = ms_to_frame(500)  # a single 120 BPM beat interval

    energetic_grid = build_grid(mm, style_for_mood("energetic"), duration_s=8.0)
    assert all(s.frames > one_beat_frames for s in energetic_grid.slots)

    calm_grid = build_grid(mm, style_for_mood("calm"), duration_s=8.0)
    assert all(s.frames > one_beat_frames for s in calm_grid.slots)


# ---------------------------------------------------------------------------
# resulting grid is valid on its own terms (timeline_beats support ON_BEAT)
# ---------------------------------------------------------------------------

def test_timeline_beats_cover_every_slot_boundary_for_on_beat_validation():
    from poc.timeline.schema import Clip, Music, Source, Timeline

    beats = [0.5 * i for i in range(20)]
    mm = make_music_map(beats)
    grid = build_grid(mm, style_for_mood("energetic"), duration_s=6.0)

    clips = [
        Clip(id=f"c{i}", start_frame=s.start_frame, end_frame=s.end_frame, source=Source(path="a.mp4", in_ms=0))
        for i, s in enumerate(grid.slots)
    ]
    tl = Timeline(
        duration_frames=grid.duration_frames,
        music=Music(path="m.mp3", in_ms=grid.music_in_ms),
        beats=grid.timeline_beats,
        clips=clips,
    )
    violations = validate(tl)
    assert not any(v.code == "ON_BEAT" for v in violations)


# ---------------------------------------------------------------------------
# boundary conditions
# ---------------------------------------------------------------------------

def test_too_few_beats_raises_grid_error():
    mm = make_music_map([1.0])  # only one beat
    with pytest.raises(GridError):
        build_grid(mm, style_for_mood("energetic"), duration_s=10.0)


def test_start_beat_index_past_available_beats_raises_grid_error():
    mm = make_music_map([0.5 * i for i in range(5)])
    with pytest.raises(GridError):
        build_grid(mm, style_for_mood("energetic"), duration_s=10.0, start_beat_index=4)


def test_duration_shorter_than_one_beats_per_cut_step_raises_grid_error():
    # calm, low energy -> 4 beats per cut = 2.0s minimum slot at 120 BPM;
    # asking for 0.1s cannot fit even one slot.
    beats = [0.5 * i for i in range(10)]
    mm = make_music_map(beats, beat_energy_db=[-40.0] * 10)  # low -> calm 4/cut
    with pytest.raises(GridError):
        build_grid(mm, style_for_mood("calm"), duration_s=0.1)


def test_running_out_of_beats_before_duration_raises_grid_error():
    beats = [0.5 * i for i in range(4)]  # only 4 beats = 1.5s of grid
    mm = make_music_map(beats, duration_s=5.0)
    with pytest.raises(GridError):
        build_grid(mm, style_for_mood("calm"), duration_s=100.0)
