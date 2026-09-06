"""Focused tests for poc.timeline.validate (Step 3).

Each of the seven invariants from docs/MVP.md Section 9 gets its own
violation and its own boundary check where one applies (MIN_LENGTH,
ON_BEAT, SOURCE_RANGE, NO_SOURCE_REUSE, MUSIC_RANGE all have an exact
edge where the check flips). Timelines here are built directly from the
Step 2 models rather than loaded from JSON, so each test isolates the one
invariant it targets without the others interfering.
"""
from __future__ import annotations

from pathlib import Path

from poc.timeline import (
    MIN_CLIP_FRAMES,
    ON_BEAT_TOLERANCE_FRAMES,
    Beat,
    Clip,
    Music,
    Source,
    Timeline,
    validate,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
HAND_TIMELINE = EXAMPLES_DIR / "hand_timeline.json"


# ---------------------------------------------------------------------------
# small builders to keep each test focused on the one invariant it targets
# ---------------------------------------------------------------------------

def _clip(clip_id: str, start: int, end: int, path: str = "footage/A.mp4", in_ms: int = 0) -> Clip:
    return Clip(id=clip_id, start_frame=start, end_frame=end, source=Source(path=path, in_ms=in_ms))


def _timeline(duration_frames: int, clips: list[Clip], beats: list[Beat] | None = None, music_in_ms: int = 0) -> Timeline:
    return Timeline(
        duration_frames=duration_frames,
        music=Music(path="music.mp3", in_ms=music_in_ms),
        beats=beats or [],
        clips=clips,
    )


def _codes(violations) -> set[str]:
    return {v.code for v in violations}


# ---------------------------------------------------------------------------
# A valid timeline produces no violations
# ---------------------------------------------------------------------------

def test_hand_timeline_is_valid_structurally():
    tl = Timeline.load(HAND_TIMELINE)
    assert validate(tl) == []


def test_hand_timeline_is_valid_with_real_media_durations():
    tl = Timeline.load(HAND_TIMELINE)
    source_durations_ms = {
        "footage/A.mp4": 12000,  # used 2000-4000 and 9000-10000
        "footage/B.mp4": 5000,  # used 500-2500
        "footage/C.mp4": 8000,  # used 4000-6000
        "footage/D.mp4": 3000,  # used 1000-2000
    }
    violations = validate(tl, source_durations_ms=source_durations_ms, music_duration_ms=10000)
    assert violations == []


def test_minimal_two_clip_timeline_is_valid():
    tl = _timeline(
        duration_frames=30,
        beats=[Beat(frame=0, energy=0.5), Beat(frame=15, energy=0.5)],
        clips=[
            _clip("c1", 0, 15, path="a.mp4"),
            _clip("c2", 15, 30, path="b.mp4"),
        ],
    )
    assert validate(tl) == []


# ---------------------------------------------------------------------------
# CONTIGUOUS
# ---------------------------------------------------------------------------

def test_contiguous_no_clips():
    tl = _timeline(duration_frames=30, clips=[])
    violations = validate(tl)
    assert _codes(violations) == {"CONTIGUOUS"}
    assert violations[0].clip_id is None


def test_contiguous_first_clip_not_at_zero():
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 6, 30)])
    violations = validate(tl)
    assert any(v.code == "CONTIGUOUS" and v.clip_id == "c1" for v in violations)


def test_contiguous_gap_between_clips():
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 0, 10), _clip("c2", 15, 30)])
    violations = validate(tl)
    gap = [v for v in violations if v.code == "CONTIGUOUS" and v.clip_id == "c2"]
    assert len(gap) == 1
    assert "gap" in gap[0].detail


def test_contiguous_overlap_between_clips():
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 0, 20), _clip("c2", 15, 30)])
    violations = validate(tl)
    overlap = [v for v in violations if v.code == "CONTIGUOUS" and v.clip_id == "c2"]
    assert len(overlap) == 1
    assert "overlap" in overlap[0].detail


def test_contiguous_last_clip_does_not_reach_duration():
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 0, 20)])
    violations = validate(tl)
    assert any(v.code == "CONTIGUOUS" and v.clip_id == "c1" for v in violations)


# ---------------------------------------------------------------------------
# MIN_LENGTH -- boundary at exactly MIN_CLIP_FRAMES (6)
# ---------------------------------------------------------------------------

