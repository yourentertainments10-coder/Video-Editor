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

import re
from dataclasses import dataclass
from typing import Literal

from poc.director.apply import TITLE_ASSET_ID
from poc.director.footage import Footage, clip_tags
from poc.render.sfx import SOUNDS
from poc.render.text import ANIMATIONS as TITLE_ANIMATIONS
from poc.timeline import AudioProperties, EditorClip, EditorTimeline, EffectRef, MediaAsset, TransitionRef

Style = Literal["clean", "auto", "bold"]
Mood = Literal["energetic", "calm"]
STYLES: tuple[str, ...] = ("clean", "auto", "bold")

# effect ids the style pass owns: re-styling replaces these, user effects with other ids stay
STYLE_EFFECTS = {
    "zoom_in", "zoom_out", "pan_left", "pan_right", "flash", "shake", "zoom_punch", "stabilize",
    "look_cinematic", "look_vintage", "look_warm", "look_cool", "look_punchy", "look_mono", "look_matte",
    "vignette", "grain", "glow", "rgb_split", "sharpen", "light_leak",
}
SFX_TRACK = "voice"            # sound effects sit on the voice track (mixed in, the music is not ducked)
SFX_PREFIX = "sfx_"
SFX_GAIN_DB = {"whoosh": -12.0, "whoosh_soft": -15.0, "impact": -10.0, "riser": -15.0}   # under the music
# name -> the generated sound's MediaAsset (poc.render.sfx; made and imported by the server)
Sfx = dict[str, MediaAsset]
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
    """Remove what the style pass adds (camera moves, accents, looks, sound effects; and transitions)."""
    clips = []
    for c in tl.clips:
        if c.clip_id.startswith(SFX_PREFIX):
            continue
        if c.track_id == "video":
            c = c.model_copy(update={"effects": [e for e in c.effects if e.effect_id not in STYLE_EFFECTS],
                                     **({"transition": None} if transitions else {})})
        clips.append(c)
    tl = tl.model_copy(update={"clips": clips})
    chosen = title_animation(tl)
    if chosen is not None and chosen[1] == "auto":
        tl = set_title_animation(tl, None)
    return tl


def add_sound_effects(tl: EditorTimeline, sfx: Sfx, *, mood: Mood, style: Style) -> tuple[EditorTimeline, int]:
    """Whooshes on the transitions, an impact on the flash (the drop) and, when bold, a riser
    into it. Never on top of the user's own voice-track clips; replaces earlier sound effects."""
    tl = tl.model_copy(update={"clips": [c for c in tl.clips if not c.clip_id.startswith(SFX_PREFIX)]})
    if not any(t.track_id == SFX_TRACK for t in tl.tracks):
        return tl, 0
    busy = [(c.start_tick, c.start_tick + c.duration_ticks) for c in tl.clips if c.track_id == SFX_TRACK]
    events: list[tuple[int, str]] = []                      # (tick where the sound starts, name)
    whoosh = "whoosh" if mood == "energetic" else "whoosh_soft"
    for clip in video_clips(tl):
        if clip.transition is not None and (mood == "energetic" or style == "bold"):
            events.append((clip.start_tick - _ticks(tl, SOUNDS[whoosh][1]), whoosh))
        if any(e.effect_id == "flash" for e in clip.effects):
            events.append((clip.start_tick, "impact"))
            if style == "bold":
                events.append((clip.start_tick - _ticks(tl, SOUNDS["riser"][0]), "riser"))
    placed = 0
    assets = list(tl.assets)
    clips = list(tl.clips)
    for start, name in sorted(events):
        asset = sfx.get(name)
        if asset is None or start < 0:
            continue
        length = min(_ticks(tl, SOUNDS[name][0]), tl.duration_ticks - start)
        if length <= _ticks(tl, 80):
            continue
        end = start + length
        if any(start < b_end and end > b_start for b_start, b_end in busy):
            continue
        busy.append((start, end))
        if asset.asset_id not in {a.asset_id for a in assets}:
            assets.append(asset)
        clips.append(EditorClip(clip_id=f"{SFX_PREFIX}{placed:03d}_{name}", asset_id=asset.asset_id, track_id=SFX_TRACK,
                                start_tick=start, duration_ticks=length, source_in_ms=0,
                                source_out_ms=int(round(_ms(tl, length))),
                                audio=AudioProperties(gain_db=SFX_GAIN_DB[name])))
        placed += 1
    return tl.model_copy(update={"clips": clips, "assets": assets}), placed


