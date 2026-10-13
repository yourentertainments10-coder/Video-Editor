"""Adapters between the frozen v0.1 cutter timeline and the editor timeline."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from poc.project_assets import ProjectAssetLibrary
from poc.timeline import (
    AudioProperties, EditorClip, EditorSettings, EditorTimeline, Layer, TimeBase,
    Timeline, TimelineMarker, Track, frame_to_ms,
)


def legacy_to_editor(
    legacy: Timeline,
    *,
    library: ProjectAssetLibrary,
    source_to_asset_id: Mapping[str | Path, str],
    music_asset_id: str,
    settings: EditorSettings,
    timeline_id: str | None = None,
) -> EditorTimeline:
    """Convert one legacy cut into the standard editor project tracks."""
    lookup = {str(key): value for key, value in source_to_asset_id.items()}

    def find_asset(source: str) -> str:
        direct = lookup.get(source)
        if direct is not None:
            return direct
        resolved = str(Path(source).resolve(strict=False))
        for key, value in lookup.items():
            if str(Path(key).resolve(strict=False)) == resolved:
                return value
        raise KeyError(f"no project asset is mapped for legacy source {source!r}")

    fps = legacy.settings.fps
    clips = [
        EditorClip(
            clip_id=clip.id,
            asset_id=find_asset(clip.source.path),
            track_id="video",
            start_tick=frame_to_ms(clip.start_frame, fps),
            duration_ticks=frame_to_ms(clip.end_frame, fps) - frame_to_ms(clip.start_frame, fps),
            source_in_ms=clip.source.in_ms,
            source_out_ms=clip.source_out_ms(fps),
            score=clip.score,
            rationale=clip.rationale,
        )
        for clip in legacy.clips
    ]
    clips.append(EditorClip(
        clip_id="auto_music",
        asset_id=music_asset_id,
        track_id="music",
        start_tick=0,
        duration_ticks=legacy.duration_ms,
        source_in_ms=legacy.music.in_ms,
        source_out_ms=legacy.music.in_ms + legacy.duration_ms,
        audio=AudioProperties(gain_db=legacy.music.gain_db, fade_out_ms=legacy.music.fade_out_ms),
    ))
    markers = [
        TimelineMarker(
            marker_id=f"beat_{index:04d}", kind="beat",
            position_tick=frame_to_ms(beat.frame, fps), value=beat.energy,
            label="downbeat" if beat.downbeat else None,
        )
        for index, beat in enumerate(legacy.beats)
    ]
    assets = [item.media_asset for item in library.project.assets]
    return EditorTimeline(
        project_id=library.project.project_id,
        timeline_id=timeline_id or "timeline_auto_edit",
        duration_ticks=legacy.duration_ms,
        time_base=TimeBase(ticks_per_second=1000),
        settings=settings,
        music_asset_id=music_asset_id,
        music_track_id="music",
        assets=assets,
        layers=[Layer(layer_id="visual", name="Visual", order=0),
                Layer(layer_id="audio", name="Audio", order=1)],
        tracks=[Track(track_id="video", kind="video", layer_id="visual", name="Video"),
                Track(track_id="overlay", kind="overlay", layer_id="visual", name="Overlay"),
                Track(track_id="voice", kind="audio", layer_id="audio", name="Voice"),
                Track(track_id="music", kind="music", layer_id="audio", name="Music")],
        clips=clips,
        markers=markers,
        provenance=legacy.provenance,
    )
