"""P2.2 tests for explicit schema-version migration policy."""
from __future__ import annotations

from pathlib import Path

import pytest

from poc.timeline.migration import (
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TimelineMigrationError,
    migrate_timeline,
)
from poc.timeline.schema import EditorTimeline, Timeline

HAND_TIMELINE = Path(__file__).resolve().parent.parent / "examples" / "hand_timeline.json"


def _legacy() -> Timeline:
    return Timeline.load(HAND_TIMELINE)


def test_v01_dispatches_to_v10():
    migrated = migrate_timeline(_legacy())
    assert migrated.schema_version == "1.0"
    assert CURRENT_SCHEMA_VERSION == "1.0"
    assert "0.1" in SUPPORTED_SCHEMA_VERSIONS
    assert "1.0" in SUPPORTED_SCHEMA_VERSIONS


def test_v10_uses_explicit_identity_canonicalization_path():
    current = migrate_timeline(_legacy())
    result = migrate_timeline(current)
    assert result == current
    assert result is not current


@pytest.mark.parametrize("version", ["0.0", "0.2"])
def test_unsupported_older_versions_fail_clearly(version):
    with pytest.raises(TimelineMigrationError, match="unsupported older"):
        migrate_timeline({"schema_version": version})


@pytest.mark.parametrize("version", ["1.1", "2.0"])
def test_unsupported_future_versions_are_never_downgraded(version):
    with pytest.raises(TimelineMigrationError, match="unsupported future"):
        migrate_timeline({"schema_version": version})


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"schema_version": None},
        {"schema_version": ""},
        {"schema_version": "v1"},
        {"schema_version": 1},
    ],
)
def test_missing_or_malformed_schema_versions_fail_clearly(document):
    with pytest.raises(TimelineMigrationError, match="schema_version"):
        migrate_timeline(document)


def test_repeated_v01_migration_is_deterministic():
    legacy = _legacy()
    first = migrate_timeline(legacy)
    second = migrate_timeline(legacy)
    assert first == second
    assert first.project_id == second.project_id
    assert first.timeline_id == second.timeline_id


def test_legacy_clip_and_asset_ids_are_preserved_or_deterministic():
    migrated = migrate_timeline(_legacy())
    legacy = _legacy()
    video_clips = {clip.clip_id: clip for clip in migrated.clips if clip.track_id != migrated.music_track_id}
    assert set(video_clips) == {clip.id for clip in legacy.clips}
    migrated_again = migrate_timeline(legacy)
    assert {asset.asset_id for asset in migrated.assets} == {
        asset.asset_id for asset in migrated_again.assets
    }


def test_migration_preserves_clip_source_timing_provenance_and_beats():
    legacy = _legacy()
    migrated = migrate_timeline(legacy)
    video_clips = {clip.clip_id: clip for clip in migrated.clips if clip.track_id != migrated.music_track_id}
    for clip in legacy.clips:
        result = video_clips[clip.id]
        assert result.source_in_ms == clip.source.in_ms
        assert result.source_out_ms == clip.source_out_ms(legacy.settings.fps)
        assert result.start_tick == round(clip.start_frame * 1000 / legacy.settings.fps)
        assert result.duration_ticks == round(clip.frames * 1000 / legacy.settings.fps)
        assert result.score == clip.score
        assert result.rationale == clip.rationale
    assert migrated.provenance == legacy.provenance
    assert len(migrated.markers) == len(legacy.beats)
    assert migrated.music_asset_id is not None
    assert migrated.music_track_id is not None


def test_serialized_migration_loads_as_current_schema_version(tmp_path):
    migrated = migrate_timeline(_legacy())
    path = tmp_path / "migrated.json"
    migrated.save(path)
    loaded = EditorTimeline.load(path)
    assert loaded.schema_version == CURRENT_SCHEMA_VERSION
    assert loaded == migrated