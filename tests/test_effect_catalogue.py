from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from poc.timeline import (
    AudioProperties,
    EditorClip,
    EditorTimeline,
    EffectRef,
    Layer,
    MediaAsset,
    TimeBase,
    Track,
    Transform,
    Timeline,
    get_effect_metadata,
    serialize_effect_catalogue,
)
from poc.timeline.schema import EFFECT_CATALOGUE

HAND_TIMELINE = Path(__file__).resolve().parent.parent / "examples" / "hand_timeline.json"


def _timeline(effect: EffectRef) -> EditorTimeline:
    asset = MediaAsset(asset_id="video-asset", kind="video", uri="outside/clip.mp4", duration_ms=5000)
    return EditorTimeline(
        project_id="project-effects",
        timeline_id="timeline-effects",
        duration_ticks=1000,
        time_base=TimeBase(ticks_per_second=1000),
        assets=[asset],
        layers=[Layer(layer_id="layer-video", name="Video", order=0)],
        tracks=[Track(track_id="track-video", kind="video", layer_id="layer-video")],
        clips=[EditorClip(
            clip_id="clip-1", asset_id=asset.asset_id, track_id="track-video",
            start_tick=0, duration_ticks=1000, source_in_ms=0, source_out_ms=1000,
            effects=[effect],
        )],
    )


def test_01_catalogue_ids_are_unique():
    ids = [entry.effect_id for entry in EFFECT_CATALOGUE]
    assert len(ids) == len(set(ids))


def test_02_unknown_effect_id_is_rejected():
    with pytest.raises(ValidationError):
        EffectRef(effect_id="shell_command")  # type: ignore[arg-type]


def test_03_known_effect_id_is_accepted():
    effect = EffectRef(effect_id="fade_in")
    assert effect.effect_id == "fade_in"


def test_04_required_parameters_are_validated():
    effect = EffectRef(effect_id="brightness", parameters={"amount": 0.25})
    assert effect.parameters.amount == 0.25


def test_05_missing_required_parameters_are_rejected():
    with pytest.raises(ValidationError, match="amount"):
        EffectRef(effect_id="brightness")


def test_06_unknown_parameters_are_rejected():
    with pytest.raises(ValidationError):
        EffectRef(effect_id="fade_in", parameters={"duration_ms": 300, "filter": "eq=1"})


def test_07_numeric_parameter_ranges_are_enforced():
    with pytest.raises(ValidationError):
        EffectRef(effect_id="brightness", parameters={"amount": 2.0})
    with pytest.raises(ValidationError):
        EffectRef(effect_id="blur", parameters={"radius": 51})
    with pytest.raises(ValidationError):
        EffectRef(effect_id="zoom_in", parameters={"from_scale": 1.0, "to_scale": 0.0})


def test_08_defaults_are_applied_deterministically():
    first = EffectRef(effect_id="fade_in")
    second = EffectRef(effect_id="fade_in")
    assert first.parameters.duration_ms == 300
    assert first == second


def test_09_invalid_parameter_types_are_rejected():
    with pytest.raises(ValidationError):
        EffectRef(effect_id="brightness", parameters={"amount": "high"})


def test_10_effect_serialization_and_deserialization_roundtrip():
    original = EffectRef(effect_id="pan_left", parameters={"distance": 0.2, "duration_ms": 900})
    restored = EffectRef.model_validate_json(original.model_dump_json())
    assert restored == original


def test_11_effect_reference_survives_timeline_save_load(tmp_path):
    original = _timeline(EffectRef(effect_id="contrast", parameters={"factor": 1.4}))
    path = tmp_path / "timeline.json"
    original.save(path)
    restored = EditorTimeline.load(path)
    assert restored.clips[0].effects == original.clips[0].effects


def test_12_identical_specifications_produce_equivalent_models():
    first = EffectRef(effect_id="saturation", parameters={"factor": 1.3})
    second = EffectRef.model_validate(first.model_dump())
    assert first == second
    assert first.model_dump() == second.model_dump()


def test_13_catalogue_order_and_serialization_are_deterministic():
    first = serialize_effect_catalogue()
    second = serialize_effect_catalogue()
    assert first == second
    decoded = json.loads(first)
    assert [entry["effect_id"] for entry in decoded] == [entry.effect_id for entry in EFFECT_CATALOGUE]


def test_14_effect_cannot_carry_arbitrary_ffmpeg_commands():
    with pytest.raises(ValidationError):
        EffectRef(effect_id="brightness", parameters={"amount": 0.1, "ffmpeg": "-vf scale=2"})
    with pytest.raises(ValidationError):
        EffectRef(effect_id="fade_in", command="ffmpeg -i input")  # type: ignore[call-arg]


def test_15_legacy_mvp_timeline_is_unaffected():
    timeline = Timeline.load(HAND_TIMELINE)
    assert timeline.schema_version == "0.1"
    assert len(timeline.clips) == 5
    assert all(not hasattr(clip, "effects") for clip in timeline.clips)


def test_catalogue_declares_renderer_status_and_determinism():
    assert [entry.effect_id for entry in EFFECT_CATALOGUE] == [
        "fade_in", "fade_out", "zoom_in", "zoom_out",
        "pan_left", "pan_right", "pan_up", "pan_down",
        "brightness", "contrast", "saturation", "blur",
    ]
    assert all(item.renderable for item in EFFECT_CATALOGUE)
    assert all(entry.deterministic for entry in EFFECT_CATALOGUE)
    assert get_effect_metadata("brightness").parameter_schema
