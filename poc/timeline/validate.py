"""poc/timeline/validate.py: Timeline Document v0 -- cross-object invariants.

``schema.py`` (Step 2) owns structural and per-field validity: types, ranges,
required-ness, checkable on one field or one model in isolation. This module
owns everything that requires comparing clips, beats, and sources against
each other and against the timeline as a whole -- the seven invariants listed
in docs/MVP.md Section 9. Nothing here raises on failure: ``validate()``
always returns, and an empty list means the timeline is renderable. This is
what lets the cutter (Step 7) run validation on its own candidate timelines
without wrapping every attempt in a try/except, and what lets the renderer
(Step 4) refuse to render with a precise, machine-readable reason instead of
failing partway through an FFmpeg invocation.

``SOURCE_RANGE`` and ``MUSIC_RANGE`` need real media durations (from
ffprobe), which this module does not compute -- ``source_durations_ms`` and
``music_duration_ms`` are optional parameters precisely so a timeline can be
checked structurally, with no filesystem or media access, before those
numbers exist. Per the path-semantics decision: any dict of durations passed
in here is keyed by the same relative-POSIX path strings stored in the
Timeline document; resolving those paths against the timeline file's parent
directory is the caller's job (Step 4+), not this module's.
"""
from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel

from poc.timeline.schema import Beat, Clip, EditorTimeline, Timeline

MIN_CLIP_FRAMES = 6  # 200 ms at 30 fps
ON_BEAT_TOLERANCE_FRAMES = 1

ViolationCode = Literal[
    "CONTIGUOUS",
    "MIN_LENGTH",
    "ON_BEAT",
    "SOURCE_RANGE",
    "NO_SOURCE_REUSE",
    "UNIQUE_IDS",
    "MUSIC_RANGE",
    "DUPLICATE_ID",
    "UNKNOWN_LAYER",
    "UNKNOWN_TRACK",
    "UNKNOWN_ASSET",
    "ASSET_KIND",
    "TIMELINE_RANGE",
    "SOURCE_REQUIRED",
    "TRANSITION_RANGE",
    "TRANSITION_BOUNDARY",
    "MUSIC_REFERENCE",
]


class Violation(BaseModel):
    code: ViolationCode
    clip_id: str | None = None
    detail: str

    def __str__(self) -> str:
        where = f" [{self.clip_id}]" if self.clip_id else ""
        return f"{self.code}{where}: {self.detail}"


def validate(
    tl: Timeline,
    source_durations_ms: dict[str, int] | None = None,
    music_duration_ms: int | None = None,
) -> list[Violation]:
    """Return every invariant violation. Empty list means the timeline is renderable.

    ``source_durations_ms`` and ``music_duration_ms`` are optional so a
    timeline can be checked structurally without probing media; pass them
    (from ffprobe) for the full check. Order of checks below is fixed
    (unique ids, then contiguity, length, beat alignment, source range,
    source reuse, music range) so output is stable across runs for the same
    input, which matters for diffing validator output in tests and logs.
    """
    v: list[Violation] = []
    fps = tl.settings.fps
    clips = tl.sorted_clips()

    v.extend(_check_unique_ids(clips))
    v.extend(_check_contiguous(clips, tl.duration_frames))
    v.extend(_check_min_length(clips))
    v.extend(_check_on_beat(clips, tl.beats))
    v.extend(_check_source_range(clips, fps, source_durations_ms))
    v.extend(_check_no_source_reuse(clips, fps))
    v.extend(_check_music_range(tl, music_duration_ms))

    return v


