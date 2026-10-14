"""Local Director, phase 3: the finishing pass an editor does after the cut.

``plan_style`` decorates a beat-cut timeline with transitions, beat accents, camera moves and a
colour look, chosen from the music (beat strength, downbeats, energy rises), the mood and what
the footage shows. Everything is ordinary timeline data from the renderable catalogues
(poc.timeline), so the user can change or remove any of it, and the renderer only ever sees
tested FFmpeg filters -- the Director never writes FFmpeg commands.

Rules of thumb it follows (the way an editor would):
  * most cuts stay hard cuts on the beat; transitions are accents, never every cut;
  * accents go on downbeats where the music is strong or rising, spaced apart;
  * never the same fancy transition twice in a row;
  * energetic: short punchy transitions, zoom punches on strong beats, a flash on the biggest
    rise; calm: longer soft transitions and slow camera moves;
  * shaky footage is stabilised, dark footage lifted, still shots get a slow push-in;
  * a transition is only placed when both clips are long enough and have spare footage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from poc.director.footage import Footage, clip_tags
from poc.timeline import EditorClip, EditorTimeline, EffectRef, TransitionRef

Style = Literal["clean", "auto", "bold"]
Mood = Literal["energetic", "calm"]
STYLES: tuple[str, ...] = ("clean", "auto", "bold")

# effect ids the style pass owns: re-styling replaces these, user effects with other ids stay
STYLE_EFFECTS = {
    "zoom_in", "zoom_out", "pan_left", "pan_right", "flash", "shake", "zoom_punch", "stabilize",
    "look_cinematic", "look_vintage", "look_warm", "look_cool", "look_punchy", "look_mono", "look_matte",
    "vignette", "grain", "glow", "rgb_split", "sharpen",
}
LOOKS = {"cinematic", "vintage", "warm", "cool", "punchy", "mono", "matte"}

ENERGETIC_TRANSITIONS = ("zoom_through", "slide_left", "blur_through", "slide_up", "circle_open", "dip_to_white")
BOLD_EXTRA = ("pixelize", "radial", "wind")
CALM_TRANSITIONS = ("dissolve", "fade", "smooth_left", "fade", "circle_open", "fade_mono")

# family -> transitions, for "zoom transitions", "slide transitions", ...
FAMILIES: dict[str, tuple[str, ...]] = {
    "zoom": ("zoom_through",),
    "slide": ("slide_left", "slide_right", "slide_up", "slide_down"),
    "wipe": ("wipe_left", "wipe_right", "smooth_left", "smooth_right", "diagonal"),
    "blur": ("blur_through",),
    "glitch": ("pixelize", "slice", "wind"),
    "circle": ("circle_open", "circle_close", "radial"),
    "flash": ("dip_to_white",),
    "black": ("dip_to_black",),
    "fade": ("fade",),
    "dissolve": ("dissolve",),
    "mix": ENERGETIC_TRANSITIONS + CALM_TRANSITIONS[:3],
}


@dataclass(frozen=True)
class Cut:
    index: int              # the incoming clip (1-based position in the video track minus one)
    tick: int
    downbeat: bool
    strength: float         # 0..1: how strong the music is here (rank of the beat's energy)
    rise: float             # energy after minus before (ranked 0..1)


def video_clips(tl: EditorTimeline) -> list[EditorClip]:
    return sorted((c for c in tl.clips if c.track_id == "video"), key=lambda c: c.start_tick)


def _ms(tl: EditorTimeline, ticks: float) -> float:
    return ticks * 1000 / tl.time_base.ticks_per_second


def _ticks(tl: EditorTimeline, ms: float) -> int:
    return int(round(ms * tl.time_base.ticks_per_second / 1000))


def _rank(values: list[float]) -> list[float]:
    """0..1 rank of each value (ties share the middle of their places)."""
    if not values:
        return []
    order = sorted(values)
    n = max(1, len(values) - 1)
    first = {}
    last = {}
    for i, v in enumerate(order):
        first.setdefault(v, i)
        last[v] = i
    return [(first[v] + last[v]) / 2 / n for v in values]


def music_cuts(tl: EditorTimeline) -> list[Cut]:
    """Every cut between video clips, with what the music does there."""
    clips = video_clips(tl)
    beats = sorted((m for m in tl.markers if m.kind == "beat"), key=lambda m: m.position_tick)
    energies = [float(m.value or 0.0) for m in beats]
    ranks = _rank(energies)
    rises_raw = []
    for i in range(len(beats)):
        after = energies[i:i + 4] or [0.0]
        before = energies[max(0, i - 4):i] or after
        rises_raw.append(sum(after) / len(after) - sum(before) / len(before))
    rises = _rank(rises_raw)
    tolerance = _ticks(tl, 70)
    cuts = []
    for index in range(1, len(clips)):
        tick = clips[index].start_tick
        nearest = min(range(len(beats)), key=lambda i: abs(beats[i].position_tick - tick), default=None)
        if nearest is None or abs(beats[nearest].position_tick - tick) > tolerance:
            cuts.append(Cut(index, tick, False, 0.0, 0.0))
            continue
        cuts.append(Cut(index, tick, beats[nearest].label == "downbeat", ranks[nearest], rises[nearest]))
    return cuts


def accent_cuts(tl: EditorTimeline, mood: Mood, style: Style) -> list[Cut]:
    """Cuts worth marking: strong or rising downbeats, spaced apart."""
    gap_ms = {"energetic": 3500, "calm": 5500}[mood] * (0.55 if style == "bold" else 1.0)
    threshold = 0.45 if style == "bold" else 0.6
    candidates = [c for c in music_cuts(tl) if c.downbeat and max(c.strength, c.rise) >= threshold]
    if not candidates:                                         # no beat data: every few seconds
        clips = video_clips(tl)
        candidates = [Cut(i, clips[i].start_tick, True, 0.5, 0.5) for i in range(1, len(clips))]
    chosen: list[Cut] = []
    for cut in sorted(candidates, key=lambda c: -max(c.strength, c.rise)):
        if all(abs(_ms(tl, cut.tick - other.tick)) >= gap_ms for other in chosen):
            chosen.append(cut)
    return sorted(chosen, key=lambda c: c.tick)


def max_transition_ticks(tl: EditorTimeline, prev: EditorClip, clip: EditorClip, limit_ms: float) -> int:
    """Longest centred transition (<= limit_ms, even number of frames) the two clips allow:
    at most half of either clip, and the spare footage before/after them."""
    tps = tl.time_base.ticks_per_second
    fps = tl.settings.fps_num / tl.settings.fps_den
    durations = {a.asset_id: a.duration_ms for a in tl.assets}
    limit = min(limit_ms, _ms(tl, prev.duration_ticks) * 0.45, _ms(tl, clip.duration_ticks) * 0.45)
    if prev.source_out_ms is not None:
        total = durations.get(prev.asset_id)
        if total is None:
            return 0
        limit = min(limit, 2 * (total - prev.source_out_ms))
    if clip.source_in_ms is not None:
        limit = min(limit, 2 * clip.source_in_ms)
    frames = 2 * int(limit * fps / 2000)
    if frames < 2:
        return 0
    return int(frames * 1000 / fps * tps / 1000)


def _transition(tid: str, ticks: int) -> TransitionRef:
    extra = {"parameters": {"mask_seed": 7}} if tid == "dissolve" else {}
    return TransitionRef(transition_id=tid, duration_ticks=ticks, **extra)


def set_transitions(tl: EditorTimeline, cut_indices: list[int], pool: tuple[str, ...], limit_ms: float,
                    *, clear_others: bool = False) -> tuple[EditorTimeline, int]:
    """Put transitions from ``pool`` (rotating, never the same twice in a row) on the given cuts."""
    clips = video_clips(tl)
    by_id = {c.clip_id: c for c in tl.clips}
    placed = 0
    last = None
    turn = 0
    wanted = set(cut_indices)
    for index in range(1, len(clips)):
        clip = clips[index]
        if index not in wanted:
            if clear_others and clip.transition is not None:
                by_id[clip.clip_id] = clip.model_copy(update={"transition": None})
            continue
        ticks = max_transition_ticks(tl, clips[index - 1], clip, limit_ms)
        if not ticks:
            by_id[clip.clip_id] = clip.model_copy(update={"transition": None})
            continue
        tid = pool[turn % len(pool)]
        if tid == last and len(set(pool)) > 1:
            turn += 1
            tid = pool[turn % len(pool)]
        turn += 1
        last = tid
        if tid == "dip_to_white":
            ticks = min(ticks, _ticks(tl, 240))                       # a flash, not a fade
            fps = tl.settings.fps_num / tl.settings.fps_den
            if ticks * fps / tl.time_base.ticks_per_second < 2:
                continue
        by_id[clip.clip_id] = clip.model_copy(update={"transition": _transition(tid, ticks)})
        placed += 1
    return tl.model_copy(update={"clips": [by_id[c.clip_id] for c in tl.clips]}), placed


def _with_effects(clip: EditorClip, effects: list[EffectRef]) -> EditorClip:
    kept = [e for e in clip.effects if e.effect_id not in {x.effect_id for x in effects}]
    return clip.model_copy(update={"effects": kept + effects})


def look_effects(look: str, mood: Mood, style: Style) -> list[EffectRef]:
    strength = {"clean": 0.0, "auto": 0.45, "bold": 0.7}[style] or 0.45
    effects = [EffectRef(effect_id=f"look_{look}", parameters={"amount": strength})]
    if look in ("cinematic", "vintage", "matte", "mono") or style == "bold":
        effects.append(EffectRef(effect_id="vignette", parameters={"amount": 0.35 if style == "bold" else 0.25}))
    if look in ("vintage",) or (style == "bold" and look == "cinematic"):
        effects.append(EffectRef(effect_id="grain", parameters={"amount": 0.25}))
    return effects


def default_look(mood: Mood, style: Style) -> str | None:
    if style == "clean":
        return None
    if style == "bold":
        return "cinematic"
    return "punchy" if mood == "energetic" else "warm"


def clear_style(tl: EditorTimeline, *, transitions: bool = True) -> EditorTimeline:
    """Remove what the style pass adds (camera moves, accents, looks; and transitions)."""
    clips = []
    for c in tl.clips:
        if c.track_id == "video":
            c = c.model_copy(update={"effects": [e for e in c.effects if e.effect_id not in STYLE_EFFECTS],
                                     **({"transition": None} if transitions else {})})
        clips.append(c)
    return tl.model_copy(update={"clips": clips})


def plan_style(tl: EditorTimeline, *, mood: Mood, style: Style = "auto", look: str | None = None,
               footage: Footage | None = None, transitions: bool = True) -> tuple[EditorTimeline, list[str]]:
    """Return a NEW timeline with the finishing pass and a few plain lines saying what was done."""
    if style not in STYLES:
        raise ValueError(f"unknown style: {style}")
    if look is not None and look not in LOOKS:
        raise ValueError(f"unknown look: {look}")
    tl = clear_style(tl, transitions=transitions)
    notes: list[str] = []
    if style == "clean" and look is None:
        return tl, ["Clean edit: hard cuts, no effects."]
    clips = video_clips(tl)
    accents = accent_cuts(tl, mood, style) if style != "clean" else []
    accent_index = {c.index for c in accents}
    # the biggest build-up gets a hard cut with a flash (energetic), not a transition
    biggest_rise = max(accents, key=lambda c: c.rise, default=None) if mood == "energetic" else None

    # 1. transitions on the accents
    if transitions and style != "clean" and accents:
        pool = (ENERGETIC_TRANSITIONS + (BOLD_EXTRA if style == "bold" else ())) if mood == "energetic" else CALM_TRANSITIONS
        limit = 300 if mood == "energetic" else 650
        on = sorted(accent_index - ({biggest_rise.index} if biggest_rise is not None else set()))
        tl, placed = set_transitions(tl, on, pool, limit)
        if placed:
            notes.append(f"{placed} transition{'s' if placed != 1 else ''} on the strongest beats; the other cuts stay "
                         f"hard cuts on the beat.")
        clips = video_clips(tl)

    by_id = {c.clip_id: c for c in tl.clips}
    punches = moves = steadied = lifted = 0
    for index, clip in enumerate(clips):
        effects: list[EffectRef] = []
        ms = _ms(tl, clip.duration_ticks)
        tags = clip_tags(clip, footage) if footage else None
        if style != "clean":
            if mood == "energetic" and index in accent_index and ms >= 300 and clip.transition is None:
                effects.append(EffectRef(effect_id="zoom_punch", parameters={
                    "at_ms": 0, "duration_ms": int(min(350, ms * 0.6)), "amount": 0.8 if style == "bold" else 0.55}))
                punches += 1
            if biggest_rise is not None and index == biggest_rise.index and clip.transition is None:
                effects.append(EffectRef(effect_id="flash", parameters={"at_ms": 0, "duration_ms": 220,
                                                                        "amount": 0.6}))
                if style == "bold" and ms >= 500:
                    effects.append(EffectRef(effect_id="shake", parameters={"at_ms": 0, "duration_ms": int(min(450, ms)),
                                                                            "amount": 0.5}))
            long_enough = ms >= (1100 if mood == "energetic" else 1400)
            if long_enough and not any(e.effect_id == "zoom_punch" for e in effects):
                scale = 1.06 if mood == "energetic" else 1.09
                if tags is not None and tags.scale == "wide" and index % 2:
                    effects.append(EffectRef(effect_id="pan_left" if index % 4 == 1 else "pan_right",
                                             parameters={"distance": 0.06, "duration_ms": int(ms)}))
                else:
                    effects.append(EffectRef(effect_id="zoom_in", parameters={
                        "from_scale": 1.0, "to_scale": scale, "duration_ms": int(ms)}))
                moves += 1
        if tags is not None and tags.shaky:
            effects.append(EffectRef(effect_id="stabilize", parameters={"amount": 0.6}))
            steadied += 1
        if tags is not None and tags.light == "dark" and not any(e.effect_id == "brightness" for e in clip.effects):
            effects.append(EffectRef(effect_id="brightness", parameters={"amount": 0.06}))
            lifted += 1
        chosen_look = look or default_look(mood, style)
        if chosen_look:
            effects.extend(look_effects(chosen_look, mood, style))
        if effects:
            by_id[clip.clip_id] = _with_effects(by_id[clip.clip_id], effects)
    tl = tl.model_copy(update={"clips": [by_id[c.clip_id] for c in tl.clips]})

    if punches:
        notes.append(f"Zoom punches on {punches} strong beat{'s' if punches != 1 else ''}"
                     + (" and a flash on the biggest build-up." if biggest_rise is not None else "."))
    if moves:
        notes.append(f"Slow camera moves on {moves} longer clip{'s' if moves != 1 else ''}.")
    if steadied:
        notes.append(f"Stabilised {steadied} shaky clip{'s' if steadied != 1 else ''}.")
    if lifted:
        notes.append(f"Lifted {lifted} dark clip{'s' if lifted != 1 else ''} a little.")
    chosen_look = look or default_look(mood, style)
    if chosen_look:
        notes.append(f"{chosen_look.capitalize()} look on every clip.")
    return tl, notes


def guess_mood(tl: EditorTimeline) -> Mood:
    """Energetic when the cuts are fast (under ~0.9 s on average)."""
    clips = video_clips(tl)
    if not clips:
        return "energetic"
    average = sum(_ms(tl, c.duration_ticks) for c in clips) / len(clips)
    return "energetic" if average < 900 else "calm"
