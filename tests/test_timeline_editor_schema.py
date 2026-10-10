"""P2.1 tests for the additive editable timeline document."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from poc.timeline.migration import migrate_legacy_timeline
from poc.timeline.schema import (
    AudioProperties,
    EditorClip,
    EditorTimeline,
    EffectRef,
    Layer,
    MediaAsset,
    TimeBase,
    Track,
    Transform,
    TransitionRef,
    Timeline,
)
from poc.timeline.validate import validate_editor_timeline

HAND_TIMELINE = Path(__file__).resolve().parent.parent / "examples" / "hand_timeline.json"


def _editor_timeline(**overrides) -> EditorTimeline:
    assets = [
        MediaAsset(asset_id="asset-video", kind="video", uri="footage/a.mp4", duration_ms=5000),
        MediaAsset(asset_id="asset-image", kind="image", uri="images/title.png"),
        MediaAsset(asset_id="asset-audio", kind="audio", uri="audio/voice.wav", duration_ms=5000),
        MediaAsset(asset_id="asset-music", kind="music", uri="music/song.mp3", duration_ms=10000),
        MediaAsset(asset_id="asset-text", kind="text", text="Opening title"),
    ]
    layers = [
        Layer(layer_id="layer-visual", name="Visual", order=0),
        Layer(layer_id="layer-audio", name="Audio", order=1),
    ]
    tracks = [
        Track(track_id="track-video", kind="video", layer_id="layer-visual"),
        Track(track_id="track-overlay", kind="overlay", layer_id="layer-visual"),
        Track(track_id="track-voice", kind="audio", layer_id="layer-audio"),
        Track(track_id="track-music", kind="music", layer_id="layer-audio"),
    ]
    clips = [
        EditorClip(
            clip_id="clip-video",
            track_id="track-video",
            asset_id="asset-video",
            start_tick=0,
            duration_ticks=1000,
            source_in_ms=500,
            source_out_ms=1500,
        ),
        EditorClip(
            clip_id="scene-image",
            track_id="track-overlay",
            asset_id="asset-image",
            start_tick=1000,
            duration_ticks=1000,
            transform=Transform(scale_x=1.2, scale_y=1.2, opacity=0.9),
        ),
        EditorClip(
            clip_id="clip-voice",
            track_id="track-voice",
            asset_id="asset-audio",
            start_tick=0,
            duration_ticks=3000,
            source_in_ms=0,
            source_out_ms=3000,
            audio=AudioProperties(gain_db=-3.0, fade_in_ms=100),
        ),
        EditorClip(
            clip_id="clip-music",
            track_id="track-music",
            asset_id="asset-music",
            start_tick=0,
            duration_ticks=4000,
            source_in_ms=1000,
            source_out_ms=5000,
            audio=AudioProperties(gain_db=-6.0),
        ),
        EditorClip(
            clip_id="scene-text",
            track_id="track-overlay",
            asset_id="asset-text",
            start_tick=3000,
            duration_ticks=1000,
        ),
    ]
    values = {
        "project_id": "project-1",
        "timeline_id": "timeline-1",
        "duration_ticks": 4000,
        "time_base": TimeBase(ticks_per_second=1000),
        "assets": assets,
        "layers": layers,
        "tracks": tracks,
        "clips": clips,
    }
    values.update(overrides)
    return EditorTimeline(**values)


def _codes(timeline: EditorTimeline) -> set[str]:
    return {violation.code for violation in validate_editor_timeline(timeline)}


def test_a_existing_mvp_timeline_still_loads():
    timeline = Timeline.load(HAND_TIMELINE)
    assert timeline.schema_version == "0.1"
    assert len(timeline.clips) == 5


def test_b_new_editor_timeline_schema_loads():
    timeline = _editor_timeline()
    assert timeline.schema_version == "1.0"
    assert validate_editor_timeline(timeline) == []


def test_c_invalid_schema_version_is_rejected():
    with pytest.raises(ValidationError):
        EditorTimeline.model_validate({
            "schema_version": "9.0",
            "project_id": "p",
            "timeline_id": "t",
            "duration_ticks": 1000,
        })


def test_d_duplicate_stable_ids_are_rejected():
    timeline = _editor_timeline()
    duplicate = timeline.clips[0].model_copy(update={"asset_id": "asset-image"})
    timeline = timeline.model_copy(update={"clips": [*timeline.clips, duplicate]})
    assert "DUPLICATE_ID" in _codes(timeline)


def test_e_invalid_track_and_layer_references_are_rejected():
    timeline = _editor_timeline()
    broken_track = timeline.tracks[0].model_copy(update={"layer_id": "missing-layer"})
    broken_clip = timeline.clips[0].model_copy(update={"track_id": "missing-track"})
    timeline = timeline.model_copy(update={
        "tracks": [broken_track, *timeline.tracks[1:]],
        "clips": [broken_clip, *timeline.clips[1:]],
    })
    assert {"UNKNOWN_LAYER", "UNKNOWN_TRACK"} <= _codes(timeline)


def test_f_invalid_source_ranges_are_rejected():
    timeline = _editor_timeline()
    broken_clip = timeline.clips[0].model_copy(update={"source_out_ms": 500})
    timeline = timeline.model_copy(update={"clips": [broken_clip, *timeline.clips[1:]]})
    assert "SOURCE_RANGE" in _codes(timeline)


def test_g_invalid_timeline_durations_are_rejected():
    with pytest.raises(ValidationError):
        _editor_timeline(duration_ticks=0)
    timeline = _editor_timeline()
    broken_clip = timeline.clips[0].model_copy(update={"start_tick": 3900, "duration_ticks": 200})
    timeline = timeline.model_copy(update={"clips": [broken_clip, *timeline.clips[1:]]})
    assert "TIMELINE_RANGE" in _codes(timeline)


def test_h_invalid_effect_identifier_is_rejected():
    with pytest.raises(ValidationError):
        EffectRef(effect_id="run_ffmpeg")  # type: ignore[arg-type]


def test_i_invalid_transition_identifier_is_rejected():
    with pytest.raises(ValidationError):
        TransitionRef(transition_id="arbitrary_filter")  # type: ignore[arg-type]


def test_j_video_asset_is_supported():
    timeline = _editor_timeline()
    assert timeline.asset_by_id("asset-video").kind == "video"


def test_k_image_asset_is_supported():
    timeline = _editor_timeline()
    assert timeline.asset_by_id("asset-image").kind == "image"


def test_l_audio_and_music_assets_are_supported():
    timeline = _editor_timeline()
    assert timeline.asset_by_id("asset-audio").kind == "audio"
    assert timeline.asset_by_id("asset-music").kind == "music"


def test_m_multiple_tracks_and_layers_can_coexist():
    timeline = _editor_timeline()
    assert len(timeline.tracks) == 4
    assert len(timeline.layers) == 2
    assert validate_editor_timeline(timeline) == []


def test_n_serialization_roundtrip_preserves_editor_timeline(tmp_path):
    original = _editor_timeline()
    path = tmp_path / "editor-timeline.json"
    original.save(path)
    restored = EditorTimeline.load(path)
    assert restored == original


def test_o_migration_preserves_legacy_clips_and_music():
    legacy = Timeline.load(HAND_TIMELINE)
    migrated = migrate_legacy_timeline(
        legacy, project_id="project-migrated", timeline_id="timeline-migrated"
    )
    assert migrated.schema_version == "1.0"
    assert migrated.settings.fps_num == legacy.settings.fps
    assert migrated.project_id == "project-migrated"
    assert migrated.duration_ticks == legacy.duration_ms
    assert migrated.provenance == legacy.provenance
    assert len(migrated.markers) == len(legacy.beats)
    migrated_video_clips = {
        clip.clip_id: clip for clip in migrated.clips
        if clip.track_id != migrated.music_track_id
    }
    for legacy_clip in legacy.clips:
        editor_clip = migrated_video_clips[legacy_clip.id]
        assert editor_clip.start_tick == legacy_clip.start_frame * 1000 // legacy.settings.fps
        assert editor_clip.duration_ticks == legacy_clip.frames * 1000 // legacy.settings.fps
        assert editor_clip.source_in_ms == legacy_clip.source.in_ms
        assert editor_clip.source_out_ms == legacy_clip.source_out_ms(legacy.settings.fps)
    assert migrated.asset_by_id(migrated.music_asset_id).uri == legacy.music.path
    assert validate_editor_timeline(migrated) == []
    repeated = migrate_legacy_timeline(
        legacy, project_id="project-migrated", timeline_id="timeline-migrated"
    )
    assert {asset.asset_id for asset in repeated.assets} == {asset.asset_id for asset in migrated.assets}