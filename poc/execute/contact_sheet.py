"""Generate a diagnostic contact sheet for a rendered Timeline."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from poc.execute.render import resolve_media_path
from poc.timeline.schema import Timeline

__all__ = ["ContactSheetError", "generate_contact_sheet"]

THUMBNAIL_WIDTH = 320
THUMBNAIL_HEIGHT = 180
TEXT_WIDTH = 280
CELL_WIDTH = THUMBNAIL_WIDTH + TEXT_WIDTH + 24
CELL_HEIGHT = THUMBNAIL_HEIGHT + 24
GAP = 12


class ContactSheetError(RuntimeError):
    """A selected clip could not be sampled or the sheet could not be saved."""


def _ascii(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def _fit_frame(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = min(THUMBNAIL_WIDTH / width, THUMBNAIL_HEIGHT / height)
    resized = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
    thumbnail = np.zeros((THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH, 3), dtype=np.uint8)
    y = (THUMBNAIL_HEIGHT - resized.shape[0]) // 2
    x = (THUMBNAIL_WIDTH - resized.shape[1]) // 2
    thumbnail[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return thumbnail


def generate_contact_sheet(
    timeline: Timeline,
    base_dir: str | Path,
    output_path: str | Path,
    *,
    columns: int = 2,
) -> Path:
    """Save one midpoint thumbnail and selection metadata for every clip."""
    if columns < 1:
        raise ValueError("columns must be at least 1")
    clips = timeline.sorted_clips()
    if not clips:
        raise ContactSheetError("cannot create a contact sheet for a timeline with no clips")

    rows = math.ceil(len(clips) / columns)
    sheet = np.full(
        (GAP + rows * (CELL_HEIGHT + GAP), GAP + columns * (CELL_WIDTH + GAP), 3),
        (242, 242, 242),
        dtype=np.uint8,
    )
    captures: dict[str, cv2.VideoCapture] = {}
    try:
        for index, clip in enumerate(clips):
            source_path = resolve_media_path(clip.source.path, Path(base_dir))
            key = str(source_path)
            capture = captures.get(key)
            if capture is None:
                capture = cv2.VideoCapture(key)
                if not capture.isOpened():
                    raise ContactSheetError(f"could not open clip source: {source_path}")
                captures[key] = capture

            sample_ms = clip.source.in_ms + round(clip.frames * 500 / timeline.settings.fps)
            capture.set(cv2.CAP_PROP_POS_MSEC, sample_ms)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ContactSheetError(
                    f"could not read frame at {sample_ms} ms from {source_path} for clip {clip.id}"
                )

            column, row = index % columns, index // columns
            x = GAP + column * (CELL_WIDTH + GAP)
            y = GAP + row * (CELL_HEIGHT + GAP)
            sheet[y:y + CELL_HEIGHT, x:x + CELL_WIDTH] = (255, 255, 255)
            thumbnail = _fit_frame(frame)
            sheet[y + 12:y + 12 + THUMBNAIL_HEIGHT, x + 12:x + 12 + THUMBNAIL_WIDTH] = thumbnail

            text_x = x + 12 + THUMBNAIL_WIDTH + 12
            text_y = y + 34
            start_s = clip.start_frame / timeline.settings.fps
            end_s = clip.end_frame / timeline.settings.fps
            lines = [
                f"Clip {index + 1:02d}  {start_s:.2f}-{end_s:.2f}s",
                f"Source: {Path(clip.source.path).name}",
                f"Score: {clip.score:.3f}" if clip.score is not None else "Score: n/a",
            ]
            lines.extend(
                f"{name}: {value:.3f}" for name, value in sorted(clip.rationale.items())[:3]
            )
            for line in lines:
                cv2.putText(
                    sheet,
                    _ascii(line)[:38],
                    (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    (30, 36, 40),
                    1,
                    cv2.LINE_AA,
                )
                text_y += 25
    finally:
        for capture in captures.values():
            capture.release()

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), sheet):
        raise ContactSheetError(f"could not write contact sheet: {output}")
    return output