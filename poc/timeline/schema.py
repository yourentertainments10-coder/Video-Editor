"""poc/timeline/schema.py: Timeline Document v0 -- schema only.

The Timeline Document is the single contract shared by every other module:
the cutter and baseline (Step 7) will write it, the renderer (Step 4) and
contact sheet will read it, the evaluator (Step 9) will compare two of them.
This module defines *what a Timeline is*. It does not check cross-field
invariants (contiguity, on-beat alignment, source-range bounds, ...) -- that
is the Timeline Validator, built separately in Step 3 as
``poc/timeline/validate.py``. This file only enforces per-field constraints
that Pydantic can check in isolation (types, ranges, required-ness).

Schema is exactly the v0.1 shape defined in docs/MVP.md Section 9, which maps
onto the full v1 schema in docs/ARCHITECTURE.md Section 12.2 by wrapping
``clips`` into ``tracks[0].clips``, adding a constant ``speed: {kind:
"constant", rate: 1.0}``, and converting frame indices to milliseconds using
``settings.fps``. No information is lost in either direction, so nothing here
should be considered a permanent format -- it is the smallest correct subset
of the eventual one.

Time bases (docs/MVP.md Section 9 and docs/ARCHITECTURE_REVIEW.md item 2):
  timeline positions  -> integer frames at settings.fps (exact grid equality,
                         no millisecond drift on grid snapping)
  source positions    -> integer milliseconds (source files have their own
                         clocks; FFmpeg seeks in seconds)
Speed is always 1.0 in v0, so a clip's source out-point is implied by its
frame length and is not stored separately.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TIMELINE_FPS = 30


def ms_to_frame(ms: float, fps: int = TIMELINE_FPS) -> int:
    """Convert a millisecond position to the nearest timeline frame."""
    return int(round(ms * fps / 1000.0))


def frame_to_ms(frame: int, fps: int = TIMELINE_FPS) -> int:
    """Convert a timeline frame index to milliseconds."""
    return int(round(frame * 1000.0 / fps))


def frames_to_seconds(frames: int, fps: int = TIMELINE_FPS) -> float:
    """Convert a frame count to seconds (for FFmpeg's ``-t``/``-ss``)."""
    return frames / fps


class _Strict(BaseModel):
    """Shared base: rejects any field not explicitly declared below.

    Not present in the docs/MVP.md reference snippet -- called out explicitly
    per the "flag assumptions" instruction rather than applied silently. See
    the ambiguity note in the handoff message for the reasoning and how to
    revert it if unwanted.
    """

    model_config = ConfigDict(extra="forbid")


class Settings(_Strict):
    """Output frame rate and frame size. One video track, one resolution."""

    fps: int = TIMELINE_FPS
    width: int = 1920
    height: int = 1080


class Music(_Strict):
    """The single music track and the window of it used by this timeline.

    ``in_ms`` is the position in the track that lines up with timeline frame
    0. There is no ``out_ms``: the window length is implied by the timeline's
    ``duration_frames`` (v0 has one track, one tempo, one window -- see
    docs/MVP.md Section 9, which omits ``out_ms`` deliberately).
    """

    path: str
    in_ms: int = 0
    gain_db: float = -1.0
    fade_out_ms: int = 1000


class Beat(_Strict):
    """One informational grid marker. Beats are not clip boundaries by
    themselves; the validator's ON_BEAT check (Step 3) is what ties clip
    starts to this list.
    """

    frame: int
    downbeat: bool = False
    energy: float = Field(ge=0.0, le=1.0)


class Source(_Strict):
    """A footage file and the millisecond offset a clip starts reading from."""

    path: str
    in_ms: int = Field(ge=0)


class Clip(_Strict):
    """One contiguous segment of the output, sourced from one footage file.

    ``score`` and ``rationale`` are optional and populated by the cutter
    (Step 7); a hand-written or baseline clip may omit them.
    """

    id: str
    start_frame: int = Field(ge=0)
    end_frame: int  # exclusive
    source: Source
    score: float | None = None
    rationale: dict[str, float] = Field(default_factory=dict)

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    def source_out_ms(self, fps: int = TIMELINE_FPS) -> int:
        """The source-file millisecond position this clip reads up to."""
        return self.source.in_ms + frame_to_ms(self.frames, fps)


class Provenance(_Strict):
    """Which generator produced this timeline, and with what parameters.

    This is how an evaluator (Step 9) tells the scored cutter output apart
    from the random baseline without trusting the output filename.
    """

    generator: Literal["hand", "baseline-v0", "cutter-v0"] = "hand"
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class Timeline(_Strict):
    """The Timeline Document: the canonical, versioned source of truth.

    Nothing about the rendered video is authoritative; this document is.
    """

    schema_version: Literal["0.1"] = "0.1"
    settings: Settings = Field(default_factory=Settings)
    duration_frames: int = Field(gt=0)
    music: Music
    beats: list[Beat] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    # ---- persistence --------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Timeline":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        """Write canonical, indented, newline-terminated JSON.

        Field order follows model declaration order, which Pydantic v2 keeps
        stable across calls and across processes for a given class
        definition -- this is what "deterministic serialization" means here
        (see the ambiguity note: this is not byte-for-byte canonicalization
        such as key sorting, which nothing in the docs asks for).
        """
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    # ---- convenience ----------------------------------------------------

    @property
    def duration_ms(self) -> int:
        return frame_to_ms(self.duration_frames, self.settings.fps)

    def sorted_clips(self) -> list[Clip]:
        return sorted(self.clips, key=lambda c: c.start_frame)
