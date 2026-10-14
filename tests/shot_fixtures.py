"""Small synthetic videos for poc.perceive.shots, built from one public-domain photo.

tests/fixtures/faces/astronaut.jpg: NASA portrait of Eileen Collins (public domain, as shipped
with scikit-image's sample data), downscaled to 256x256.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

PHOTO = Path(__file__).parent / "fixtures" / "faces" / "astronaut.jpg"
W, H, FPS = 360, 640, 30


def _write(path: Path, frames) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for frame in frames:
        proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


def _canvas(photo_px: int, gain: float = 1.0, background: int = 70) -> np.ndarray:
    photo = cv2.resize(cv2.imread(str(PHOTO)), (photo_px, photo_px))
    canvas = np.full((H, W, 3), background, np.uint8)
    rng = np.random.default_rng(1)
    canvas = np.clip(canvas + rng.normal(0, 12, canvas.shape), 0, 255).astype(np.uint8)   # texture
    y, x = (H - photo_px) // 2, (W - photo_px) // 2
    ys, xs = max(0, -y), max(0, -x)
    crop = photo[ys:ys + min(H, photo_px), xs:xs + min(W, photo_px)]
    canvas[max(0, y):max(0, y) + crop.shape[0], max(0, x):max(0, x) + crop.shape[1]] = crop
    return np.clip(canvas.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def still(path: Path, photo_px: int, seconds: float = 4, gain: float = 1.0) -> Path:
    frame = _canvas(photo_px, gain)
    return _write(path, (frame for _ in range(int(seconds * FPS))))


def moving(path: Path, photo_px: int, seconds: float = 4, *, shake_px: float = 0, pan_px_per_s: float = 0) -> Path:
    big = cv2.resize(_canvas(photo_px), (W + 120, H + 120))
    rng = np.random.default_rng(7)

    def frames():
        for i in range(int(seconds * FPS)):
            jx, jy = rng.normal(0, shake_px, 2) if shake_px else (0.0, 0.0)
            ox = 10 + jx + pan_px_per_s * i / FPS
            oy = 60 + jy
            m = np.float32([[1, 0, -ox], [0, 1, -oy]])
            yield cv2.warpAffine(big, m, (W, H), borderMode=cv2.BORDER_REFLECT)
    return _write(path, frames())


def _still_frames(photo_px: int, seconds: float, gain: float = 1.0):
    frame = _canvas(photo_px, gain)
    for _ in range(int(seconds * FPS)):
        yield frame


def _moving_frames(photo_px: int, seconds: float, shake_px: float):
    big = cv2.resize(_canvas(photo_px), (W + 120, H + 120))
    rng = np.random.default_rng(7)
    for _ in range(int(seconds * FPS)):
        jx, jy = rng.normal(0, shake_px, 2)
        m = np.float32([[1, 0, -(60 + jx)], [0, 1, -(60 + jy)]])
        yield cv2.warpAffine(big, m, (W, H), borderMode=cv2.BORDER_REFLECT)


def _empty_frames(seconds: float):
    rng = np.random.default_rng(3)
    base = np.clip(np.full((H, W, 3), 120.0) + rng.normal(0, 25, (H, W, 3)), 0, 255).astype(np.uint8)
    for _ in range(int(seconds * FPS)):
        yield base


# 4-second parts of tour.mp4, in order
TOUR = ["medium", "close-up", "dark", "shaky", "empty"]


def tour(path: Path) -> Path:
    """One file with a medium shot, a close-up, a dark shot, a shaky shot and no people (4 s each)."""
    def frames():
        yield from _still_frames(300, 4)
        yield from _still_frames(700, 4)
        yield from _still_frames(300, 4, gain=0.3)
        yield from _moving_frames(300, 4, shake_px=6)
        yield from _empty_frames(4)
    return _write(path, frames())