def plan_style(tl: EditorTimeline, *, mood: Mood, style: Style = "auto", look: str | None = None,
               footage: Footage | None = None, transitions: bool = True,
               sfx: Sfx | None = None, title_animation_id: str | None = None) -> tuple[EditorTimeline, list[str]]:
    """Return a NEW timeline with the finishing pass and a few plain lines saying what was done."""
    if style not in STYLES:
        raise ValueError(f"unknown style: {style}")
    if look is not None and look not in LOOKS:
        raise ValueError(f"unknown look: {look}")
    tl = clear_style(tl, transitions=transitions)
    notes: list[str] = []
    title = title_animation(tl)
    if title is not None and title_animation_id is not None:
        tl = set_title_animation(tl, title_animation_id)
        notes.append(f"Title with {TITLE_ANIMATION_NAMES[title_animation_id]}.")
    elif title is not None and title[1] == "auto" and style != "clean":
        text = next(a.text or "" for a in tl.assets if a.asset_id == TITLE_ASSET_ID)
        picked = pick_title_animation(mood, style, text)
        tl = set_title_animation(tl, picked, by="auto")
        notes.append(f"Title with {TITLE_ANIMATION_NAMES[picked]}.")
    if style == "clean" and look is None:
        return tl, notes + ["Clean edit: hard cuts, no effects."]
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
        if mood == "calm":
            on = on[::2]                     # calm: every other accent is a hard cut with a light leak
        tl, placed = set_transitions(tl, on, pool, limit)
        if placed:
            notes.append(f"{placed} transition{'s' if placed != 1 else ''} on the strongest beats; the other cuts stay "
                         f"hard cuts on the beat.")
        clips = video_clips(tl)

    by_id = {c.clip_id: c for c in tl.clips}
    punches = moves = steadied = lifted = leaks = 0
    for index, clip in enumerate(clips):
        effects: list[EffectRef] = []
        ms = _ms(tl, clip.duration_ticks)
        tags = clip_tags(clip, footage) if footage else None
        if style != "clean":
            if mood == "energetic" and index in accent_index and ms >= 300 and clip.transition is None:
                effects.append(EffectRef(effect_id="zoom_punch", parameters={
                    "at_ms": 0, "duration_ms": int(min(350, ms * 0.6)), "amount": 0.8 if style == "bold" else 0.55}))
                punches += 1
            if mood == "calm" and index in accent_index and clip.transition is None and ms >= 900:
                effects.append(EffectRef(effect_id="light_leak", parameters={
                    "at_ms": 0, "duration_ms": int(min(1200, ms * 0.7)), "amount": 0.55 if style == "bold" else 0.4}))
                leaks += 1
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
    if leaks:
        notes.append(f"Warm light leaks on {leaks} clip{'s' if leaks != 1 else ''}.")
    chosen_look = look or default_look(mood, style)
    if chosen_look:
        notes.append(f"{chosen_look.capitalize()} look on every clip.")
    if sfx and style != "clean":
        tl, sounds = add_sound_effects(tl, sfx, mood=mood, style=style)
        if sounds:
            notes.append(f"{sounds} sound effect{'s' if sounds != 1 else ''} (whooshes on transitions"
                         + (", an impact on the drop)." if mood == "energetic" else ")."))
    return tl, notes


# ---------------------------------------------------------------- animated titles

TITLE_ANIMATION_NAMES = {"fade": "a simple fade", "pop": "a pop", "slide_up": "a slide up",
                         "typewriter": "a typewriter reveal", "word_pop": "words popping in one by one",
                         "glow": "a neon glow", "lower_third": "a lower-third name bar"}