def test_min_length_at_boundary_is_valid():
    assert MIN_CLIP_FRAMES == 6
    tl = _timeline(duration_frames=6, clips=[_clip("c1", 0, 6)])
    violations = validate(tl)
    assert "MIN_LENGTH" not in _codes(violations)


def test_min_length_one_frame_under_boundary_is_invalid():
    tl = _timeline(duration_frames=5, clips=[_clip("c1", 0, 5)])
    violations = validate(tl)
    assert any(v.code == "MIN_LENGTH" and v.clip_id == "c1" for v in violations)


# ---------------------------------------------------------------------------
# ON_BEAT -- boundary at exactly ON_BEAT_TOLERANCE_FRAMES (1)
# ---------------------------------------------------------------------------

def test_on_beat_frame_zero_is_always_exempt():
    # No beat at frame 0 at all; the only clip still starts at 0.
    tl = _timeline(duration_frames=10, beats=[Beat(frame=5, energy=0.5)], clips=[_clip("c1", 0, 10)])
    assert validate(tl) == []


def test_on_beat_no_beats_defined_skips_the_check():
    tl = _timeline(duration_frames=40, beats=[], clips=[_clip("c1", 0, 19), _clip("c2", 19, 40, path="b.mp4")])
    violations = validate(tl)
    assert "ON_BEAT" not in _codes(violations)


def test_on_beat_at_exact_tolerance_is_valid():
    assert ON_BEAT_TOLERANCE_FRAMES == 1
    beats = [Beat(frame=0, energy=0.5), Beat(frame=20, energy=0.5)]
    # c2 starts at frame 19: nearest beat is 20, distance 1 == tolerance.
    tl = _timeline(duration_frames=40, beats=beats, clips=[_clip("c1", 0, 19), _clip("c2", 19, 40, path="b.mp4")])
    violations = validate(tl)
    assert "ON_BEAT" not in _codes(violations)


def test_on_beat_one_frame_past_tolerance_is_invalid():
    beats = [Beat(frame=0, energy=0.5), Beat(frame=20, energy=0.5)]
    # c2 starts at frame 18: nearest beat is 20, distance 2 > tolerance.
    tl = _timeline(duration_frames=40, beats=beats, clips=[_clip("c1", 0, 18), _clip("c2", 18, 40, path="b.mp4")])
    violations = validate(tl)
    on_beat = [v for v in violations if v.code == "ON_BEAT"]
    assert len(on_beat) == 1
    assert on_beat[0].clip_id == "c2"
    assert "2 frames" in on_beat[0].detail


# ---------------------------------------------------------------------------
# SOURCE_RANGE -- requires source_durations_ms; boundary at exact duration
# ---------------------------------------------------------------------------

def test_source_range_skipped_when_durations_not_provided():
    # 30 frames at 30 fps = 1000 ms consumed; no duration given, so no check.
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 0, 30, in_ms=0)])
    assert validate(tl) == []


def test_source_range_exactly_at_duration_is_valid():
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 0, 30, path="a.mp4", in_ms=0)])
    violations = validate(tl, source_durations_ms={"a.mp4": 1000})  # exactly consumed
    assert "SOURCE_RANGE" not in _codes(violations)


def test_source_range_one_ms_over_duration_is_invalid():
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 0, 30, path="a.mp4", in_ms=0)])
    violations = validate(tl, source_durations_ms={"a.mp4": 999})
    assert any(v.code == "SOURCE_RANGE" and v.clip_id == "c1" for v in violations)


def test_source_range_unknown_source_path_is_invalid():
    tl = _timeline(duration_frames=30, clips=[_clip("c1", 0, 30, path="missing.mp4")])
    violations = validate(tl, source_durations_ms={"a.mp4": 5000})
    match = [v for v in violations if v.code == "SOURCE_RANGE" and v.clip_id == "c1"]
    assert len(match) == 1
    assert "unknown source" in match[0].detail


# ---------------------------------------------------------------------------
# NO_SOURCE_REUSE -- boundary at touching (not overlapping) source ranges
# ---------------------------------------------------------------------------

