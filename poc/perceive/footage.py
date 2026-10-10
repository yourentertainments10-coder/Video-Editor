"""poc/perceive/footage.py: footage perception -- numbers, not understanding.

Implements exactly the "Minimum video analysis" in docs/MVP.md Section 6:
four curves (motion, sharpness, luma, clip_frac) sampled at 4 Hz on a
320-pixel-wide grayscale downscale, plus four scalars (duration_ms, fps,
width, height). Nothing else. Per that document:

  "What is deliberately absent: optical flow, camera-motion classification,
  faces, scale, keyframes, embeddings. The first of these to add is faces,
  and only if Claim A demands it."

and per docs/ARCHITECTURE_REVIEW.md item 11, raw phone footage is treated as
continuous takes with no editorial cuts to detect -- there is no shot
detector here and none is planned for v0. This module produces *candidate
material* (a set of scored moments a sliding window can land anywhere in),
not a list of shots.

This module measures footage; it does not choose clips. Normalization
across a footage set, candidate-window scoring, and clip selection all
belong to the Decide layer (Step 7's grid/cutter), which is why the output
here (FootageMap) is plain, serializable data with no ranking, no
percentiles, and no editing parameters baked in -- exactly the same
separation MusicMap keeps from the Decide layer in Step 5.

Decoding goes through the imageio-ffmpeg bundled binary, consistent with
every other module that touches raw media in this project (poc.execute.
render, poc.perceive.music). Sampling is delegated entirely to FFmpeg's own
``fps`` filter rather than hand-rolled frame-seeking: ``fps=4`` resamples
the source to an exact, constant 4 Hz timeline (dropping or duplicating
source frames as needed), which is precisely "sample by timestamp, not
frame index" (docs/MVP.md Section 5) for variable-frame-rate phone video,
and it means every output frame's timestamp is exactly ``i / sample_rate``
by construction -- no seek-accuracy assumptions about any particular
container or codec.

This module intentionally duplicates a small amount of ffmpeg-output
parsing already present in poc.execute.render (duration/fps probing) rather
than importing from it: the renderer was explicitly put off-limits for
changes when Step 4 was approved, and each perception module is meant to
stand alone so it can be cached and reused independently (see the module
docstring principle "video/audio perception should be independent from
decision-making" in docs/ARCHITECTURE.md).

Ambiguities in docs/MVP.md, resolved with the simplest deterministic choice
rather than a model (flagged here, not silently decided):

  - "exposure" is not one formula in docs/MVP.md; the document defines two
    separate scalars, ``luma`` (mean gray value) and ``clip_frac``
    (fraction of pixels above 250 or below 5), and lets the cutter combine
    them however it needs to. This module keeps them separate, exactly as
    specified, rather than inventing a single merged "exposure score".
  - motion for the very first sample of a file has no previous sample to
    difference against. docs/MVP.md does not say what to do here; this
    module reports 0.0 (no measurable change from nothing), the simplest
    deterministic default, rather than ``None``/NaN which would force every
    consumer to special-case the first sample.
  - "above 250 or below 5" is read as strict inequality (256 possible
    8-bit values; 251..255 and 0..4), matching the plain English wording.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import cv2
import imageio_ffmpeg
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from poc.perceive.cache import load_or_compute
__all__ = [
    "DEFAULT_SAMPLE_RATE_HZ",
    "DEFAULT_ANALYSIS_WIDTH",
    "FootageSample",
    "FootageMap",
    "analyze_footage",
]

# ---------------------------------------------------------------------------
# validated-by-specification constants -- docs/MVP.md Section 6
# ---------------------------------------------------------------------------

DEFAULT_SAMPLE_RATE_HZ = 4.0
DEFAULT_ANALYSIS_WIDTH = 320
_CLIP_HIGH_THRESHOLD = 250  # "above 250"
_CLIP_LOW_THRESHOLD = 5  # "below 5"


# ---------------------------------------------------------------------------
# output structure -- its own shape, independent of the Timeline schema and
# of poc.perceive.music.MusicMap, so it can be cached/reused on its own.
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FootageSample(_Strict):
    """One 4 Hz sample. Every field is a plain measurement -- no ranking,
    no percentile, no editing judgment. Normalizing these across a whole
    footage set is the cutter's job (Step 7), not this module's."""

    t_s: float
    motion: float = Field(ge=0.0, le=1.0)
    sharpness: float = Field(ge=0.0)
    luma: float = Field(ge=0.0, le=255.0)
    clip_frac: float = Field(ge=0.0, le=1.0)


class FootageMap(_Strict):
    """Everything the grid/cutter (Step 7) needs about one footage file's
    quality/activity curves, computed once and reusable without
    re-decoding the file (it is plain, serializable data)."""

    schema_version: Literal["0.1"] = "0.1"
    analyzer_version: str = "poc-footage-v0.1"

    # source scalars -- docs/MVP.md Section 6's four scalars
    duration_ms: int
    fps: float | None
    width: int
    height: int

    # analysis parameters, kept for reproducibility
    sample_rate_hz: float
    analysis_width: int

    samples: list[FootageSample] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "FootageMap":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# probing (independent of poc.execute.render -- see module docstring)
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_VIDEO_LINE_RE = re.compile(r"Stream #\d+:\d+[^\n]*:\s*Video:[^\n]*")
_DIMENSIONS_RE = re.compile(r"(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"([\d.]+)\s*fps")


class _VideoMetadata(BaseModel):
    duration_ms: int
    fps: float | None
    width: int
    height: int


class FootageProbeError(RuntimeError):
    """FFmpeg produced no parseable video stream information."""

    def __init__(self, path: Path, output: str):
        self.path = path
        self.output = output
        super().__init__(f"could not determine video metadata for {path} from ffmpeg output:\n{output}")


def _probe_video_metadata(path: Path, ffmpeg_exe: str) -> _VideoMetadata:
    proc = subprocess.run(
        [ffmpeg_exe, "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    text = proc.stdout or ""

    duration_match = _DURATION_RE.search(text)
    video_line_match = _VIDEO_LINE_RE.search(text)
    if not duration_match or not video_line_match:
        raise FootageProbeError(path, text)

    hours, minutes, seconds = duration_match.groups()
    duration_ms = int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))

    video_line = video_line_match.group(0)
    dims_match = _DIMENSIONS_RE.search(video_line)
    if not dims_match:
        raise FootageProbeError(path, text)
    width, height = int(dims_match.group(1)), int(dims_match.group(2))

    fps_match = _FPS_RE.search(video_line)
    fps = float(fps_match.group(1)) if fps_match else None

    return _VideoMetadata(duration_ms=duration_ms, fps=fps, width=width, height=height)


# ---------------------------------------------------------------------------
# sampling: FFmpeg's fps filter resamples to an exact constant-rate grid
# ---------------------------------------------------------------------------


def _extract_grayscale_samples(
    path: Path, sample_rate_hz: float, analysis_width: int, ffmpeg_exe: str
) -> list[np.ndarray]:
    with tempfile.TemporaryDirectory(prefix="poc_footage_") as tmp:
        pattern = Path(tmp) / "f_%06d.png"
        vf = f"fps={sample_rate_hz},scale={analysis_width}:-2,format=gray"
        cmd = [ffmpeg_exe, "-y", "-loglevel", "error", "-i", str(path), "-vf", vf, str(pattern)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        frame_paths = sorted(Path(tmp).glob("f_*.png"))
        frames = []
        for frame_path in frame_paths:
            gray = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
            if gray is not None:
                frames.append(gray)
        return frames


# ---------------------------------------------------------------------------
# per-frame metrics -- docs/MVP.md Section 6's four curves
# ---------------------------------------------------------------------------


def _frame_metrics(gray: np.ndarray, prev_gray: np.ndarray | None) -> tuple[float, float, float, float]:
    """Return (motion, sharpness, luma, clip_frac) for one grayscale frame.

    motion: mean absolute difference between consecutive sampled frames,
      divided by 255 -- 0.0 for the first frame of a file (no predecessor).
    sharpness: variance of the Laplacian.
    luma: mean gray value (0..255).
    clip_frac: fraction of pixels above 250 or below 5.
    """
    gray_f = gray.astype(np.float64)
    luma = float(gray_f.mean())
    clip_frac = float(np.mean((gray > _CLIP_HIGH_THRESHOLD) | (gray < _CLIP_LOW_THRESHOLD)))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if prev_gray is None:
        motion = 0.0
    else:
        motion = float(np.mean(np.abs(gray_f - prev_gray.astype(np.float64))) / 255.0)
    return motion, sharpness, luma, clip_frac


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def _analyze_footage_uncached(
    path: str | Path,
    *,
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
    analysis_width: int = DEFAULT_ANALYSIS_WIDTH,
    ffmpeg_exe: str | None = None,
) -> FootageMap:
    """Analyze one footage file and return its FootageMap.

    Produces exactly docs/MVP.md Section 6's per-file index: motion,
    sharpness, luma, and clip_frac at ``sample_rate_hz`` (4 Hz by default)
    on an ``analysis_width``-pixel-wide grayscale downscale, plus the
    source's duration_ms/fps/width/height. Does not detect shots, does not
    rank or select anything -- see the module docstring.
    """
    path = Path(path)
    ffmpeg_exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()

    metadata = _probe_video_metadata(path, ffmpeg_exe)
    frames = _extract_grayscale_samples(path, sample_rate_hz, analysis_width, ffmpeg_exe)

    samples: list[FootageSample] = []
    prev_gray: np.ndarray | None = None
    for i, gray in enumerate(frames):
        motion, sharpness, luma, clip_frac = _frame_metrics(gray, prev_gray)
        samples.append(
            FootageSample(t_s=i / sample_rate_hz, motion=motion, sharpness=sharpness, luma=luma, clip_frac=clip_frac)
        )
        prev_gray = gray

    return FootageMap(
        duration_ms=metadata.duration_ms,
        fps=metadata.fps,
        width=metadata.width,
        height=metadata.height,
        sample_rate_hz=sample_rate_hz,
        analysis_width=analysis_width,
        samples=samples,
    )


def analyze_footage(
    path: str | Path,
    *,
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
    analysis_width: int = DEFAULT_ANALYSIS_WIDTH,
    ffmpeg_exe: str | None = None,
    cache_dir: str | Path | None = None,
) -> FootageMap:
    """Analyze footage, reusing a content- and parameter-keyed JSON map."""
    path = Path(path)
    ffmpeg_exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()
    parameters = {
        "sample_rate_hz": sample_rate_hz,
        "analysis_width": analysis_width,
        "ffmpeg_exe": ffmpeg_exe,
    }
    return load_or_compute(
        path,
        namespace="footage",
        analyzer_version="poc-footage-v0.1",
        parameters=parameters,
        model=FootageMap,
        compute=lambda: _analyze_footage_uncached(
            path,
            sample_rate_hz=sample_rate_hz,
            analysis_width=analysis_width,
            ffmpeg_exe=ffmpeg_exe,
        ),
        cache_dir=cache_dir,
    )
