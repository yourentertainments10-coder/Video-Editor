"""Test NotImplementedError guards in editor_render.py"""

import pytest

from poc.execute.editor_render import EditorRenderer
from poc.project_assets import ProjectAssetLibrary
from poc.timeline import (
    AudioProperties,
    EditorSettings,
    EditorTimeline,
    EffectRef,
    Layer,
    MediaAsset,
    TimeBase,
    Track,
    TransitionRef,
    EditorClip,
)


def test_editor_renderer_raises_on_effects(tmp_path):
    """EditorRenderer should raise NotImplementedError for effects."""
    # Use an existing fixture video
    source_path = "tests/fixtures/ingest/vfr_clip.mp4"

    library = ProjectAssetLibrary.create(tmp_path / "project", project_id="test")
    imported = library.import_asset(source_path)
    asset = imported.media_asset

    settings = EditorSettings(fps_num=30, fps_den=1, width=640, height=480)
    timeline = EditorTimeline(
        project_id="test",
        timeline_id="test-timeline",
        duration_ticks=3000,
        time_base=TimeBase(ticks_per_second=1000),
        settings=settings,
        assets=[asset],
        layers=[Layer(layer_id="visual-layer", name="Visual", order=0)],
        tracks=[Track(track_id="video", kind="video", layer_id="visual-layer")],
        clips=[EditorClip(
            clip_id="test-clip",
            asset_id=asset.asset_id,
            track_id="video",
            start_tick=0,
            duration_ticks=3000,
            source_in_ms=0,
            source_out_ms=3000,
            effects=[EffectRef(effect_id="brightness", parameters={"amount": 0.2})]
        )]
    )

    renderer = EditorRenderer()
    with pytest.raises(NotImplementedError, match=r"P2\.6 step 3c\+: effects rendering not yet implemented"):
        renderer.compile(timeline, library)


def test_editor_renderer_raises_on_transitions(tmp_path):
    """EditorRenderer should raise NotImplementedError for transitions."""
    source_path = "tests/fixtures/ingest/vfr_clip.mp4"

    library = ProjectAssetLibrary.create(tmp_path / "project", project_id="test")
    imported = library.import_asset(source_path)
    asset = imported.media_asset

    settings = EditorSettings(fps_num=30, fps_den=1, width=640, height=480)
    timeline = EditorTimeline(
        project_id="test",
        timeline_id="test-timeline",
        duration_ticks=6000,  # 6 seconds to accommodate two clips
        time_base=TimeBase(ticks_per_second=1000),
        settings=settings,
        assets=[asset],
        layers=[Layer(layer_id="visual-layer", name="Visual", order=0)],
        tracks=[Track(track_id="video", kind="video", layer_id="visual-layer")],
        clips=[
            EditorClip(
                clip_id="clip-1",
                asset_id=asset.asset_id,
                track_id="video",
                start_tick=0,
                duration_ticks=3000,
                source_in_ms=0,
                source_out_ms=3000,
            ),
            EditorClip(
                clip_id="test-clip",
                asset_id=asset.asset_id,
                track_id="video",
                start_tick=3000,
                duration_ticks=3000,
                source_in_ms=0,
                source_out_ms=3000,
                transition=TransitionRef(transition_id="fade", duration_ticks=500)
            )
        ]
    )

    renderer = EditorRenderer()
    with pytest.raises(NotImplementedError, match=r"P2\.6 step 3c\+: transitions rendering not yet implemented"):
        renderer.compile(timeline, library)


def test_editor_renderer_raises_on_audio(tmp_path):
    """EditorRenderer should raise NotImplementedError for audio on clips."""
    source_path = "tests/fixtures/ingest/vfr_clip.mp4"

    library = ProjectAssetLibrary.create(tmp_path / "project", project_id="test")
    imported = library.import_asset(source_path)
    asset = imported.media_asset

    settings = EditorSettings(fps_num=30, fps_den=1, width=640, height=480)
    timeline = EditorTimeline(
        project_id="test",
        timeline_id="test-timeline",
        duration_ticks=3000,
        time_base=TimeBase(ticks_per_second=1000),
        settings=settings,
        assets=[asset],
        layers=[Layer(layer_id="visual-layer", name="Visual", order=0)],
        tracks=[Track(track_id="video", kind="video", layer_id="visual-layer")],
        clips=[EditorClip(
            clip_id="test-clip",
            asset_id=asset.asset_id,
            track_id="video",
            start_tick=0,
            duration_ticks=3000,
            source_in_ms=0,
            source_out_ms=3000,
            audio=AudioProperties(gain_db=-3.0, fade_in_ms=100)
        )]
    )

    renderer = EditorRenderer()
    with pytest.raises(NotImplementedError, match=r"P2\.6 step 3c\+: audio rendering not yet implemented"):
        renderer.compile(timeline, library)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])