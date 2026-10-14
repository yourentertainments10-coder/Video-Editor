"""poc/perceive/shots.py: what is in the footage, offline -- faces, shot size, light, shake.

The Local Director needs to answer "what is in clip 3?", "remove the dark clips" or "only
close-ups". This module measures, per moment of a video file, the few things that can be
measured reliably on a laptop with what ships inside OpenCV (no model download):

  * faces    -- OpenCV's frontal + profile Haar cascades, 1 Hz on a 360-px-wide frame;
                count and the size of the biggest face (fraction of the frame area).
  * shot size -- from the biggest face: close-up / medium / wide; unknown without faces.
  * light    -- mean brightness (dark / normal / bright).
  * shake    -- jitter of the camera's frame-to-frame translation (phase correlation,
                4 Hz): a steady pan is smooth, a shaky hand is not.
  * movement -- mean absolute frame difference.

Everything is plain, cached data (``ShotMap``); ``describe`` turns a time range into tags and
``find_segments`` looks for footage that matches wanted tags. Nothing here edits a timeline.
"""
from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterator, Literal

import cv2
import imageio_ffmpeg
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from poc.perceive.cache import load_or_compute

__all__ = [
    "ANALYZER_VERSION", "ShotSample", "ShotMap", "ShotTags", "analyze_shots", "describe",
    "find_segments", "faces_available", "TAG_LABELS", "TAG_SINGULAR",
]

ANALYZER_VERSION = "poc-shots-v2"
SAMPLE_RATE_HZ = 4.0
FACE_EVERY = 4                 # faces on every 4th sample: 1 Hz
ANALYSIS_WIDTH = 360

CLOSE_UP_AREA = 0.035          # biggest face >= 3.5 % of the frame
MEDIUM_AREA = 0.006            # >= 0.6 %; smaller faces mean a wide shot
DARK_LUMA = 60.0
BRIGHT_LUMA = 190.0
SHAKY_JITTER = 0.015           # mean change of frame-to-frame shift, fraction of the width
BUSY_MOTION = 0.07
STILL_MOTION = 0.012


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShotSample(_Strict):
    t_ms: int
    luma: float = Field(ge=0.0, le=255.0)
    motion: float = Field(ge=0.0, le=1.0)
    shift_x: float = 0.0       # camera shift since the previous sample, fraction of the width
    shift_y: float = 0.0
    faces: int | None = None   # None: faces not measured at this sample
    face_area: float = 0.0     # biggest face / frame area
    face_x: float | None = None  # centre of the biggest face, 0..1 across the frame


class ShotMap(_Strict):
    schema_version: Literal["1"] = "1"
    analyzer_version: str = ANALYZER_VERSION
    duration_ms: int
    width: int
    height: int
    faces_measured: bool
    samples: list[ShotSample] = Field(default_factory=list)


class ShotTags(_Strict):
    scale: Literal["close-up", "medium", "wide"] | None = None
    faces: int = 0
    light: Literal["dark", "normal", "bright"] = "normal"
    shaky: bool = False
    movement: Literal["still", "some", "busy"] = "some"
    faces_measured: bool = True

    def labels(self) -> list[str]:
        out = []
        if self.scale:
            out.append({"close-up": "close-up", "medium": "medium shot", "wide": "wide shot"}[self.scale])
        if self.faces_measured:
            out.append("no faces" if self.faces == 0 else "1 face" if self.faces == 1 else f"{self.faces} faces")
        if self.light != "normal":
            out.append(self.light)
        if self.shaky:
            out.append("shaky")
        if self.movement != "some":
            out.append("lots of movement" if self.movement == "busy" else "still")
        return out

    def has(self, tag: str) -> bool:
        return TAG_TESTS[tag](self)


