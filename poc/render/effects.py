"""Pure FFmpeg filter generation for deterministic clip effects."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def _parameters(effect) -> tuple[str, dict]:
    if isinstance(effect, Mapping):
        effect_id = effect.get("effect_id")
        values = effect.get("parameters", {})
    else:
        effect_id = getattr(effect, "effect_id", None)
        values = getattr(effect, "parameters", {})
    if hasattr(values, "model_dump"):
        values = values.model_dump()
    if not isinstance(values, Mapping):
        raise ValueError(f"effect parameters must be a mapping: {effect_id}")
    return str(effect_id), dict(values)


def _num(value: float) -> str:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("effect values must be finite")
    return f"{value:.12g}"


def _expr(value: str) -> str:
    """Quote an FFmpeg expression so its commas stay inside the filter option."""
    return f"'{value}'"


def build_effect_filters(
    effects: Sequence,
    *,
    width: int,
    height: int,
    fps: int,
    left_handle_frames: int,
    clip_frames: int,
) -> str:
    """Build a comma-separated, deterministic video filter chain."""
    if not effects:
        return ""
    if min(width, height, fps, clip_frames) <= 0 or left_handle_frames < 0:
        raise ValueError("frame dimensions, rate, and clip length must be positive")

    start = left_handle_frames / fps
    clip_duration = clip_frames / fps
    filters: list[str] = []
    for effect in effects:
        effect_id, p = _parameters(effect)
        if effect_id == "brightness":
            filters.append(f"eq=brightness={_num(p['amount'])}")
        elif effect_id == "contrast":
            filters.append(f"eq=contrast={_num(p.get('factor', 1.0))}")
        elif effect_id == "saturation":
            filters.append(f"eq=saturation={_num(p.get('factor', 1.0))}")
        elif effect_id == "blur":
            radius = float(p["radius"])
            if radius != 0:
                filters.append(f"gblur=sigma={_num(radius)}")
        elif effect_id in {"fade_in", "fade_out"}:
            duration = min(max(0.0, float(p.get("duration_ms", 300)) / 1000), clip_duration)
            if duration <= 0:
                continue
            if effect_id == "fade_in":
                filters.append(f"fade=t=in:st={_num(start)}:d={_num(duration)}")
            else:
                end = start + clip_duration
                filters.append(
                    f"fade=t=out:st={_num(max(start, end-duration))}:d={_num(duration)}"
                )
        elif effect_id in {"zoom_in", "zoom_out"}:
            from_scale = float(p.get("from_scale", 1.0 if effect_id == "zoom_in" else 1.2))
            to_scale = float(p.get("to_scale", 1.2 if effect_id == "zoom_in" else 1.0))
            for scale in (from_scale, to_scale):
                if scale < 1.0:
                    raise ValueError("zoom below 1.0 is not supported")
                if not math.isfinite(scale):
                    raise ValueError("zoom scales must be finite")
            duration = min(max(1.0 / fps, float(p.get("duration_ms", 1000)) / 1000), clip_duration)
            start_frame = left_handle_frames
            duration_frames = max(1, round(duration * fps))
            progress = f"min(max((n-{start_frame})/{duration_frames}\\,0)\\,1)"
            scale_expr = f"({ _num(from_scale) }+({_num(to_scale-from_scale)})*{progress})"
            sw = f"ceil({width}*{scale_expr}/2)*2"
            sh = f"ceil({height}*{scale_expr}/2)*2"
            filters.extend([
                f"scale=w={_expr(sw)}:h={_expr(sh)}:eval=frame",
                f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2",
            ])
        elif effect_id.startswith("pan_") and effect_id in {
            "pan_left", "pan_right", "pan_up", "pan_down"
        }:
            distance = float(p.get("distance", 0.1))
            if distance == 0:
                continue
            duration = min(max(1.0 / fps, float(p.get("duration_ms", 1000)) / 1000), clip_duration)
            duration_frames = max(1, round(duration * fps))
            progress = f"min(max((n-{left_handle_frames})/{duration_frames}\\,0)\\,1)"
            # Overscan leaves room for a centered crop that can travel smoothly.
            scaled_w = f"ceil({width}*(1+{_num(distance)})/2)*2"
            scaled_h = f"ceil({height}*(1+{_num(distance)})/2)*2"
            max_x = f"iw-{width}"
            max_y = f"ih-{height}"
            if effect_id == "pan_left":
                x = f"({max_x})*(0.5+0.5*{progress})"
                y = f"({max_y})/2"
            elif effect_id == "pan_right":
                x = f"({max_x})*(0.5-0.5*{progress})"
                y = f"({max_y})/2"
            elif effect_id == "pan_up":
                x = f"({max_x})/2"
                y = f"({max_y})*(0.5+0.5*{progress})"
            else:
                x = f"({max_x})/2"
                y = f"({max_y})*(0.5-0.5*{progress})"
            filters.extend([
                f"scale=w={_expr(scaled_w)}:h={_expr(scaled_h)}:eval=frame",
                f"crop={width}:{height}:{_expr(x)}:{_expr(y)}",
            ])
        else:
            raise ValueError(f"unsupported effect: {effect_id}")
    return ",".join(filters)
