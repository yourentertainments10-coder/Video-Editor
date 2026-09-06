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

from poc.timeline.schema import Beat, Clip, Timeline

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
