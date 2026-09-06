"""poc.timeline: the Timeline Document -- the single source of truth.

``schema`` (Step 2) owns structural and per-field validity. ``validate``
(Step 3) owns cross-object editing invariants. Both are re-exported here.
"""

from poc.timeline.schema import (
    TIMELINE_FPS,
    Beat,
    Clip,
    Music,
    Provenance,
    Settings,
    Source,
    Timeline,
    frame_to_ms,
    frames_to_seconds,
    ms_to_frame,
)
from poc.timeline.validate import (
    MIN_CLIP_FRAMES,
    ON_BEAT_TOLERANCE_FRAMES,
    Violation,
    ViolationCode,
    validate,
)

__all__ = [
    "TIMELINE_FPS",
    "Beat",
    "Clip",
    "Music",
    "Provenance",
    "Settings",
    "Source",
    "Timeline",
    "frame_to_ms",
    "frames_to_seconds",
    "ms_to_frame",
    "MIN_CLIP_FRAMES",
    "ON_BEAT_TOLERANCE_FRAMES",
    "Violation",
    "ViolationCode",
    "validate",
]
