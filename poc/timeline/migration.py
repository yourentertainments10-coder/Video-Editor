"""Explicit migration dispatch for legacy MVP 0.1 and editor 1.0 timelines."""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from poc.timeline.schema import (
    AudioProperties,
    EditorClip,
    EditorSettings,
    EditorTimeline,
    Layer,
    MediaAsset,
    TimeBase,
    Timeline,
    TimelineMarker,
    Track,
    frame_to_ms,
)

LEGACY_SCHEMA_VERSION = "0.1"
CURRENT_SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = (LEGACY_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION)


class TimelineMigrationError(ValueError):
    """A source timeline is malformed or its schema version is unsupported."""


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TimelineMigrationError",
    "migrate_legacy_timeline",
    "migrate_timeline",
]


def _asset_id(kind: str, uri: str) -> str:
    normalized_uri = PurePosixPath(uri.replace("\\", "/")).as_posix()
    key = f"{kind}\0{normalized_uri}".encode("utf-8")
    return f"asset_{hashlib.sha256(key).hexdigest()[:20]}"


def migrate_legacy_timeline(
    legacy: Timeline,
    *,
    project_id: str | None = None,
    timeline_id: str | None = None,
) -> EditorTimeline:
    """Convert v0.1 without changing the legacy model or its consumers.

    Asset IDs are deterministic for ``(media kind, normalized URI)`` and
    legacy clip IDs are retained. Project and timeline IDs are generated
    once unless supplied by the caller and become stable when persisted.
    """
    document_hash = hashlib.sha256(legacy.model_dump_json().encode("utf-8")).hexdigest()
    project_id = project_id or f"project_{document_hash[:24]}"
    timeline_id = timeline_id or f"timeline_{document_hash[24:48]}"
    prefix = hashlib.sha256(timeline_id.encode("utf-8")).hexdigest()[:12]
    video_layer_id = f"migration_{prefix}_layer_video"
    audio_layer_id = f"migration_{prefix}_layer_audio"
    video_track_id = f"migration_{prefix}_track_video"
    music_track_id = f"migration_{prefix}_track_music"

    asset_by_key: dict[tuple[str, str], MediaAsset] = {}

    def add_asset(kind: str, uri: str) -> MediaAsset:
        normalized_uri = PurePosixPath(uri.replace("\\", "/")).as_posix()
        key = (kind, normalized_uri)
        if key not in asset_by_key:
            asset_by_key[key] = MediaAsset(
                asset_id=_asset_id(kind, normalized_uri),
                kind=kind,  # type: ignore[arg-type]
                uri=normalized_uri,
            )
        return asset_by_key[key]

    editor_clips: list[EditorClip] = []
    for clip in legacy.clips:
        asset = add_asset("video", clip.source.path)
        start_tick = frame_to_ms(clip.start_frame, legacy.settings.fps)
        end_tick = frame_to_ms(clip.end_frame, legacy.settings.fps)
        editor_clips.append(EditorClip(
            clip_id=clip.id,
            asset_id=asset.asset_id,
            track_id=video_track_id,
            start_tick=start_tick,
            duration_ticks=end_tick - start_tick,
            source_in_ms=clip.source.in_ms,
            source_out_ms=clip.source_out_ms(legacy.settings.fps),
            score=clip.score,
            rationale=clip.rationale,
        ))

    music_asset = add_asset("music", legacy.music.path)
    editor_clips.append(EditorClip(
        clip_id=f"migration_{prefix}_music_clip",
        asset_id=music_asset.asset_id,
        track_id=music_track_id,
        start_tick=0,
        duration_ticks=legacy.duration_ms,
        source_in_ms=legacy.music.in_ms,
        source_out_ms=legacy.music.in_ms + legacy.duration_ms,
        audio=AudioProperties(gain_db=legacy.music.gain_db, fade_out_ms=legacy.music.fade_out_ms),
    ))

    markers = [
        TimelineMarker(
            marker_id=f"migration_{prefix}_beat_{index:03d}",
            kind="beat",
            position_tick=frame_to_ms(beat.frame, legacy.settings.fps),
            label="downbeat" if beat.downbeat else None,
            value=beat.energy,
        )
        for index, beat in enumerate(legacy.beats)
    ]

    return EditorTimeline(
        project_id=project_id,
        timeline_id=timeline_id,
        duration_ticks=legacy.duration_ms,
        time_base=TimeBase(ticks_per_second=1000),
        settings=EditorSettings(
            fps_num=legacy.settings.fps,
            fps_den=1,
            width=legacy.settings.width,
            height=legacy.settings.height,
        ),
        music_asset_id=music_asset.asset_id,
        music_track_id=music_track_id,
        assets=list(asset_by_key.values()),
        layers=[
            Layer(layer_id=video_layer_id, name="Video", order=0),
            Layer(layer_id=audio_layer_id, name="Audio", order=1),
        ],
        tracks=[
            Track(track_id=video_track_id, kind="video", layer_id=video_layer_id, name="Video"),
            Track(track_id=music_track_id, kind="music", layer_id=audio_layer_id, name="Music"),
        ],
        clips=editor_clips,
        markers=markers,
        provenance=legacy.provenance,
    )


def _schema_version(source: Timeline | EditorTimeline | Mapping[str, Any]) -> str:
    if isinstance(source, (Timeline, EditorTimeline)):
        version = source.schema_version
    elif isinstance(source, Mapping):
        version = source.get("schema_version")
    else:
        raise TimelineMigrationError(
            "schema_version is unavailable: expected a timeline model or mapping"
        )

    if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+", version) is None:
        raise TimelineMigrationError(
            f"schema_version is missing or malformed: {version!r}; expected 'major.minor'"
        )
    if version in SUPPORTED_SCHEMA_VERSIONS:
        return version

    major_minor = tuple(int(part) for part in version.split("."))
    current_major_minor = tuple(int(part) for part in CURRENT_SCHEMA_VERSION.split("."))
    direction = "future" if major_minor > current_major_minor else "older"
    raise TimelineMigrationError(
        f"unsupported {direction} schema_version {version!r}; supported versions are "
        f"{', '.join(SUPPORTED_SCHEMA_VERSIONS)}"
    )


def migrate_timeline(
    source: Timeline | EditorTimeline | Mapping[str, Any],
) -> EditorTimeline:
    """Dispatch an input document to the current v1.0 schema.

    Supported paths are legacy v0.1 -> v1.0 via ``migrate_legacy_timeline``
    and v1.0 -> v1.0 via validation/canonicalization into a fresh model.
    No implicit downgrade or best-effort parsing is performed.
    """
    version = _schema_version(source)
    if version == LEGACY_SCHEMA_VERSION:
        try:
            legacy = source if isinstance(source, Timeline) else Timeline.model_validate(source)
        except ValidationError as error:
            raise TimelineMigrationError(f"invalid v0.1 timeline: {error}") from error
        return migrate_legacy_timeline(legacy)

    try:
        current = (
            source if isinstance(source, EditorTimeline)
            else EditorTimeline.model_validate(source)
        )
        # Canonicalization returns a fresh strict model and never mutates the source.
        return EditorTimeline.model_validate(current.model_dump(mode="python"))
    except ValidationError as error:
        raise TimelineMigrationError(f"invalid v1.0 timeline: {error}") from error