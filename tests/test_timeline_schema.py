"""Focused tests for poc.timeline.schema (Step 2).

Scope is deliberately narrow: per-field Pydantic validation, the frame/ms
conversion helpers, and save/load. Cross-field invariants (contiguity,
on-beat alignment, source-range bounds, ...) belong to the Step 3 validator
and are not exercised here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from poc.timeline import (
    Beat,
    Clip,
    Music,
    Provenance,
    Settings,
    Source,
    Timeline,
    frame_to_ms,
    frames_to_seconds,
    ms_to_frame,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
HAND_TIMELINE = EXAMPLES_DIR / "hand_timeline.json"


# ---------------------------------------------------------------------------
# Frame/ms conversion helpers
# ---------------------------------------------------------------------------

def test_ms_to_frame_exact_grid():
    # 120 BPM -> one beat every 500 ms -> exactly 15 frames at 30 fps.
    assert ms_to_frame(500) == 15
    assert ms_to_frame(0) == 0
    assert ms_to_frame(1000) == 30


def test_frame_to_ms_exact_grid():
    assert frame_to_ms(15) == 500
    assert frame_to_ms(30) == 1000
    assert frame_to_ms(0) == 0


def test_ms_frame_roundtrip_on_grid_points():
    # Values that land exactly on a 30 fps frame boundary must round-trip.
    for frame in (0, 1, 15, 30, 240, 1000):
        ms = frame_to_ms(frame)
        assert ms_to_frame(ms) == frame


def test_frames_to_seconds():
    assert frames_to_seconds(30) == 1.0
    assert frames_to_seconds(15) == 0.5
    assert frames_to_seconds(0) == 0.0


def test_conversion_helpers_respect_custom_fps():
    assert ms_to_frame(1000, fps=60) == 60
    assert frame_to_ms(60, fps=60) == 1000
    assert frames_to_seconds(60, fps=60) == 1.0


# ---------------------------------------------------------------------------
# Per-field model validation
# ---------------------------------------------------------------------------

def test_beat_energy_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Beat(frame=0, energy=1.5)
    with pytest.raises(ValidationError):
        Beat(frame=0, energy=-0.1)


def test_beat_energy_boundaries_accepted():
    Beat(frame=0, energy=0.0)
    Beat(frame=0, energy=1.0)


def test_source_in_ms_negative_rejected():
    with pytest.raises(ValidationError):
        Source(path="footage/A.mp4", in_ms=-1)


def test_clip_start_frame_negative_rejected():
    with pytest.raises(ValidationError):
        Clip(
            id="c001",
            start_frame=-1,
            end_frame=10,
            source=Source(path="footage/A.mp4", in_ms=0),
        )


def test_timeline_duration_must_be_positive():
    with pytest.raises(ValidationError):
        Timeline(
            duration_frames=0,
            music=Music(path="music.mp3"),
        )


def test_timeline_requires_music():
    with pytest.raises(ValidationError):
        Timeline(duration_frames=30)  # type: ignore[call-arg]


def test_extra_field_rejected_on_every_model():
    # model_config extra="forbid" -- flagged explicitly in the handoff note.
    with pytest.raises(ValidationError):
        Settings(fps=30, width=1920, height=1080, bogus_field=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Timeline.model_validate(
            {
                "duration_frames": 30,
                "music": {"path": "music.mp3"},
                "unexpected": "nope",
            }
        )


def test_clip_defaults_score_and_rationale():
    clip = Clip(
        id="c001",
        start_frame=0,
        end_frame=30,
        source=Source(path="footage/A.mp4", in_ms=0),
    )
    assert clip.score is None
    assert clip.rationale == {}


def test_provenance_generator_restricted_to_known_values():
    with pytest.raises(ValidationError):
        Provenance(generator="not-a-real-generator")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Clip helper methods
# ---------------------------------------------------------------------------

def test_clip_frames_and_source_out_ms():
    clip = Clip(
        id="c001",
        start_frame=0,
        end_frame=60,
        source=Source(path="footage/A.mp4", in_ms=2000),
    )
    assert clip.frames == 60
    # 60 frames at 30 fps = 2000 ms of source consumed.
    assert clip.source_out_ms() == 2000 + 2000


def test_timeline_duration_ms_and_sorted_clips():
    tl = Timeline.load(HAND_TIMELINE)
    assert tl.duration_ms == 8000  # 240 frames at 30 fps
    ids_in_order = [c.id for c in tl.sorted_clips()]
    assert ids_in_order == ["c001", "c002", "c003", "c004", "c005"]


# ---------------------------------------------------------------------------
# Hand-written fixture loads and is well-formed
# ---------------------------------------------------------------------------

def test_hand_timeline_loads_and_has_expected_shape():
    tl = Timeline.load(HAND_TIMELINE)
    assert tl.schema_version == "0.1"
    assert tl.settings.fps == 30
    assert tl.duration_frames == 240
    assert len(tl.clips) == 5
    assert len(tl.beats) == 16
    assert tl.provenance.generator == "hand"


# ---------------------------------------------------------------------------
# Save -> load round trip
# ---------------------------------------------------------------------------

def test_save_load_roundtrip(tmp_path: Path):
    original = Timeline.load(HAND_TIMELINE)
    out_path = tmp_path / "roundtrip.json"
    original.save(out_path)
    reloaded = Timeline.load(out_path)
    assert reloaded == original


def test_save_load_roundtrip_for_a_freshly_built_timeline(tmp_path: Path):
    tl = Timeline(
        duration_frames=30,
        music=Music(path="music.mp3", in_ms=1000),
        beats=[Beat(frame=0, downbeat=True, energy=0.5)],
        clips=[
            Clip(
                id="c001",
                start_frame=0,
                end_frame=30,
                source=Source(path="footage/A.mp4", in_ms=0),
                score=0.81,
                rationale={"quality": 0.9},
            )
        ],
        provenance=Provenance(generator="cutter-v0", params={"mood": "energetic"}, seed=42),
    )
    out_path = tmp_path / "fresh.json"
    tl.save(out_path)
    reloaded = Timeline.load(out_path)
    assert reloaded == tl
    assert reloaded.clips[0].score == 0.81


# ---------------------------------------------------------------------------
# Serialization determinism
# ---------------------------------------------------------------------------

def test_serializing_the_same_instance_twice_is_byte_identical():
    tl = Timeline.load(HAND_TIMELINE)
    assert tl.model_dump_json(indent=2) == tl.model_dump_json(indent=2)


def test_save_is_byte_identical_across_repeated_writes(tmp_path: Path):
    tl = Timeline.load(HAND_TIMELINE)
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    tl.save(path_a)
    tl.save(path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_field_order_in_json_matches_model_declaration_order():
    tl = Timeline.load(HAND_TIMELINE)
    data = json.loads(tl.model_dump_json())
    assert list(data.keys()) == [
        "schema_version",
        "settings",
        "duration_frames",
        "music",
        "beats",
        "clips",
        "provenance",
    ]


def test_save_writes_trailing_newline(tmp_path: Path):
    tl = Timeline.load(HAND_TIMELINE)
    out_path = tmp_path / "t.json"
    tl.save(out_path)
    assert out_path.read_text(encoding="utf-8").endswith("\n")