def test_no_source_reuse_touching_but_not_overlapping_is_valid():
    # c1 reads 0-1000ms of a.mp4 (30 frames); c2 starts reading a.mp4 exactly
    # where c1 left off. Touching is allowed; only overlap is a violation.
    tl = _timeline(
        duration_frames=60,
        clips=[_clip("c1", 0, 30, path="a.mp4", in_ms=0), _clip("c2", 30, 60, path="a.mp4", in_ms=1000)],
    )
    assert "NO_SOURCE_REUSE" not in _codes(validate(tl))


def test_no_source_reuse_overlapping_ranges_is_invalid():
    tl = _timeline(
        duration_frames=60,
        clips=[_clip("c1", 0, 30, path="a.mp4", in_ms=0), _clip("c2", 30, 60, path="a.mp4", in_ms=999)],
    )
    violations = validate(tl)
    reuse = [v for v in violations if v.code == "NO_SOURCE_REUSE"]
    assert len(reuse) == 1
    assert reuse[0].clip_id == "c2"


# ---------------------------------------------------------------------------
# UNIQUE_IDS
# ---------------------------------------------------------------------------

def test_unique_ids_duplicate_reported_once():
    tl = _timeline(
        duration_frames=60,
        clips=[_clip("dup", 0, 30, path="a.mp4"), _clip("dup", 30, 60, path="b.mp4")],
    )
    violations = validate(tl)
    dup = [v for v in violations if v.code == "UNIQUE_IDS"]
    assert len(dup) == 1
    assert dup[0].clip_id == "dup"


def test_unique_ids_no_duplicates_is_valid():
    tl = _timeline(
        duration_frames=60,
        clips=[_clip("c1", 0, 30, path="a.mp4"), _clip("c2", 30, 60, path="b.mp4")],
    )
    assert "UNIQUE_IDS" not in _codes(validate(tl))


# ---------------------------------------------------------------------------
# MUSIC_RANGE -- requires music_duration_ms; boundary at exact fit
# ---------------------------------------------------------------------------

def test_music_range_skipped_when_duration_not_provided():
    tl = _timeline(duration_frames=30, music_in_ms=0, clips=[_clip("c1", 0, 30)])
    assert validate(tl) == []


def test_music_range_exact_fit_is_valid():
    # duration_frames=30 at 30 fps -> 1000 ms window.
    tl = _timeline(duration_frames=30, music_in_ms=0, clips=[_clip("c1", 0, 30)])
    violations = validate(tl, music_duration_ms=1000)
    assert "MUSIC_RANGE" not in _codes(violations)


def test_music_range_one_ms_over_is_invalid():
    tl = _timeline(duration_frames=30, music_in_ms=0, clips=[_clip("c1", 0, 30)])
    violations = validate(tl, music_duration_ms=999)
    assert any(v.code == "MUSIC_RANGE" for v in violations)


def test_music_range_uses_music_in_ms_offset():
    tl = _timeline(duration_frames=30, music_in_ms=500, clips=[_clip("c1", 0, 30)])
    # window is 500 + 1000 = 1500 ms into a 1500 ms track: exact fit, valid.
    assert "MUSIC_RANGE" not in _codes(validate(tl, music_duration_ms=1500))
    # one ms short of the track: invalid.
    assert any(v.code == "MUSIC_RANGE" for v in validate(tl, music_duration_ms=1499))


# ---------------------------------------------------------------------------
# Multiple simultaneous violations
# ---------------------------------------------------------------------------

def test_multiple_simultaneous_violations_are_all_reported():
    # Gap between c1 and c2 (CONTIGUOUS), c2 too short (MIN_LENGTH), and c2's
    # id duplicated by c3 (UNIQUE_IDS), all in one timeline.
    tl = _timeline(
        duration_frames=40,
        clips=[
            _clip("c1", 0, 10, path="a.mp4"),
            _clip("c2", 15, 18, path="b.mp4"),  # gap before it, only 3 frames long
            _clip("c2", 18, 40, path="c.mp4"),  # duplicate id
        ],
    )
    violations = validate(tl)
    codes = _codes(violations)
    assert "CONTIGUOUS" in codes
    assert "MIN_LENGTH" in codes
    assert "UNIQUE_IDS" in codes


def test_violations_are_plain_data_not_exceptions():
    # validate() never raises for invariant failures; it always returns a list.
    tl = _timeline(duration_frames=30, clips=[])
    violations = validate(tl)
    assert isinstance(violations, list)
    assert all(hasattr(v, "code") and hasattr(v, "detail") for v in violations)
