"""poc.execute: turns a validated Timeline into a rendered MP4 (FFmpeg).

The only module here is ``render``. No transitions, speed ramps, effects,
captions, caching, parallel rendering, or GPU use -- see poc/execute/render.py
for the full list of what v0 deliberately does not do.
"""

from poc.execute.contact_sheet import ContactSheetError, generate_contact_sheet
from poc.execute.render import (
    FFmpegError,
    MediaProbe,
    MediaProbeError,
    RenderResult,
    TimelineInvalidError,
    probe,
    probe_duration_ms,
    render,
    render_from_file,
    resolve_media_path,
)

__all__ = [
    "FFmpegError",
    "ContactSheetError",
    "MediaProbe",
    "MediaProbeError",
    "RenderResult",
    "TimelineInvalidError",
    "probe",
    "probe_duration_ms",
    "render",
    "render_from_file",
    "resolve_media_path",
    "generate_contact_sheet",
]