# tag name -> test; the Director's words map onto these names
TAG_TESTS: dict[str, Callable[[ShotTags], bool]] = {
    "close-up": lambda t: t.scale == "close-up",
    "medium": lambda t: t.scale == "medium",
    "wide": lambda t: t.scale == "wide" or (t.faces_measured and t.faces == 0),
    "faces": lambda t: t.faces > 0,
    "no-faces": lambda t: t.faces_measured and t.faces == 0,
    "group": lambda t: t.faces >= 3,
    "dark": lambda t: t.light == "dark",
    "bright": lambda t: t.light == "bright",
    "shaky": lambda t: t.shaky,
    "steady": lambda t: not t.shaky,
    "busy": lambda t: t.movement == "busy",
    "still": lambda t: t.movement == "still",
}
TAG_LABELS = {
    "close-up": "close-ups", "medium": "medium shots", "wide": "wide shots", "faces": "shots with faces",
    "no-faces": "shots without faces", "group": "group shots", "dark": "dark shots", "bright": "bright shots",
    "shaky": "shaky shots", "steady": "steady shots", "busy": "shots with lots of movement", "still": "still shots",
}
TAG_SINGULAR = {
    "close-up": "a close-up", "medium": "a medium shot", "wide": "a wide shot", "faces": "a shot with faces",
    "no-faces": "a shot without faces", "group": "a group shot", "dark": "a dark shot", "bright": "a bright shot",
    "shaky": "a shaky shot", "steady": "a steady shot", "busy": "a shot with lots of movement", "still": "a still shot",
}
NEEDS_FACES = {"close-up", "medium", "wide", "faces", "no-faces", "group"}


# ---------------------------------------------------------------------------- detection

_LOCAL = threading.local()       # cascades are not safe to share between threads: one set each


def _cascade_dir() -> Path | None:
    """Where OpenCV keeps its Haar cascades (also inside the packaged app)."""
    candidates = [Path(cv2.__file__).parent / "data"]
    data = getattr(cv2, "data", None)
    if data is not None and getattr(data, "haarcascades", None):
        candidates.insert(0, Path(data.haarcascades))
    for folder in candidates:
        if (folder / "haarcascade_frontalface_default.xml").is_file():
            return folder
    return None


def faces_available() -> bool:
    """OpenCV 4.x ships the Haar cascades; OpenCV 5 moved them out of the main package."""
    return hasattr(cv2, "CascadeClassifier") and _cascade_dir() is not None


def _cascades() -> list:
    cascades = getattr(_LOCAL, "cascades", None)
    if cascades is None:
        folder = _cascade_dir()
        names = ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")
        loaded = [cv2.CascadeClassifier(str(folder / name)) for name in names] if folder else []
        cascades = _LOCAL.cascades = [c for c in loaded if not c.empty()]
    return cascades


def _faces(gray: np.ndarray, *, profiles: bool = True) -> list[tuple[int, int, int, int]]:
    """Faces in one grayscale frame, de-duplicated. Frontal first; side views (profile and its
    mirror) only when no frontal face was found and ``profiles`` is set -- they cost twice as much."""
    h, w = gray.shape
    min_side = max(14, int(0.035 * w))
    options = dict(scaleFactor=1.15, minNeighbors=5, minSize=(min_side, min_side))
    equalized = cv2.equalizeHist(gray)
    cascades = _cascades()
    boxes = [tuple(int(v) for v in box) for box in cascades[0].detectMultiScale(equalized, **options)]
    if not boxes and profiles and len(cascades) > 1:
        boxes += [tuple(int(v) for v in box) for box in cascades[1].detectMultiScale(equalized, **options)]
        flipped = cascades[1].detectMultiScale(cv2.flip(equalized, 1), **options)
        boxes += [(w - int(x) - int(bw), int(y), int(bw), int(bh)) for x, y, bw, bh in flipped]
    kept: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda b: -b[2] * b[3]):
        if all(_overlap(box, other) < 0.3 for other in kept):
            kept.append(box)
    return kept


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(min(aw * ah, bw * bh)) if inter else 0.0


