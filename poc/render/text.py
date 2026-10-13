"""Deterministic libass text rendering with the project's bundled Noto fonts."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from poc.ingest.normalize import FFMPEG_EXE, FFMPEG_NETWORK_SAFE
from poc.render._process import run_ffmpeg_atomic


_FONTS_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_FONT_ENTRIES = json.loads((_FONTS_DIR / "fonts.json").read_text(encoding="utf-8"))
_FONT_FAMILIES = {entry["family"] for entry in _FONT_ENTRIES}
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class TextOverlay:
    text: str
    start_ms: int
    end_ms: int
    x: float = 0.5
    y: float = 0.8
    size_pct: float = 0.06
    color: str = "#FFFFFF"
    outline_px: int = 3
    bold: bool = False
    fade_ms: int = 150
    font: str = "Noto Sans"


def _validate_overlay(overlay: TextOverlay) -> None:
    if overlay.end_ms <= overlay.start_ms:
        raise ValueError("end_ms must be greater than start_ms")
    if overlay.start_ms < 0:
        raise ValueError("start_ms must be non-negative")
    if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in (overlay.x, overlay.y)):
        raise ValueError("x and y must be between 0 and 1")
    if not math.isfinite(overlay.size_pct) or not 0.0 < overlay.size_pct <= 0.5:
        raise ValueError("size_pct must be in (0, 0.5]")
    if not isinstance(overlay.color, str) or not _HEX_COLOR.fullmatch(overlay.color):
        raise ValueError("color must be #RRGGBB")
    if overlay.font not in _FONT_FAMILIES:
        raise ValueError(f"font family {overlay.font!r} is not in the bundled font manifest")


def _ass_time(ms: int) -> str:
    centiseconds = round(ms / 10)
    hours, rem = divmod(centiseconds, 360000)
    minutes, rem = divmod(rem, 6000)
    seconds, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def _ass_color(color: str) -> str:
    rgb = color[1:]
    return f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"


def _ass_text(text: str) -> str:
    # Escape user-supplied ASS syntax before adding the intentional line breaks.
    escaped_lines = []
    for line in text.split("\n"):
        line = line.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        escaped_lines.append(line)
    return r"\N".join(escaped_lines)


def build_ass(overlays: list[TextOverlay], width: int, height: int) -> str:
    """Build a stable ASS script for text overlays in a frame-sized canvas."""
    if width <= 0 or height <= 0:
        raise ValueError("frame dimensions must be positive")
    for overlay in overlays:
        _validate_overlay(overlay)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Default,Noto Sans,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1",
    ]
    lines.extend([
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    for index, overlay in enumerate(overlays):
        x = round(overlay.x * width)
        y = round(overlay.y * height)
        duration = overlay.end_ms - overlay.start_ms
        fade = min(max(0, overlay.fade_ms), duration // 2)
        # ASS uses em units; convert the requested visible pixel height to its
        # font-size unit so cap-height text matches the size_pct contract.
        font_size = max(1, round(overlay.size_pct * height * 1.7))
        bold = 1 if overlay.bold else 0
        tags = (
            f"\\fn{overlay.font}\\fs{font_size}\\b{bold}"
            f"\\c{_ass_color(overlay.color)}&\\bord{max(0, overlay.outline_px)}"
            f"\\an5\\pos({x},{y})\\fad({fade},{fade})"
        )
        lines.append(
            f"Dialogue: 0,{_ass_time(overlay.start_ms)},{_ass_time(overlay.end_ms)},"
            f"Default,,0,0,0,,{{{tags}}}{_ass_text(overlay.text)}"
        )
    return "\n".join(lines) + "\n"


def _escape_filter_path(path: str | Path) -> str:
    """Quote one path value for an FFmpeg filtergraph option."""
    item = Path(path)
    value = item.resolve().as_posix() if item.is_absolute() else item.as_posix()
    for char in (":", "'", ",", "[", "]", ";"):
        value = value.replace(char, "\\" + char)
    return f"'{value}'"


def _write_ass(path: Path, overlays: list[TextOverlay], width: int, height: int) -> None:
    path.write_text(build_ass(overlays, width, height), encoding="utf-8", newline="\n")


def _ass_filter(ass_path: Path) -> str:
    return f"ass={_escape_filter_path(ass_path)}:fontsdir={_escape_filter_path(_FONTS_DIR)}"


def burn_text_to_file(
    in_video: str | Path,
    overlays: list[TextOverlay],
    out_path: str | Path,
    *,
    crf: int = 18,
    preset: str = "medium",
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Burn ASS overlays into video while copying any audio stream unchanged."""
    in_video = Path(in_video)
    out_path = Path(out_path)
    if not overlays:
        raise ValueError("at least one text overlay is required")
    for overlay in overlays:
        _validate_overlay(overlay)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out_path.parent / f".text-{os.getpid()}-{uuid.uuid4().hex}.partial.ass"
    try:
        with av_open_dimensions(in_video) as (width, height):
            _write_ass(ass_path, overlays, width, height)
        cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:], "-i", str(in_video),
               "-vf", _ass_filter(ass_path), "-map", "0:v:0", "-map", "0:a?",
               "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-fflags", "+bitexact",
               "-flags:v", "+bitexact", "-map_metadata", "-1",
               "-colorspace", "bt709", "-color_primaries", "bt709",
               "-color_trc", "bt709", "-color_range", "tv",
               "-movflags", "+faststart", str(out_path)]
        cmd[cmd.index("-vf") + 1] = _ass_filter(Path(ass_path.name))
        return run_ffmpeg_atomic(
            cmd, out_path, cancel=cancel, label="text burn", cwd=out_path.parent,
        )
    finally:
        ass_path.unlink(missing_ok=True)


