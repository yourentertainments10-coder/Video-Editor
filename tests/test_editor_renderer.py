from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
import pytest

from poc.execute import probe
from poc.project_assets import ProjectAssetLibrary
from poc.timeline import (
    AudioProperties,
    Beat,
    Clip,
    EditorClip,
    EditorSettings,
    EditorTimeline,
    EffectRef,
    FadeParameters,
    FadeTransitionParameters,
    Layer,
    MediaAsset,
    Music,
    Provenance,
    Settings,
    Source,
    TimeBase,
    Timeline,
    TimelineMarker,
    Track,
    TransitionRef,
    ZoomInParameters,
    ZoomOutParameters,
    validate,
    validate_editor_timeline,
)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _video(path: Path, *, duration: float = 2.0, color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"color=c={color}:s=64x48:r=10:d={duration}", "-an", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return path


def _audio(path: Path, *, duration: float = 3.0, frequency: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"sine=frequency={frequency}:duration={duration}", "-c:a", "pcm_s16le", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return path


def _image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", np.full((48, 64, 3), (30, 120, 220), dtype=np.uint8))
    assert ok
    encoded.tofile(path)
    return path


def _project(tmp_path: Path, *media: Path) -> tuple[ProjectAssetLibrary, dict[str, MediaAsset]]:
    library = ProjectAssetLibrary.create(tmp_path / "project", project_id="renderer-test")
    imported = {path.name: library.import_asset(path).media_asset for path in media}
    return library, imported


def _timeline(
    assets: list[MediaAsset], clips: list[EditorClip], duration_ms: int,
    *, tracks: list[Track] | None = None,
) -> EditorTimeline:
    layers = [
        Layer(layer_id="visual-layer", name="Visual", order=0),
        Layer(layer_id="audio-layer", name="Audio", order=1),
    ]
    return EditorTimeline(
        project_id="renderer-test",
        timeline_id="render-timeline",
        duration_ticks=duration_ms,
        time_base=TimeBase(ticks_per_second=1000),
        settings=EditorSettings(fps_num=10, fps_den=1, width=64, height=48),
        assets=assets,
        layers=layers,
        tracks=tracks or [
            Track(track_id="video", kind="video", layer_id="visual-layer"),
            Track(track_id="overlay", kind="overlay", layer_id="visual-layer"),
            Track(track_id="voice", kind="audio", layer_id="audio-layer"),
            Track(track_id="music", kind="music", layer_id="audio-layer"),
        ],
        clips=clips,
    )


def _visual_clip(clip_id: str, asset: MediaAsset, track_id: str, start: int, duration: int,
                 source_in: int = 0, source_out: int | None = None) -> EditorClip:
    return EditorClip(
        clip_id=clip_id, asset_id=asset.asset_id, track_id=track_id,
        start_tick=start, duration_ticks=duration,
        source_in_ms=source_in if asset.kind == "video" else None,
        source_out_ms=source_out if asset.kind == "video" else None,
    )


# Now the tests that require the editor render module.
# We mark them as xfail because the module is not yet implemented (P2.6).
# The import is done inside the test so that collection does not fail.

def test_one_video_clip_renders_as_editor_timeline(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    source = _video(tmp_path / "source" / "one.mp4")
    library, assets = _project(tmp_path, source)
    asset = assets[source.name]
    timeline = _timeline(
        [asset], [_visual_clip("clip-one", asset, "video", 0, 1000, 250, 1250)], 1000,
    )
    result = render_editor_timeline(timeline, library, tmp_path / "out" / "one.mp4")
    output_info = probe(result.out_path, FFMPEG)
    assert output_info.has_video
    assert output_info.duration_ms == pytest.approx(1000, abs=100)
    assert result.video_clip_order == ("clip-one",)


def test_multiple_adjacent_video_clips_keep_timeline_order(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    red = _video(tmp_path / "source" / "red.mp4", color="red")
    blue = _video(tmp_path / "source" / "blue.mp4", color="blue")
    library, assets = _project(tmp_path, red, blue)
    a, b = assets[red.name], assets[blue.name]
    timeline = _timeline(
        [a, b], [
            _visual_clip("red-clip", a, "video", 0, 1000, 0, 1000),
            _visual_clip("blue-clip", b, "video", 1000, 1000, 500, 1500),
        ], 2000,
    )
    plan = build_editor_render_plan(timeline, library)
    assert plan.video_clip_order == ("red-clip", "blue-clip")
    result = render_editor_timeline(timeline, library, tmp_path / "ordered.mp4")
    assert result.video_clip_order == plan.video_clip_order
    assert probe(result.out_path, FFMPEG).duration_ms == pytest.approx(2000, abs=100)


def test_render_plan_preserves_exact_source_trim_ranges(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    source = _video(tmp_path / "source" / "trim.mp4")
    library, assets = _project(tmp_path, source)
    asset = assets[source.name]
    timeline = _timeline(
        [asset], [_visual_clip("trim", asset, "video", 0, 750, 250, 1000)], 750,
    )
    segment = build_editor_render_plan(timeline, library).video_segments[0]
    assert (segment.source_in_ms, segment.source_out_ms) == (250, 1000)
    assert segment.duration_frames == 8


def test_rendered_duration_matches_editor_timeline_duration(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    source = _video(tmp_path / "source" / "duration.mp4")
    library, assets = _project(tmp_path, source)
    asset = assets[source.name]
    timeline = _timeline([asset], [_visual_clip("duration", asset, "video", 0, 2000, 0, 2000)], 2000)
    result = render_editor_timeline(timeline, library, tmp_path / "duration.mp4")
    assert result.duration_ms == 2000
    assert result.duration_frames == 20
    assert probe(result.out_path, FFMPEG).duration_ms == pytest.approx(2000, abs=100)


def test_render_plan_order_is_stable_for_identical_timeline(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    first = _video(tmp_path / "source" / "first.mp4")
    second = _video(tmp_path / "source" / "second.mp4", color="blue")
    library, assets = _project(tmp_path, first, second)
    a, b = assets[first.name], assets[second.name]
    timeline = _timeline([a, b], [
        _visual_clip("later", b, "video", 1000, 1000, 0, 1000),
        _visual_clip("earlier", a, "video", 0, 1000, 0, 1000),
    ], 2000)
    plan_a = build_editor_render_plan(timeline, library)
    plan_b = build_editor_render_plan(timeline, library)
    assert plan_a == plan_b
    assert plan_a.video_clip_order == ("earlier", "later")


def test_audio_and_music_tracks_mix_with_video(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    video = _video(tmp_path / "source" / "picture.mp4")
    voice = _audio(tmp_path / "source" / "voice.wav", frequency=330)
    music = _audio(tmp_path / "source" / "music.wav", frequency=550)
    library, assets = _project(tmp_path, video, voice, music)
    v, a, m = assets[video.name], assets[voice.name], assets[music.name]
    clips = [
        _visual_clip("video", v, "video", 0, 2000, 0, 2000),
        EditorClip(clip_id="voice", asset_id=a.asset_id, track_id="voice", start_tick=0,
                   duration_ticks=1500, source_in_ms=500, source_out_ms=2000,
                   audio=AudioProperties(gain_db=-3.0, fade_in_ms=100)),
        EditorClip(clip_id="music", asset_id=m.asset_id, track_id="music", start_tick=0,
                   duration_ticks=2000, source_in_ms=0, source_out_ms=2000,
                   audio=AudioProperties(gain_db=-6.0)),
    ]
    timeline = _timeline([v, a, m], clips, 2000)
    result = render_editor_timeline(timeline, library, tmp_path / "mixed-audio.mp4")
    info = probe(result.out_path, FFMPEG)
    assert info.has_video and info.has_audio
    assert result.audio_clip_count == 2


def test_image_clip_renders_when_supported_by_opencv_and_ffmpeg(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    image = _image(tmp_path / "source" / "still.png")
    library, assets = _project(tmp_path, image)
    asset = assets[image.name]
    timeline = _timeline(
        [asset], [_visual_clip("still", asset, "overlay", 0, 1000)], 1000,
    )
    result = render_editor_timeline(timeline, library, tmp_path / "still.mp4")
    info = probe(result.out_path, FFMPEG)
    assert info.has_video
    assert result.video_clip_order == ("still",)


def test_visual_overlap_is_rejected_as_unsupported_mixed_media_layout(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    one = _video(tmp_path / "source" / "one.mp4")
    two = _video(tmp_path / "source" / "two.mp4", color="blue")
    library, assets = _project(tmp_path, one, two)
    a, b = assets[one.name], assets[two.name]
    timeline = _timeline([a, b], [
        _visual_clip("one", a, "video", 0, 1500, 0, 1500),
        _visual_clip("two", b, "overlay", 1000, 1000, 0, 1000),
    ], 2000)
    with pytest.raises(EditorRenderError, match="overlapping visual clips"):
        render_editor_timeline(timeline, library, tmp_path / "overlap.mp4")


def test_non_renderable_effect_is_rejected_clearly(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    source = _video(tmp_path / "source" / "one.mp4")
    library, assets = _project(tmp_path, source)
    asset = assets[source.name]
    clip = _visual_clip("one", asset, "video", 0, 1000, 0, 1000).model_copy(
        update={"effects": [EffectRef(effect_id="brightness", parameters={"amount": 0.2})]}
    )
    timeline = _timeline([asset], [clip], 1000)
    with pytest.raises(EditorRenderError, match="effect.*brightness.*not renderable"):
        render_editor_timeline(timeline, library, tmp_path / "effect.mp4")


def test_non_renderable_transition_is_rejected_without_mutating_source_ranges(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    first = _video(tmp_path / "source" / "first.mp4")
    second = _video(tmp_path / "source" / "second.mp4", color="blue")
    library, assets = _project(tmp_path, first, second)
    a, b = assets[first.name], assets[second.name]
    incoming = _visual_clip("second", b, "video", 1000, 1000, 200, 1200).model_copy(
        update={"transition": TransitionRef(transition_id="wipe_right", duration_ticks=100)}
    )
    timeline = _timeline([a, b], [
        _visual_clip("first", a, "video", 0, 1000, 0, 1000), incoming,
    ], 2000)
    source_range = (incoming.source_in_ms, incoming.source_out_ms)
    with pytest.raises(EditorRenderError, match="transition.*wipe_right.*not renderable"):
        render_editor_timeline(timeline, library, tmp_path / "transition.mp4")
    assert (incoming.source_in_ms, incoming.source_out_ms) == source_range


def test_transition_on_first_clip_fails_boundary_validation(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    source = _video(tmp_path / "source" / "one.mp4")
    library, assets = _project(tmp_path, source)
    asset = assets[source.name]
    clip = _visual_clip("first", asset, "video", 0, 1000, 0, 1000).model_copy(
        update={"transition": TransitionRef(transition_id="fade", duration_ticks=100)}
    )
    timeline = _timeline([asset], [clip], 1000)
    with pytest.raises(EditorRenderError, match="incoming transition requires an adjacent preceding clip"):
        render_editor_timeline(timeline, library, tmp_path / "bad-boundary.mp4")


def test_invalid_timeline_is_rejected_before_render(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    source = _video(tmp_path / "source" / "one.mp4")
    library, assets = _project(tmp_path, source)
    asset = assets[source.name]
    timeline = _timeline([asset], [_visual_clip("one", asset, "video", 0, 2000, 0, 1000)], 1000)
    with pytest.raises(EditorRenderError, match="timeline validation failed"):
        render_editor_timeline(timeline, library, tmp_path / "invalid.mp4")


def test_missing_asset_reference_is_rejected(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    library = ProjectAssetLibrary.create(tmp_path / "project", project_id="renderer-test")
    missing = MediaAsset(asset_id="missing", kind="video", uri="missing.mp4", duration_ms=1000)
    timeline = _timeline([missing], [_visual_clip("clip", missing, "video", 0, 1000, 0, 1000)], 1000)
    with pytest.raises(EditorRenderError, match="not present in project asset library"):
        render_editor_timeline(timeline, library, tmp_path / "missing.mp4")


def test_text_layout_is_rejected_until_a_graphics_renderer_exists(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    library = ProjectAssetLibrary.create(tmp_path / "project", project_id="renderer-test")
    text = MediaAsset(asset_id="text", kind="text", text="Hello")
    timeline = _timeline([text], [_visual_clip("text", text, "overlay", 0, 1000)], 1000)
    with pytest.raises(EditorRenderError, match="text assets are not renderable"):
        render_editor_timeline(timeline, library, tmp_path / "text.mp4")


def test_legacy_renderer_path_remains_separate():
    from poc.timeline import Timeline
    legacy = Timeline.load(Path(__file__).resolve().parent.parent / "examples" / "hand_timeline.json")
    assert legacy.schema_version == "0.1"
    assert len(legacy.clips) == 5


def test_repeated_render_has_deterministic_plan_and_decoded_frames(tmp_path):
    from poc.execute.editor_render import (
        EditorRenderError,
        build_editor_render_plan,
        render_editor_timeline,
    )
    source = _video(tmp_path / "source" / "one.mp4")
    library, assets = _project(tmp_path, source)
    asset = assets[source.name]
    timeline = _timeline([asset], [_visual_clip("one", asset, "video", 0, 1000, 0, 1000)], 1000)
    plan_a = build_editor_render_plan(timeline, library)
    plan_b = build_editor_render_plan(timeline, library)
    assert plan_a == plan_b
    first = render_editor_timeline(timeline, library, tmp_path / "first.mp4")
    second = render_editor_timeline(timeline, library, tmp_path / "second.mp4")
    assert first.duration_frames == second.duration_frames
    assert hashlib.sha256(first.out_path.read_bytes()).digest() == hashlib.sha256(second.out_path.read_bytes()).digest()