def _shift(prev: np.ndarray, cur: np.ndarray) -> tuple[float, float]:
    """Camera translation between two frames, as a fraction of the width (phase correlation)."""
    small_prev = cv2.resize(prev, (prev.shape[1] // 2, prev.shape[0] // 2)).astype(np.float32)
    small_cur = cv2.resize(cur, (cur.shape[1] // 2, cur.shape[0] // 2)).astype(np.float32)
    window = cv2.createHanningWindow((small_prev.shape[1], small_prev.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(small_prev, small_cur, window)
    if response < 0.05:                                  # no clear global shift (cut, blur, crowd)
        return 0.0, 0.0
    width = small_prev.shape[1]
    return float(dx) / width, float(dy) / width


# ---------------------------------------------------------------------------- decoding

def _frames(path: Path, ffmpeg_exe: str) -> Iterator[np.ndarray]:
    """Grayscale frames at SAMPLE_RATE_HZ, ANALYSIS_WIDTH wide, rotation applied (PGM stream)."""
    cmd = [ffmpeg_exe, "-nostdin", "-loglevel", "error", "-i", str(path), "-an",
           "-vf", f"fps={SAMPLE_RATE_HZ},scale={ANALYSIS_WIDTH}:-2,format=gray",
           "-f", "image2pipe", "-c:v", "pgm", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    header = re.compile(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s")
    buffer = b""
    try:
        while True:
            match = header.match(buffer)
            if match is None or len(buffer) < match.end() + int(match.group(1)) * int(match.group(2)):
                chunk = proc.stdout.read(1 << 16)
                if not chunk:
                    break
                buffer += chunk
                continue
            w, h = int(match.group(1)), int(match.group(2))
            start = match.end()
            frame = np.frombuffer(buffer[start:start + w * h], dtype=np.uint8).reshape(h, w)
            buffer = buffer[start + w * h:]
            yield frame
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg could not read {path}: {stderr.strip()[:300]}")


def _analyze_uncached(path: Path, ffmpeg_exe: str) -> ShotMap:
    measure_faces = faces_available() and bool(_cascades())
    samples: list[ShotSample] = []
    prev: np.ndarray | None = None
    width = height = 0
    for index, gray in enumerate(_frames(path, ffmpeg_exe)):
        height, width = gray.shape
        motion = 0.0 if prev is None else float(np.mean(cv2.absdiff(gray, prev))) / 255.0
        dx, dy = (0.0, 0.0) if prev is None else _shift(prev, gray)
        sample = ShotSample(t_ms=int(round(index * 1000 / SAMPLE_RATE_HZ)), luma=float(gray.mean()),
                            motion=min(1.0, motion), shift_x=dx, shift_y=dy)
        if measure_faces and index % FACE_EVERY == 0:
            boxes = _faces(gray, profiles=(index // FACE_EVERY) % 2 == 0)
            sample.faces = len(boxes)
            if boxes:
                x, y, bw, bh = boxes[0]
                sample.face_area = (bw * bh) / float(width * height)
                sample.face_x = (x + bw / 2) / width
        samples.append(sample)
        prev = gray
    if not samples:
        raise RuntimeError(f"no video frames in {path}")
    duration_ms = int(round(len(samples) * 1000 / SAMPLE_RATE_HZ))
    return ShotMap(duration_ms=duration_ms, width=width, height=height, faces_measured=measure_faces,
                   samples=samples)


def analyze_shots(path: str | Path, *, cache_dir: str | Path | None = None, content_hash: str | None = None,
                  ffmpeg_exe: str | None = None) -> ShotMap:
    """Measure one video file (cached by content: a file is only ever analysed once)."""
    source = Path(path).resolve()
    exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()
    return load_or_compute(
        source, namespace="shots", analyzer_version=ANALYZER_VERSION,
        parameters={"rate": SAMPLE_RATE_HZ, "width": ANALYSIS_WIDTH, "face_every": FACE_EVERY,
                    "faces": faces_available()},
        model=ShotMap, compute=lambda: _analyze_uncached(source, exe),
        cache_dir=cache_dir, content_hash=content_hash,
    )


# ---------------------------------------------------------------------------- describing

def _in_range(shots: ShotMap, start_ms: int, end_ms: int, pad_ms: int = 0) -> list[ShotSample]:
    return [s for s in shots.samples if start_ms - pad_ms <= s.t_ms < end_ms + pad_ms]


def describe(shots: ShotMap, start_ms: int, end_ms: int) -> ShotTags:
    """Tags for the footage between start_ms and end_ms of the file."""
    end_ms = max(end_ms, start_ms + 1)
    window = _in_range(shots, start_ms, end_ms) or _in_range(shots, start_ms, end_ms, pad_ms=250)
    if not window:
        return ShotTags(faces_measured=shots.faces_measured)
    lumas = sorted(s.luma for s in window)
    luma = lumas[len(lumas) // 2]
    motion = float(np.mean([s.motion for s in window[1:]] or [0.0]))
    shifts = np.array([[s.shift_x, s.shift_y] for s in window])
    jitter = float(np.mean(np.linalg.norm(np.diff(shifts, axis=0), axis=1))) if len(shifts) >= 3 else 0.0
    tags = ShotTags(
        light="dark" if luma < DARK_LUMA else "bright" if luma > BRIGHT_LUMA else "normal",
        shaky=jitter > SHAKY_JITTER,
        movement="busy" if motion > BUSY_MOTION else "still" if motion < STILL_MOTION else "some",
        faces_measured=shots.faces_measured,
    )
    if shots.faces_measured:
        measured = [s for s in window if s.faces is not None]
        if not measured:                                     # short clip between face samples
            measured = [s for s in _in_range(shots, start_ms, end_ms, pad_ms=600) if s.faces is not None]
        if measured:
            counts = sorted(s.faces or 0 for s in measured)
            tags.faces = counts[len(counts) // 2] if len(counts) > 2 else max(counts)
            with_faces = [s.face_area for s in measured if s.faces]
            if with_faces and len(with_faces) * 2 >= len(measured):
                ordered = sorted(with_faces)
                area = ordered[0] if len(ordered) <= 2 else ordered[len(ordered) // 2]   # cautious when few
                tags.scale = "close-up" if area >= CLOSE_UP_AREA else "medium" if area >= MEDIUM_AREA else "wide"
            elif tags.faces == 0:
                tags.scale = None
    return tags


def find_segments(shots: ShotMap, length_ms: int, wanted: list[str], *, avoid: list[str] | None = None,
                  taken: list[tuple[int, int]] | None = None, step_ms: int = 250) -> list[int]:
    """Start times (ms) of footage of length_ms that has every wanted tag and none of the avoided
    ones, not overlapping the taken ranges; best (steadiest, brightest-normal) first."""
    taken = taken or []
    avoid = avoid or []
    found: list[tuple[float, int]] = []
    last_start = shots.duration_ms - length_ms
    start = 0
    while start <= last_start:
        end = start + length_ms
        if not any(start < t_end and end > t_start for t_start, t_end in taken):
            tags = describe(shots, start, end)
            # faces are seen once a second: also check around the segment, so a segment that
            # only touches a close-up is not taken for one
            around = describe(shots, max(0, start - 500), end + 500) if any(t in NEEDS_FACES for t in wanted) else tags
            if all(tags.has(tag) and around.has(tag) for tag in wanted) and not any(tags.has(tag) for tag in avoid):
                score = (0.0 if tags.shaky else 1.0) + (0.5 if tags.light == "normal" else 0.0) + 0.1 * min(tags.faces, 3)
                found.append((score, start))
        start += step_ms
    found.sort(key=lambda item: (-item[0], item[1]))
    return [start for _, start in found]