def validate_editor_timeline(timeline: EditorTimeline) -> list[Violation]:
    """Validate v2 editor references and bounds without rendering semantics.

    Multiple tracks may overlap by design. This validator therefore checks
    stable identities, references, asset/source compatibility, and timeline
    bounds, but does not impose the legacy single-track contiguity rule.
    """
    violations: list[Violation] = []
    entities = [
        (asset.asset_id, "asset", None) for asset in timeline.assets
    ] + [
        (layer.layer_id, "layer", None) for layer in timeline.layers
    ] + [
        (track.track_id, "track", None) for track in timeline.tracks
    ] + [
        (clip.clip_id, "clip", clip.clip_id) for clip in timeline.clips
    ] + [
        (marker.marker_id, "marker", None) for marker in timeline.markers
    ]
    counts = Counter(entity_id for entity_id, _, _ in entities)
    for entity_id, entity_type, clip_id in entities:
        if counts[entity_id] > 1 and not any(
            violation.code == "DUPLICATE_ID" and violation.detail.startswith(f"duplicate id {entity_id!r}")
            for violation in violations
        ):
            violations.append(Violation(
                code="DUPLICATE_ID",
                clip_id=clip_id,
                detail=f"duplicate id {entity_id!r} across timeline entities (including {entity_type})",
            ))

    layers_by_id = {layer.layer_id: layer for layer in timeline.layers}
    tracks_by_id = {track.track_id: track for track in timeline.tracks}
    assets_by_id = {asset.asset_id: asset for asset in timeline.assets}

    for track in timeline.tracks:
        if track.layer_id not in layers_by_id:
            violations.append(Violation(
                code="UNKNOWN_LAYER",
                detail=f"track {track.track_id!r} references missing layer {track.layer_id!r}",
            ))

    if (timeline.music_asset_id is None) != (timeline.music_track_id is None):
        violations.append(Violation(
            code="MUSIC_REFERENCE", detail="music_asset_id and music_track_id must be set together"
        ))
    if timeline.music_asset_id is not None:
        music_asset = assets_by_id.get(timeline.music_asset_id)
        music_track = tracks_by_id.get(timeline.music_track_id or "")
        if music_asset is None or music_asset.kind not in {"music", "audio"}:
            violations.append(Violation(
                code="MUSIC_REFERENCE",
                detail=f"music asset {timeline.music_asset_id!r} is missing or is not audio/music",
            ))
        if music_track is None or music_track.kind != "music":
            violations.append(Violation(
                code="MUSIC_REFERENCE",
                detail=f"music track {timeline.music_track_id!r} is missing or is not a music track",
            ))

    allowed_asset_kinds = {
        "video": {"video"},
        "overlay": {"video", "image", "text"},
        "audio": {"audio"},
        "music": {"audio", "music"},
    }
    source_kinds = {"video", "audio", "music"}
    clips_by_track: dict[str, list] = {}
    for item in timeline.clips:
        clips_by_track.setdefault(item.track_id, []).append(item)
    for track_clips in clips_by_track.values():
        track_clips.sort(key=lambda item: (item.start_tick, item.clip_id))

    for clip in timeline.clips:
        if clip.start_tick + clip.duration_ticks > timeline.duration_ticks:
            violations.append(Violation(
                code="TIMELINE_RANGE",
                clip_id=clip.clip_id,
                detail=(
                    f"clip ends at tick {clip.start_tick + clip.duration_ticks}, "
                    f"after timeline duration {timeline.duration_ticks}"
                ),
            ))

        track = tracks_by_id.get(clip.track_id)
        asset = assets_by_id.get(clip.asset_id)
        if track is None:
            violations.append(Violation(
                code="UNKNOWN_TRACK",
                clip_id=clip.clip_id,
                detail=f"clip references missing track {clip.track_id!r}",
            ))
        if asset is None:
            violations.append(Violation(
                code="UNKNOWN_ASSET",
                clip_id=clip.clip_id,
                detail=f"clip references missing asset {clip.asset_id!r}",
            ))
        if track is not None and asset is not None:
            if asset.kind not in allowed_asset_kinds[track.kind]:
                violations.append(Violation(
                    code="ASSET_KIND",
                    clip_id=clip.clip_id,
                    detail=f"asset kind {asset.kind!r} is not valid on {track.kind!r} track",
                ))
            if asset.kind in source_kinds:
                if clip.source_in_ms is None or clip.source_out_ms is None:
                    violations.append(Violation(
                        code="SOURCE_REQUIRED",
                        clip_id=clip.clip_id,
                        detail=f"{asset.kind} clips require source_in_ms and source_out_ms",
                    ))
                elif clip.source_out_ms <= clip.source_in_ms:
                    violations.append(Violation(
                        code="SOURCE_RANGE",
                        clip_id=clip.clip_id,
                        detail="source_out_ms must be greater than source_in_ms",
                    ))
                elif asset.duration_ms is not None and clip.source_out_ms > asset.duration_ms:
                    violations.append(Violation(
                        code="SOURCE_RANGE",
                        clip_id=clip.clip_id,
                        detail=(
                            f"source out {clip.source_out_ms} ms > asset duration "
                            f"{asset.duration_ms} ms"
                        ),
                    ))
            elif clip.source_in_ms is not None or clip.source_out_ms is not None:
                violations.append(Violation(
                    code="SOURCE_RANGE",
                    clip_id=clip.clip_id,
                    detail=f"{asset.kind} clips do not use a source time range",
                ))

        if clip.transition is not None:
            preceding = [
                previous for previous in clips_by_track.get(clip.track_id, [])
                if previous.clip_id != clip.clip_id
                and previous.start_tick + previous.duration_ticks == clip.start_tick
            ]
            if not preceding:
                violations.append(Violation(
                    code="TRANSITION_BOUNDARY",
                    clip_id=clip.clip_id,
                    detail="incoming transition requires an adjacent preceding clip on the same track",
                ))
                continue
            if clip.transition.transition_id == "cut":
                continue
            previous = max(preceding, key=lambda item: (item.start_tick, item.clip_id))
            maximum_duration = int(0.6 * min(previous.duration_ticks, clip.duration_ticks))
            if clip.transition.duration_ticks > maximum_duration:
                violations.append(Violation(
                    code="TRANSITION_RANGE",
                    clip_id=clip.clip_id,
                    detail=(
                        f"transition duration {clip.transition.duration_ticks} ticks exceeds 60% "
                        f"of the shorter adjacent clip ({maximum_duration} ticks)"
                    ),
                ))

    for marker in timeline.markers:
        if marker.position_tick > timeline.duration_ticks:
            violations.append(Violation(
                code="TIMELINE_RANGE",
                detail=(
                    f"marker {marker.marker_id!r} is at tick {marker.position_tick}, "
                    f"after timeline duration {timeline.duration_ticks}"
                ),
            ))

    return violations


