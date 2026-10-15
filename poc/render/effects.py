"""Pure FFmpeg filter generation for deterministic clip effects."""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence

_LABELS = itertools.count()      # unique labels for effects that need a small sub-graph (glow)


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
        elif effect_id.startswith("look_") or effect_id in {"vignette", "grain", "sharpen", "glow", "rgb_split",
                                                             "stabilize"}:
            chain = _amount_effect(effect_id, float(p.get("amount", 0.6)), width, height)
            if chain:
                filters.append(chain)
        elif effect_id == "light_leak":
            at = start + max(0.0, float(p.get("at_ms", 0))) / 1000
            length = max(2.0 / fps, float(p.get("duration_ms", 600)) / 1000)
            amount = float(p.get("amount", 0.6))
            if amount <= 0:
                continue
            n = next(_LABELS)
            rise = min(0.25 * length, 0.3)
            # a blurred, warm-tinted copy of the picture, faded in and out, screen-blended on top
            filters.append(
                f"split[leak{n}a][leak{n}b];[leak{n}b]scale={max(2, width // 8)}:{max(2, height // 8)},"
                f"gblur=sigma=4,colorchannelmixer=rr=1:rg=0.35:gg=0.55:bb=0.15:br=0.1,"
                f"eq=brightness={_num(0.12 * amount)},scale={width}:{height},"
                f"fade=t=in:st={_num(at)}:d={_num(rise)},fade=t=out:st={_num(at + length - rise)}:d={_num(rise)}"
                f"[leak{n}c];[leak{n}a][leak{n}c]blend=all_mode=screen:all_opacity={_num(min(1.0, 0.5 + 0.5 * amount))}")
        elif effect_id in {"flash", "shake", "zoom_punch"}:
            at = start + max(0.0, float(p.get("at_ms", 0))) / 1000
            length = max(1.0 / fps, float(p.get("duration_ms", 300)) / 1000)
            amount = float(p.get("amount", 0.6))
            if amount <= 0:
                continue
            # 1 at the accent, falling to 0 over its duration
            env = f"if(between(t\\,{_num(at)}\\,{_num(at + length)})\\,1-(t-{_num(at)})/{_num(length)}\\,0)"
            if effect_id == "flash":
                filters.append(f"eq=brightness={_expr(f'{_num(0.55 * amount)}*{env}')}:eval=frame")
            elif effect_id == "shake":
                pad = 1 + 0.08 * amount
                amp = 0.03 * amount * width
                filters.extend([
                    f"scale=w={_even(width * pad)}:h={_even(height * pad)}",
                    f"crop={width}:{height}:{_expr(f'(iw-ow)/2+{_num(amp)}*sin(t*53)*{env}')}:"
                    f"{_expr(f'(ih-oh)/2+{_num(amp)}*cos(t*41)*{env}')}",
                ])
            else:
                start_frame = round(at * fps)
                frames = max(1, round(length * fps))
                env_n = f"if(between(n\\,{start_frame}\\,{start_frame + frames})\\,1-(n-{start_frame})/{frames}\\,0)"
                scale_expr = f"(1+{_num(0.14 * amount)}*{env_n})"
                filters.extend([
                    f"scale=w={_expr(f'ceil({width}*{scale_expr}/2)*2')}:h={_expr(f'ceil({height}*{scale_expr}/2)*2')}:eval=frame",
                    f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2",
                ])
        else:
            raise ValueError(f"unsupported effect: {effect_id}")
    return ",".join(filters)


def _even(value: float) -> int:
    return int(math.ceil(value / 2) * 2)


def _amount_effect(effect_id: str, a: float, width: int, height: int) -> str:
    """Looks, textures and repair: one strength knob each (0 = nothing)."""
    a = min(max(a, 0.0), 1.0)
    if a == 0:
        return ""
    if effect_id == "look_cinematic":
        return (f"colorbalance=rs={_num(-0.10 * a)}:gs={_num(-0.02 * a)}:bs={_num(0.12 * a)}:"
                f"rh={_num(0.10 * a)}:gh={_num(0.02 * a)}:bh={_num(-0.10 * a)},"
                f"eq=contrast={_num(1 + 0.08 * a)}:saturation={_num(1 + 0.05 * a)}")
    if effect_id == "look_vintage":
        return (f"curves=r={_expr(f'0/{_num(0.10 * a)} 0.5/0.52 1/{_num(1 - 0.06 * a)}')}:"
                f"g={_expr(f'0/{_num(0.05 * a)} 1/{_num(1 - 0.05 * a)}')}:"
                f"b={_expr(f'0/{_num(0.14 * a)} 1/{_num(1 - 0.16 * a)}')},"
                f"eq=saturation={_num(1 - 0.25 * a)}")
    if effect_id == "look_warm":
        return f"colortemperature=temperature={round(6500 - 1800 * a)}"
    if effect_id == "look_cool":
        return f"colortemperature=temperature={round(6500 + 2800 * a)}"
    if effect_id == "look_punchy":
        return (f"eq=contrast={_num(1 + 0.15 * a)}:saturation={_num(1 + 0.28 * a)},"
                f"unsharp=5:5:{_num(0.45 * a)}")
    if effect_id == "look_mono":
        return f"hue=s=0,eq=contrast={_num(1 + 0.25 * a)}"
    if effect_id == "look_matte":
        return (f"curves=all={_expr(f'0/{_num(0.09 * a)} 1/{_num(1 - 0.05 * a)}')},"
                f"eq=saturation={_num(1 - 0.18 * a)}")
    if effect_id == "vignette":
        return f"vignette=angle={_num(0.25 + 0.5 * a)}"
    if effect_id == "grain":
        return f"noise=alls={round(3 + 12 * a)}:allf=t"     # fine grain: strong noise only adds megabytes
    if effect_id == "sharpen":
        return f"unsharp=5:5:{_num(1.5 * a)}"
    if effect_id == "rgb_split":
        shift = max(1, round(a * width / 150))
        return f"rgbashift=rh={-shift}:bh={shift}"
    if effect_id == "glow":
        n = next(_LABELS)
        return (f"split[glow{n}a][glow{n}b];[glow{n}b]gblur=sigma={_num(6 + 10 * a)}[glow{n}c];"
                f"[glow{n}a][glow{n}c]blend=all_mode=screen:all_opacity={_num(0.2 + 0.35 * a)}")
    if effect_id == "stabilize":
        keep = 1 - 0.04 - 0.04 * a
        search = 16 * (1 + round(3 * a))             # deshake needs a multiple of 16 (16..64)
        return (f"deshake=rx={search}:ry={search},"
                f"crop={_expr(f'iw*{_num(keep)}')}:{_expr(f'ih*{_num(keep)}')},scale={width}:{height}")
    raise ValueError(f"unsupported effect: {effect_id}")
