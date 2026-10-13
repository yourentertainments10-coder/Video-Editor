from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from poc.timeline import (
    EditorClip,
    EditorTimeline,
    Layer,
    MediaAsset,
    TimeBase,
    Track,
    Timeline,
    TransitionRef,
    TRANSITION_CATALOGUE,
    get_transition_metadata,
    serialize_transition_catalogue,
    validate_editor_timeline,
)

HAND_TIMELINE = Path(__file__).resolve().parent.parent / "examples" / "hand_timeline.json"


def _two_clip_timeline(transition: TransitionRef | None, *, first_clip_transition=None) -> EditorTimeline:
    asset = MediaAsset(asset_id="video", kind="video", uri="outside/clip.mp4", duration_ms=5000)
    clips = [
        EditorClip(
            clip_id="clip-a", asset_id=asset.asset_id, track_id="track-video",
            start_tick=0, duration_ticks=1000, source_in_ms=0, source_out_ms=1000,
            transition=first_clip_transition,
        ),
        EditorClip(
            clip_id="clip-b", asset_id=asset.asset_id, track_id="track-video",
            start_tick=1000, duration_ticks=1000, source_in_ms=1000, source_out_ms=2000,
            transition=transition,
        ),
    ]
    return EditorTimeline(
        project_id="project-transitions", timeline_id="timeline-transitions",
        duration_ticks=2000, time_base=TimeBase(ticks_per_second=1000), assets=[asset],
        layers=[Layer(layer_id="layer-video", name="Video", order=0)],
        tracks=[Track(track_id="track-video", kind="video", layer_id="layer-video")],
        clips=clips,
    )


def _codes(timeline: EditorTimeline) -> set[str]:
    return {item.code for item in validate_editor_timeline(timeline)}


def test_01_transition_ids_are_unique():
    ids = [item.transition_id for item in TRANSITION_CATALOGUE]
    assert len(ids) == len(set(ids))
    assert ids == [
        "cut", "fade", "dissolve", "dip_to_black", "dip_to_white",
        "wipe_left", "wipe_right", "wipe_up", "wipe_down",
    ]


def test_02_unknown_transition_id_is_rejected():
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="custom_filter")  # type: ignore[arg-type]


def test_03_known_transition_id_is_accepted():
    assert TransitionRef(transition_id="fade").transition_id == "fade"


def test_04_required_transition_parameters_are_validated():
    transition = TransitionRef(transition_id="dissolve", parameters={"mask_seed": 123})
    assert transition.parameters.mask_seed == 123


def test_05_missing_required_transition_parameters_are_rejected():
    with pytest.raises(ValidationError, match="mask_seed"):
        TransitionRef(transition_id="dissolve")


def test_06_unknown_transition_parameters_are_rejected():
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="fade", parameters={"curve": "linear", "filter": "xfade=expr"})


def test_07_invalid_transition_parameter_types_are_rejected():
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="dissolve", parameters={"mask_seed": "random"})


def test_08_transition_parameter_ranges_are_enforced():
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="wipe_left", parameters={"softness": 1.5})
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="dip_to_black", parameters={"peak_fraction": 0.0})


def test_09_transition_defaults_are_deterministic():
    first = TransitionRef(transition_id="fade")
    second = TransitionRef(transition_id="fade")
    assert first == second
    assert first.duration_ticks == 500
    assert first.parameters.curve == "linear"


def test_10_transition_serialization_roundtrip():
    original = TransitionRef(transition_id="wipe_up", parameters={"softness": 0.2})
    restored = TransitionRef.model_validate_json(original.model_dump_json())
    assert restored == original


def test_11_transition_reference_survives_timeline_save_load(tmp_path):
    timeline = _two_clip_timeline(TransitionRef(transition_id="fade", duration_ticks=200))
    path = tmp_path / "transition-timeline.json"
    timeline.save(path)
    restored = EditorTimeline.load(path)
    assert restored.clips[1].transition == timeline.clips[1].transition


def test_12_equivalent_specs_validate_to_equal_models():
    first = TransitionRef(transition_id="dip_to_white", parameters={"peak_fraction": 0.6})
    second = TransitionRef.model_validate(first.model_dump())
    assert first == second
    assert first.model_dump() == second.model_dump()


def test_13_catalogue_order_and_serialization_are_deterministic():
    first = serialize_transition_catalogue()
    assert first == serialize_transition_catalogue()
    payload = json.loads(first)
    assert [item["transition_id"] for item in payload] == [item.transition_id for item in TRANSITION_CATALOGUE]


def test_14_transition_rejects_arbitrary_commands():
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="fade", command="ffmpeg -vf xfade")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="wipe_left", parameters={"softness": 0.1, "ffmpeg_filter": "[0]xfade"})


def test_15_legacy_timeline_v01_remains_unaffected():
    timeline = Timeline.load(HAND_TIMELINE)
    assert timeline.schema_version == "0.1"
    assert len(timeline.clips) == 5
    assert all(not hasattr(clip, "transition") for clip in timeline.clips)


def test_16_transition_duration_is_separate_from_source_ranges():
    timeline = _two_clip_timeline(TransitionRef(transition_id="fade", duration_ticks=200))
    incoming = timeline.clips[1]
    assert incoming.transition.duration_ticks == 200
    assert (incoming.source_in_ms, incoming.source_out_ms) == (1000, 2000)


def test_17_invalid_transition_durations_are_rejected():
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="fade", duration_ticks=0)
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="cut", duration_ticks=1)
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="dissolve", duration_ticks=30001, parameters={"mask_seed": 7})


def test_18_transition_must_attach_to_a_boundary_with_adjacent_clips():
    valid = _two_clip_timeline(TransitionRef(transition_id="cut"))
    assert "TRANSITION_BOUNDARY" not in _codes(valid)
    invalid = _two_clip_timeline(
        TransitionRef(transition_id="fade"),
        first_clip_transition=TransitionRef(transition_id="cut"),
    )
    assert "TRANSITION_BOUNDARY" in _codes(invalid)


def test_transition_duration_is_limited_by_both_adjacent_clips():
    too_long = _two_clip_timeline(TransitionRef(transition_id="fade", duration_ticks=700))
    assert "TRANSITION_RANGE" in _codes(too_long)


def test_catalogue_exposes_renderability_and_behavior_contracts():
    RENDERABLE_NOW = {"cut", "fade", "dip_to_black", "wipe_left"}
    assert {i.transition_id for i in TRANSITION_CATALOGUE if i.renderable} == RENDERABLE_NOW
    assert all(item.deterministic for item in TRANSITION_CATALOGUE)
    assert get_transition_metadata("cut").name == "Cut"
    assert all(item.behavior_contract for item in TRANSITION_CATALOGUE)