class av_open_dimensions:
    """Context manager keeping PyAV import and stream probing local to the call."""

    def __init__(self, path: Path):
        import av
        self.container = av.open(str(path))

    def __enter__(self) -> tuple[int, int]:
        stream = self.container.streams.video[0]
        return stream.width, stream.height

    def __exit__(self, exc_type, exc, tb) -> None:
        self.container.close()


def text_boxes(
    overlays: list[TextOverlay], width: int, height: int
) -> list[tuple[int, int, int, int]]:
    """Measure non-black rendered bounds for each overlay using libass itself."""
    if width <= 0 or height <= 0:
        raise ValueError("frame dimensions must be positive")
    boxes = []
    for overlay in overlays:
        _validate_overlay(overlay)
        visible = TextOverlay(
            text=overlay.text, start_ms=0, end_ms=1000, x=overlay.x, y=overlay.y,
            size_pct=overlay.size_pct, color=overlay.color,
            outline_px=overlay.outline_px, bold=overlay.bold, fade_ms=0,
            font=overlay.font,
        )
        with tempfile.TemporaryDirectory(prefix="text-box-") as temp_name:
            ass_path = Path(temp_name) / "box.ass"
            _write_ass(ass_path, [visible], width, height)
            cmd = [FFMPEG_EXE, "-v", "error", *FFMPEG_NETWORK_SAFE[1:],
                   "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=1:d=1",
                   "-vf", _ass_filter(ass_path), "-frames:v", "1", "-f", "rawvideo",
                   "-pix_fmt", "rgb24", "pipe:1"]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode:
                raise RuntimeError(f"text box render failed: {result.stderr.decode(errors='replace')}")
            frame = np.frombuffer(result.stdout, dtype=np.uint8)
            expected = width * height * 3
            if frame.size != expected:
                raise RuntimeError(f"text box render returned {frame.size} bytes; expected {expected}")
            image = frame.reshape(height, width, 3)
            mask = np.any(image != 0, axis=2)
            ys, xs = np.nonzero(mask)
            if not len(xs):
                boxes.append((0, 0, 0, 0))
            else:
                left, right = int(xs.min()), int(xs.max()) + 1
                top, bottom = int(ys.min()), int(ys.max()) + 1
                boxes.append((left, top, right-left, bottom-top))
    return boxes