# (pattern, animation, may stand alone without the word "title" nearby)
_TITLE_ANIMATION_WORDS: tuple[tuple[str, str, bool], ...] = (
    (r"type ?writer|typing|typed out|type[- ]on|letter by letter", "typewriter", True),
    (r"word[- ]by[- ]word|word[- ]pops?|words? pop(?:ping)?(?: in)?", "word_pop", True),
    (r"lower[- ]third|name (?:bar|strap|tag)", "lower_third", True),
    (r"neon|glow(?:ing|y)?", "glow", False),
    (r"slides? up|slid(?:e|ing) in|slid(?:e|ing)|ris(?:e|ing)", "slide_up", False),
    (r"pop(?:[- ]?up|ping|s)?|bounc(?:e|y|ing)|punchy", "pop", False),
    (r"(?:simple|plain|just a|soft) fade|fade[- ]?in|fading|no animation|without animation|"
     r"(?:plain|simple|static) (?:title|text)", "fade", False),
)
_TITLE_WORD = re.compile(r"\b(?:title|titles|heading|text|caption|naam|name)\b")
_NOT_TITLE = r"(?!\s+(?:song|songs|music|track|beat|beats|transitions?|look|colou?rs?|grade))"


def find_title_animation(text: str, context: str | None = None) -> tuple[str, tuple[int, int]] | None:
    """The title animation a sentence asks for and where it was said, e.g. "title with a typewriter
    effect", "make the title pop", "lower third". ``text`` is lower-case. Words like "pop" or
    "glow" only count next to "title"/"text" (so "pop song" or "glow look" are not titles).
    ``context``: the same sentence before parts of it were blanked out (where "title" still shows)."""
    titles = [m.span() for m in _TITLE_WORD.finditer(context or text)]
    for pattern, animation, alone in _TITLE_ANIMATION_WORDS:
        for m in re.finditer(rf"\b(?:{pattern})\b(?:\s+(?:effect|animation|style|reveal|intro|entry))?{_NOT_TITLE}", text):
            near = any(abs(m.start() - e) <= 40 or abs(s - m.end()) <= 40 for s, e in titles
                       if not re.search(r"[.;!?]", (context or text)[min(e, m.end()):max(s, m.start())]))
            if alone or near:
                return animation, m.span()
    return None


def title_animation(tl: EditorTimeline) -> tuple[str, str] | None:
    """(animation, who chose it: "user" or "auto") of the Director's title, None without a title."""
    asset = next((a for a in tl.assets if a.asset_id == TITLE_ASSET_ID and a.kind == "text"), None)
    if asset is None:
        return None
    style = asset.metadata.get("title_style") if isinstance(asset.metadata, dict) else None
    if not isinstance(style, dict) or style.get("animation") not in TITLE_ANIMATIONS:
        return "fade", "auto"
    return style["animation"], "user" if style.get("by") == "user" else "auto"


def set_title_animation(tl: EditorTimeline, animation: str | None, *, by: str = "user") -> EditorTimeline:
    """Give the Director's title an animation (None: back to the plain fade). No title: unchanged."""
    if animation is not None and animation not in TITLE_ANIMATIONS:
        raise ValueError(f"unknown title animation: {animation}")
    assets = []
    for a in tl.assets:
        if a.asset_id == TITLE_ASSET_ID and a.kind == "text":
            metadata = {k: v for k, v in a.metadata.items() if k != "title_style"}
            if animation is not None:
                metadata["title_style"] = {"animation": animation, "by": by}
            a = a.model_copy(update={"metadata": metadata})
        assets.append(a)
    return tl.model_copy(update={"assets": assets})


def pick_title_animation(mood: Mood, style: Style, text: str) -> str:
    """Energetic: words pop in (a long title) or the whole title pops; calm: slides up (a short
    title) or types itself; bold adds a neon glow to calm edits."""
    words = len(text.split())
    if mood == "energetic":
        return "word_pop" if words >= 3 else "pop"
    if style == "bold":
        return "glow"
    return "slide_up" if words <= 3 else "typewriter"


def guess_mood(tl: EditorTimeline) -> Mood:
    """Energetic when the cuts are fast (under ~0.9 s on average)."""
    clips = video_clips(tl)
    if not clips:
        return "energetic"
    average = sum(_ms(tl, c.duration_ticks) for c in clips) / len(clips)
    return "energetic" if average < 900 else "calm"