def _check_unique_ids(clips: list[Clip]) -> list[Violation]:
    counts = Counter(c.id for c in clips)
    duplicates = sorted(clip_id for clip_id, n in counts.items() if n > 1)
    return [
        Violation(code="UNIQUE_IDS", clip_id=clip_id, detail="duplicate clip id")
        for clip_id in duplicates
    ]


def _check_contiguous(clips: list[Clip], duration_frames: int) -> list[Violation]:
    v: list[Violation] = []
    if not clips:
        v.append(Violation(code="CONTIGUOUS", detail="timeline has no clips"))
        return v

    if clips[0].start_frame != 0:
        v.append(
            Violation(
                code="CONTIGUOUS",
                clip_id=clips[0].id,
                detail=f"first clip starts at {clips[0].start_frame}, expected 0",
            )
        )
    for prev, cur in zip(clips, clips[1:]):
        if cur.start_frame != prev.end_frame:
            kind = "gap" if cur.start_frame > prev.end_frame else "overlap"
            v.append(
                Violation(
                    code="CONTIGUOUS",
                    clip_id=cur.id,
                    detail=(
                        f"{kind}: {prev.id} ends {prev.end_frame}, "
                        f"{cur.id} starts {cur.start_frame}"
                    ),
                )
            )
    if clips[-1].end_frame != duration_frames:
        v.append(
            Violation(
                code="CONTIGUOUS",
                clip_id=clips[-1].id,
                detail=(
                    f"last clip ends {clips[-1].end_frame}, "
                    f"expected duration_frames {duration_frames}"
                ),
            )
        )
    return v


def _check_min_length(clips: list[Clip]) -> list[Violation]:
    return [
        Violation(code="MIN_LENGTH", clip_id=c.id, detail=f"{c.frames} frames < {MIN_CLIP_FRAMES}")
        for c in clips
        if c.frames < MIN_CLIP_FRAMES
    ]


def _check_on_beat(clips: list[Clip], beats: list[Beat]) -> list[Violation]:
    # Frame 0 is on the grid by construction and is exempt. If there are no
    # beats at all, alignment cannot be checked and nothing is reported here
    # (an empty beat list is not itself an ON_BEAT violation).
    beat_frames = sorted(b.frame for b in beats)
    if not beat_frames:
        return []
    v: list[Violation] = []
    for c in clips:
        if c.start_frame == 0:
            continue
        nearest = min(beat_frames, key=lambda b: abs(b - c.start_frame))
        distance = abs(nearest - c.start_frame)
        if distance > ON_BEAT_TOLERANCE_FRAMES:
            v.append(
                Violation(
                    code="ON_BEAT",
                    clip_id=c.id,
                    detail=(
                        f"start {c.start_frame} is {distance} frames "
                        f"from nearest beat {nearest}"
                    ),
                )
            )
    return v


def _check_source_range(
    clips: list[Clip], fps: int, source_durations_ms: dict[str, int] | None
) -> list[Violation]:
    if source_durations_ms is None:
        return []
    v: list[Violation] = []
    for c in clips:
        dur = source_durations_ms.get(c.source.path)
        if dur is None:
            v.append(
                Violation(code="SOURCE_RANGE", clip_id=c.id, detail=f"unknown source {c.source.path}")
            )
        elif c.source_out_ms(fps) > dur:
            v.append(
                Violation(
                    code="SOURCE_RANGE",
                    clip_id=c.id,
                    detail=f"source out {c.source_out_ms(fps)} ms > duration {dur} ms",
                )
            )
    return v


def _check_no_source_reuse(clips: list[Clip], fps: int) -> list[Violation]:
    v: list[Violation] = []
    by_path: dict[str, list[Clip]] = {}
    for c in clips:
        by_path.setdefault(c.source.path, []).append(c)
    for path, group in by_path.items():
        group = sorted(group, key=lambda c: c.source.in_ms)
        for a, b in zip(group, group[1:]):
            if b.source.in_ms < a.source_out_ms(fps):
                v.append(Violation(code="NO_SOURCE_REUSE", clip_id=b.id, detail=f"overlaps {a.id} in {path}"))
    return v


def _check_music_range(tl: Timeline, music_duration_ms: int | None) -> list[Violation]:
    if music_duration_ms is None:
        return []
    if tl.music.in_ms + tl.duration_ms > music_duration_ms:
        return [
            Violation(
                code="MUSIC_RANGE",
                detail=(
                    f"window {tl.music.in_ms}+{tl.duration_ms} ms exceeds "
                    f"track {music_duration_ms} ms"
                ),
            )
        ]
    return []
