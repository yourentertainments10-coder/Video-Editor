"""poc.timeline: the Timeline Document -- the single source of truth.

Step 2 exports the schema. Step 3 will add the validator (``validate``,
``Violation``) to this same namespace.
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
]
