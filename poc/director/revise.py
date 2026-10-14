"""Local Director, part 2: written revisions -> timeline operations (docs/LOCAL_DIRECTOR.md §4).

``parse_revision(text, timeline)`` reads one instruction ("remove clip 3", "shorten the intro
by 2 seconds", "add fades", "make clip 2 brighter", "title 'Goa' at the end", "undo") and
returns typed operations plus a plain-language summary. ``apply_operations`` turns them
into a NEW timeline; nothing is saved here. The UI shows the summary, and Apply goes
through the normal edit path (autosave + undo).

Rules (as for briefs): deterministic, closed set of intents, references are resolved
against the real timeline and never invented; anything unclear becomes a question.
Clips are numbered 1..N in time order on the video track (the UI shows these numbers).
"""
from __future__ import annotations

import re
import statistics
from typing import Literal

from pydantic import BaseModel, Field

from poc.director.apply import TITLE_ASSET_ID, TITLE_CLIP_ID, TITLE_MS, apply_style
from poc.director.brief import MAX_TITLE_CHARS, _normalise, _words_to_numbers
from poc.director.footage import Footage, clip_tags, replace_sources, tags_in, tags_sentence
from poc.director.style import (CALM_TRANSITIONS, ENERGETIC_TRANSITIONS, FAMILIES, STYLE_EFFECTS, accent_cuts, clear_style,
                                guess_mood, look_effects, plan_style, set_transitions)
from poc.perceive.shots import NEEDS_FACES, TAG_LABELS, TAG_SINGULAR
from poc.timeline import EditorClip, EditorTimeline, EffectRef, MediaAsset, TransitionRef

Intent = Literal["remove", "trim", "length", "transition", "look", "motion", "title", "undo", "redo",
                 "explain", "retime", "unsupported", "unknown", "multiple", "footage", "describe", "style"]

MIN_CLIP_MS = 200
FADE_MS = 400
BRIGHTNESS_STEP = 0.15
CONTRAST_STEP = 1.25
SATURATION_STEP = 1.3
BLUR_RADIUS = 6.0
ZOOM_SCALE = 1.2

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10, "pehla": 1, "pehli": 1, "doosra": 2, "doosri": 2,
            "dusra": 2, "dusri": 2, "teesra": 3, "teesri": 3}
CLIP_WORDS = r"(?:clip|clips|shot|shots|scene|scenes)"


class Operation(BaseModel):
    """One change. ``op`` decides which fields are used."""

    op: Literal["remove_clips", "trim_clip", "cut_range", "set_length", "set_transitions", "set_transition",
                "adjust_look", "clear_look", "add_motion", "set_title", "remove_title", "replace_source",
                "apply_style", "set_look", "add_effect", "style_transitions", "add_accents"]
    clip_ids: list[str] = Field(default_factory=list)
    side: Literal["start", "end"] | None = None
    ms: int | None = None
    policy: Literal["soft", "cuts_only"] | None = None
    transition: Literal["fade"] | None = None
    effect_id: str | None = None
    amount: float | None = None
    text: str | None = None
    at: Literal["start", "end"] | None = None
    style: str | None = None                           # apply_style: clean / auto / bold
    look: str | None = None                            # apply_style / set_look: cinematic, vintage, ...
    family: str | None = None                          # style_transitions: zoom, slide, glitch, ... or mix
    scope: Literal["all", "accents"] | None = None     # style_transitions / add_accents: every cut or the strong beats
    tags: list[str] = Field(default_factory=list)      # replace_source: footage the new shots must show
    avoid: list[str] = Field(default_factory=list)     # replace_source: footage they must not show


class RevisionResult(BaseModel):
    intent: Intent
    operations: list[Operation] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    answer: str | None = None
    mood: Literal["energetic", "calm"] | None = None     # for intent "retime"


# ---------------------------------------------------------------- timeline helpers

def video_clips(tl: EditorTimeline) -> list[EditorClip]:
    return sorted((c for c in tl.clips if c.track_id == "video"), key=lambda c: c.start_tick)


def _ms(tl: EditorTimeline, ticks: float) -> float:
    return ticks * 1000 / tl.time_base.ticks_per_second


def _ticks(tl: EditorTimeline, ms: float) -> int:
    return int(round(ms * tl.time_base.ticks_per_second / 1000))


def _frame_ms(tl: EditorTimeline) -> float:
    return 1000 * tl.settings.fps_den / tl.settings.fps_num


def _seconds(ms: float) -> str:
    return f"{ms / 1000:.2f}".rstrip("0").rstrip(".") + " s"


def _clock(ms: float) -> str:
    """Source position as people read it: 1:55.7 (or 7.2 s under a minute)."""
    seconds = ms / 1000
    if seconds < 60:
        return _seconds(ms)
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def _explain(number: int, name: str, clip) -> str:
    """Why the auto-edit chose a clip, in words (terms of poc/decide/cutter.py's score)."""
    r = clip.rationale or {}
    reasons = []
    if "motion_match" in r:
        m = r["motion_match"]
        fit = "fits the music's pace well" if m >= 0.8 else "fits the music's pace" if m >= 0.6 else "is the best pace match it had"
        reasons.append(f"its movement {fit} (motion match {m:.2f})")
    if "quality" in r:
        q = r["quality"]
        look = "sharp and well exposed" if q >= 0.7 else "acceptable" if q >= 0.45 else "on the soft side"
        reasons.append(f"the picture is {look} (quality {q:.2f})")
    if "reuse" in r:
        reasons.append("it is a different file from the clips just before it" if r["reuse"] == 0
                       else f"it repeats a file used just before (reuse {r['reuse']:.2f}), which counted against it")
    if "coverage" in r and r["coverage"] >= 0.5:
        reasons.append(f"this file was already used a lot (coverage {r['coverage']:.2f}), which counted against it")
    because = "; ".join(reasons) if reasons else "it had the best score"
    return (f"Clip {number} is {name} from {_clock(clip.source_in_ms or 0)}. "
            f"The auto-edit chose it (score {clip.score or 0:.2f}) because {because}.")


# ---------------------------------------------------------------- references

class _Refs(BaseModel):
    indices: list[int] = Field(default_factory=list)   # 1-based clip numbers
    everything: bool = False
    named: str | None = None                           # "intro", "ending", "clip 3", ...
    problems: list[str] = Field(default_factory=list)


def _resolve(text: str, clips: list[EditorClip], tl: EditorTimeline) -> _Refs:
    refs = _Refs()
    n = len(clips)

    def add(index: int, label: str) -> None:
        if 1 <= index <= n:
            if index not in refs.indices:
                refs.indices.append(index)
            refs.named = refs.named or label
        else:
            refs.problems.append(f"There is no clip {index}; the timeline has {n} clips.")

    for m in re.finditer(rf"\b{CLIP_WORDS}\s*#?\s*(\d+)\s*(?:-|to|through|till|se)\s*#?\s*(\d+)\b", text):
        a, b = int(m.group(1)), int(m.group(2))
        for i in range(min(a, b), max(a, b) + 1):
            add(i, f"clips {min(a, b)}-{max(a, b)}")
    for m in re.finditer(rf"\b{CLIP_WORDS}\s*(?:number\s*|no\.?\s*|#\s*)?(\d+)((?:\s*(?:,|and|&|aur)\s*#?\s*\d+)*)", text):
        add(int(m.group(1)), f"clip {m.group(1)}")
        for extra in re.findall(r"\d+", m.group(2) or ""):
            add(int(extra), f"clip {extra}")
    for m in re.finditer(rf"\b(\d+)(?:st|nd|rd|th)\s+{CLIP_WORDS}\b", text):
        add(int(m.group(1)), f"clip {m.group(1)}")
    for word, index in ORDINALS.items():
        for m in re.finditer(rf"\b{word}\s+(?:and\s+(\w+)\s+)?{CLIP_WORDS}\b", text):
            if re.search(rf"\b{word}\s+(?:to\s+)?last\b", text):
                continue
            add(index, f"clip {index}")
            if m.group(1) and m.group(1) in ORDINALS:
                add(ORDINALS[m.group(1)], f"clip {ORDINALS[m.group(1)]}")
    if re.search(rf"\b(?:second|2nd)\s+(?:to\s+)?last\b", text):
        add(n - 1, "the second-last clip")
    elif re.search(rf"\b(?:last|final|aakhri|akhri)\s+{CLIP_WORDS}\b", text):
        add(n, "the last clip")
    if re.search(r"\b(?:intro|opening|beginning|shuruat|start of the video)\b", text) and not refs.indices:
        add(1, "the intro (clip 1)")
    if re.search(r"\b(?:ending|outro|end of the video|the end|last part)\b", text) and not refs.indices:
        add(n, f"the ending (clip {n})")
    for m in re.finditer(r"\bat\s+(\d{1,2}):([0-5]\d)\b|\bat\s+(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)\b", text):
        seconds = int(m.group(1)) * 60 + int(m.group(2)) if m.group(1) else float(m.group(3))
        tick = _ticks(tl, seconds * 1000)
        hit = [i for i, c in enumerate(clips, 1) if c.start_tick <= tick < c.start_tick + c.duration_ticks]
        if hit:
            add(hit[0], f"the clip at {seconds:g} s (clip {hit[0]})")
        else:
            refs.problems.append(f"No clip plays at {seconds:g} s.")
    if re.search(r"\b(?:all clips|every clip|each clip|everything|whole video|entire video|all of it|sab|saare|sabhi)\b", text):
        refs.everything = True
    return refs


def _amount_ms(text: str) -> int | None:
    m = re.search(r"\bby\s+(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?)\b", text) or \
        re.search(r"\b(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?)\s+(?:shorter|less|kam|chhota|chota)\b", text) or \
        re.search(r"\b(?:remove|cut|trim|drop|lose)\s+(?:the\s+)?(?:first|last)?\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?)\b", text)
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    return int(round(value if unit == "ms" else value * 60000 if unit.startswith("m") and unit != "ms" else value * 1000))


def _target_length_ms(text: str) -> int | None:
    m = re.search(r"\bto\s+(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?)\b", text) or \
        re.search(r"\b(?:make|cut|trim|shorten|keep)\s+(?:it|this|the video|the edit|the whole video|everything|video)\s+"
                  r"(?:only\s+)?(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?)\b", text) or \
        re.search(r"\b(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?)\s+(?:long|ka|ki|total)\b", text) or \
        re.search(r"\bto\s+(\d{1,2}):([0-5]\d)\b", text)
    if not m:
        return None
    if m.group(2) and m.group(2).isdigit():
        return (int(m.group(1)) * 60 + int(m.group(2))) * 1000
    value, unit = float(m.group(1)), m.group(2)
    return int(round(value * 60000 if unit.startswith("m") else value * 1000))


_TITLE_RE = re.compile(r"(?:\"([^\"]{1,200})\"|'([^']{1,200})')")


# ---------------------------------------------------------------- the parser

_COMMAND_START = (r"(?:make|add|remove|delete|drop|shorten|trim|cut|zoom|pan|fade|crossfade|blur|unblur|title|"
                  r"put|change|use|no|get rid|set|keep|reset|clear|undo|redo|why)\b")
_SPLIT_RE = re.compile(rf"\s*(?:;|\.\s+|,?\s*\band then\b|,?\s*\bthen\b|,?\s*\bphir\b|,?\s*\baur\b\s+(?={_COMMAND_START})|"
                       rf",\s*(?={_COMMAND_START})|\s+\band\b\s+(?={_COMMAND_START}))\s*", re.IGNORECASE)


def split_instructions(text: str) -> list[str]:
    """'remove clip 2 and make it black and white' -> ['remove clip 2', 'make it black and white'].

    Splits only where a new command word starts, so 'remove clips 2 and 5' and
    'black and white' stay whole. Quoted titles are never split.
    """
    quoted = list(_TITLE_RE.finditer(text or ""))
    masked = text or ""
    for m in quoted:
        masked = masked[:m.start()] + "\0" * (m.end() - m.start()) + masked[m.end():]
    parts, last = [], 0
    for m in _SPLIT_RE.finditer(masked):
        if m.start() == 0:
            continue
        parts.append(text[last:m.start()])
        last = m.end()
    parts.append(text[last:])
    return [p.strip(" ,.") for p in parts if p.strip(" ,.")]


def _numbers(indices: list[int]) -> str:
    if len(indices) == 1:
        return f"clip {indices[0]}"
    return "clips " + ", ".join(str(i) for i in indices[:-1]) + f" and {indices[-1]}"


def _footage_request(t: str, clips: list[EditorClip], refs: "_Refs", ids: list[str], footage: Footage | None,
                     pending: int):
    """Requests about what the footage shows. Returns (intent, ops, summary, questions, answer) or None."""
    if re.search(r"\bstabili[sz]|\bsteady (?:it|them|the)\b|\bfix (?:the )?shak", t):
        return None                                      # stabilising is the finishing pass's job
    tags = tags_in(t)
    describing = bool(re.search(r"\bwhat(?:'s| is| are)\b[^?]*\b(?:in|on)\b|\bwhat does\b[^?]*\bshow\b|"
                                r"\bkya (?:hai|dikh)|\bdescribe\b|\bkaisa (?:hai|dikh)", t))
    if not tags and not (describing and ids):
        return None
    footage = footage or {}
    needed = {clips[i - 1].asset_id for i in refs.indices} if (describing and not tags) else {c.asset_id for c in clips}
    if not footage or (pending and not needed <= set(footage)):
        wait = (f"I'm still watching your footage ({pending} file{'s' if pending != 1 else ''} to go). "
                "Ask again in a moment.") if pending else "I haven't looked at this footage yet. Ask again in a moment."
        return "footage", [], [], [wait], None
    if tags and any(tag in NEEDS_FACES for tag in tags) and not any(m.faces_measured for _, m in footage.values()):
        return "footage", [], [], ["I can't see faces on this computer (this OpenCV has no face detector). "
                                   "Light, shake and movement still work: try 'remove the dark clips'."], None

    numbered = list(enumerate(clips, 1))
    seen = {c.clip_id: clip_tags(c, footage) for c in clips}

    def matching(wanted: list[str]) -> list[int]:
        return [i for i, c in numbered if seen[c.clip_id] is not None and any(seen[c.clip_id].has(w) for w in wanted)]

    # describe clips
    if describing and ids and not tags:
        lines = []
        for i in refs.indices[:6]:
            tg = seen[clips[i - 1].clip_id]
            lines.append(f"Clip {i}: {tags_sentence(tg) if tg else 'not looked at yet'}.")
        return "describe", [], [], [], " ".join(lines)

    label = " and ".join(TAG_LABELS[tag] for tag in tags)
    # which clips are ...
    if re.search(r"\b(?:which|kaun ?se|kaunse|kon ?se|list|show me|dikhao|how many|kitne)\b", t):
        hits = matching(tags)
        one = " or ".join(TAG_SINGULAR[tag] for tag in tags)
        answer = (f"{_numbers(hits)} is {one}." if len(hits) == 1 else f"{_numbers(hits)} are {label}." if hits
                  else f"None of the {len(clips)} clips are {label}.")
        return "describe", [], [], [], answer[0].upper() + answer[1:]

    negated = bool(re.search(r"\b(?:no|not|without|avoid|bina|mat|nahi|less|kam)\s+(?:\w+\s+)?(?:" +
                             "|".join(re.escape(w) for w in ("dark", "shaky", "bright", "busy", "still", "wide", "close",
                                                             "faces", "people", "crowd", "medium", "group", "steady")) + ")", t))
    removing = re.search(r"\b(?:remove|delete|drop|cut out|get rid of|hatao|hata do|nikalo|nikal do)\b", t)
    replacing = re.search(r"\b(?:replace|swap|change|exchange|badlo|badal do|fix)\b", t)
    keeping = re.search(r"\b(?:only|sirf|just|mostly|more|zyada|jyada|keep|use|rakho|chahiye|prefer)\b", t)

    if removing and not ids:
        hits = matching(tags)
        if not hits:
            return "footage", [], [], [f"None of the clips are {label}; nothing to remove."], None
        if len(hits) == len(clips):
            return "footage", [], [], [f"All {len(clips)} clips are {label}; removing them would leave nothing. "
                                       f"Try 'replace the {label}' instead."], None
        return ("footage", [Operation(op="remove_clips", clip_ids=[clips[i - 1].clip_id for i in hits])],
                [f"Remove the {label}: {_numbers(hits)}; later clips move earlier and the video gets shorter."], [], None)

    if replacing and ids:
        after = re.split(r"\bwith\b", t, maxsplit=1)
        wanted = tags_in(after[1]) if len(after) > 1 else []
        avoid = [] if wanted else tags
        if not wanted and not avoid:
            return "footage", [], [], ["Replace it with what? e.g. 'replace clip 3 with a close-up'."], None
        names = TAG_SINGULAR if len(ids) == 1 else TAG_LABELS
        what = " and ".join(names[w] for w in wanted) if wanted else "footage that is not " + label
        return ("footage", [Operation(op="replace_source", clip_ids=ids, tags=wanted, avoid=avoid)],
                [f"Swap {_numbers(refs.indices)} for {what}; length and timing stay the same."], [], None)

    if (replacing or negated) and not ids:
        hits = matching(tags)
        if not hits:
            return "footage", [], [], [f"None of the clips are {label}."], None
        return ("footage", [Operation(op="replace_source", clip_ids=[clips[i - 1].clip_id for i in hits], avoid=tags)],
                [f"Swap the {label} ({_numbers(hits)}) for other footage; length and timing stay the same."], [], None)

    if keeping:
        targets = [i for i, c in numbered if seen[c.clip_id] is None or not all(seen[c.clip_id].has(w) for w in tags)]
        if not targets:
            return "footage", [], [], [], f"All {len(clips)} clips are already {label}."
        return ("footage", [Operation(op="replace_source", clip_ids=[clips[i - 1].clip_id for i in targets], tags=tags)],
                [f"Use {label}: swap {_numbers(targets)} for {label} from your footage; cuts stay on the beat."], [], None)

    hits = matching(tags)
    return ("footage", [], [], [f"{_numbers(hits)[0].upper() + _numbers(hits)[1:]} {'is' if len(hits) == 1 else 'are'} "
                                f"{label}. Do you want to keep only {label}, remove them, or replace them?"
                                if hits else f"None of the clips are {label}."], None)


_LOOK_WORDS = {"cinematic": "cinematic", "movie": "cinematic", "filmy": "cinematic", "vintage": "vintage",
               "retro": "vintage", "old film": "vintage", "warm": "warm", "golden": "warm", "cool": "cool",
               "cold": "cool", "blue": "cool", "punchy": "punchy", "vivid": "punchy", "matte": "matte",
               "faded": "matte", "film": "matte"}
_TEXTURES = [(r"\b(?:film )?grain\b|\bgrainy\b", "grain", "film grain"), (r"\bvignette\b", "vignette", "a vignette"),
             (r"\bglow(?:ing|y)?\b|\bbloom\b|\bdreamy glow\b", "glow", "a soft glow"),
             (r"\brgb split\b|\bchromatic\b|\bglitch (?:look|effect|style)\b", "rgb_split", "an RGB-split glitch look")]
_FAMILY_WORDS = [(r"zoom(?:ing)?", "zoom"), (r"slid(?:e|es|ing)|push", "slide"), (r"wipes?", "wipe"),
                 (r"whip|blur(?:ry)?|swish", "blur"), (r"glitch(?:y)?|pixel(?:ated|s|ize)?|digital", "glitch"),
                 (r"circle|circular|iris|round", "circle"), (r"(?:white )?flash(?:y)?", "flash"),
                 (r"(?:dip to )?black", "black"), (r"dissolve", "dissolve"),
                 (r"mixed|different|cool|fancy|random|creative|stylish|nice|good|best|cinematic|varied", "mix")]
_FAMILY_NAMES = {"zoom": "zoom", "slide": "slide", "wipe": "wipe", "blur": "whip-blur", "glitch": "glitch",
                 "circle": "circle", "flash": "white-flash", "black": "dip-to-black", "dissolve": "dissolve",
                 "mix": "mixed"}


def _style_request(t: str, clips: list[EditorClip], ids: list[str], refs: "_Refs", scope_all: bool,
                   scope_text: str, footage: Footage | None):
    """Styles, looks, textures, transition families and beat accents. Returns
    (intent, ops, summary, questions) or None."""
    everywhere = bool(re.search(r"\b(?:all|every|each|har|sab|saare|sabhi)\b", t))
    look = next((name for word, name in _LOOK_WORDS.items()
                 if re.search(rf"\b{word}\s+(?:look|style|grade|grading|colou?rs?|feel|vibe|tone|filter)\b|"
                              rf"\bmake it (?:look )?(?:more )?{word}\b|\b{word} (?:wala|jaisa)\b", t)), None)

    # clean / remove everything the finishing pass adds
    if re.search(r"\b(?:remove|clear|no|without|hatao|hata do|drop)\s+(?:all\s+)?(?:the\s+)?(?:styling|style)\b|"
                 r"\bclean (?:it up|edit|cuts)\b|\bplain cuts\b|\bstart (?:clean|plain)\b", t) and not ids:
        return "style", [Operation(op="apply_style", style="clean")], [
            "Remove the transitions, camera moves, beat accents and looks; keep hard cuts on the beat."], []

    # the whole finishing pass
    if re.search(r"\b(?:style it|make it (?:look )?(?:pro|professional|better|awesome|amazing|fire|lit|viral|cool)|"
                 r"add (?:some )?effects|auto(?:matic)? effects|effects (?:lagao|daalo|dalo|add karo)|jazz it up|"
                 r"spice it up|polish it|finish it|edit (?:it )?like a pro|make it pop|style karo)\b", t):
        bold = bool(re.search(r"\b(?:bold|crazy|lots of|more|zyada|jyada|heavy|extreme|max)\b", t))
        style = "bold" if bold else "auto"
        words = (f"{'Bold' if bold else 'Auto'} finishing pass: transitions on the strongest beats, beat accents, "
                 f"slow camera moves" + (f" and a {look} look." if look else " and a colour look."))
        return "style", [Operation(op="apply_style", style=style, look=look)], [words], []

    # a colour look
    if look:
        target = [c.clip_id for c in clips] if scope_all else ids
        return "style", [Operation(op="set_look", look=look, clip_ids=target)], [f"{look.capitalize()} look on {scope_text}."], []

    # textures
    for pattern, effect_id, label in _TEXTURES:
        if re.search(pattern, t):
            target = [c.clip_id for c in clips] if scope_all else ids
            remove = bool(re.search(r"\b(?:remove|no|without|hatao|less)\b", t))
            return "style", [Operation(op="add_effect", effect_id=effect_id, clip_ids=target, amount=0.0 if remove else 0.5)], [
                f"{'Remove' if remove else 'Add'} {label} {'from' if remove else 'on'} {scope_text}."], []

    # stabilise
    if re.search(r"\b(?:stabili[sz]e|stabili[sz]ation|steady (?:it|them|the)|less shaky|fix (?:the )?shak(?:e|y|ing))", t):
        target = ids
        if not target:
            shaky = [c for c in clips if footage and (tg := clip_tags(c, footage)) is not None and tg.shaky]
            target = [c.clip_id for c in shaky] if shaky else [c.clip_id for c in clips]
        numbers = [i for i, c in enumerate(clips, 1) if c.clip_id in target]
        what = f"clip{'s' if len(numbers) > 1 else ''} {', '.join(map(str, numbers))}" if len(numbers) < len(clips) else "all clips"
        return "style", [Operation(op="add_effect", effect_id="stabilize", clip_ids=target, amount=0.6)], [
            f"Stabilise {what} (the picture gets slightly closer to hide the moving edges)."], []

    # beat accents
    accent = re.search(r"\b(flash(?:es)?|zoom punch(?:es)?|punch(?:es)?|beat zoom|zoom on (?:the )?beats?|shake|shakes)\b"
                       r"[^.]*\b(?:beats?|drops?|bass|hits?)\b|\b(?:beat|drop) (flash|zoom|shake)", t)
    if accent:
        word = (accent.group(1) or accent.group(2) or "").lower()
        effect_id = "flash" if "flash" in word else "shake" if "shake" in word else "zoom_punch"
        scope = "all" if everywhere or re.search(r"\bevery beat|\bbeats\b", t) and "drop" not in t else "accents"
        names = {"flash": "flashes", "shake": "camera shakes", "zoom_punch": "zoom punches"}
        where = "on the downbeats" if scope == "all" else "on the strongest beats"
        return "style", [Operation(op="add_accents", effect_id=effect_id, scope=scope)], [
            f"Add {names[effect_id]} {where}."], []

    # transition families: "zoom transitions", "glitch transitions on every cut", "different transitions"
    if re.search(r"\btransitions?\b", t) and not re.search(r"\b(?:remove|delete|no|without|drop|hatao|hata do|"
                                                          r"get rid of)\b", t):
        family = next((name for pattern, name in _FAMILY_WORDS if re.search(rf"\b(?:{pattern})\b", t)), None)
        if family is None and not re.search(r"\b(?:fade|fades|dissolve|smooth\w*|soft\w*)\b", t):
            family = "mix"
        if family and not ids:
            scope = "all" if everywhere else "accents"
            where = "every cut" if scope == "all" else "the strongest beats"
            return "style", [Operation(op="style_transitions", family=family, scope=scope)], [
                f"{_FAMILY_NAMES[family].capitalize()} transitions on {where}" +
                ("; the rest stay hard cuts on the beat." if scope == "accents" else " (where the clips are long enough).")], []
        if family and ids:
            return "style", [Operation(op="style_transitions", family=family, scope="all", clip_ids=ids)], [
                f"{_FAMILY_NAMES[family].capitalize()} transition into {scope_text}."], []
    return None


def parse_instructions(text: str, timeline: EditorTimeline, footage: Footage | None = None,
                       pending: int = 0) -> RevisionResult:
    """One or several instructions in one sentence. Clip numbers always refer to the timeline as
    it is now (before any of the changes), like people mean them."""
    parts = split_instructions(text)
    if len(parts) <= 1:
        return parse_revision(text, timeline, footage, pending)
    results = [parse_revision(part, timeline, footage, pending) for part in parts]
    single = {"undo", "redo", "explain", "retime", "describe"}
    if any(r.intent in single for r in results):
        return RevisionResult(intent="unknown", questions=[
            "Ask 'undo', 'why …' or 'faster/calmer' on its own, not together with other changes."])
    combined = RevisionResult(intent="multiple")
    for part, r in zip(parts, results):
        combined.operations.extend(r.operations)
        # "all 55 clips" is wrong once another part removes some: say "all clips"
        combined.summary.extend(re.sub(r"\ball \d+ clips\b", "all clips", line) for line in r.summary)
        combined.questions.extend(f"'{part}': {q}" for q in r.questions)
    return combined


def parse_revision(text: str, timeline: EditorTimeline, footage: Footage | None = None,
                   pending: int = 0) -> RevisionResult:
    """``footage``: what is in each video file (poc.perceive.shots), when it has been measured;
    ``pending``: how many files are still being looked at."""
    raw = _normalise(text or "")
    clips = video_clips(timeline)
    if not raw:
        return RevisionResult(intent="unknown", questions=["Type a change, e.g. 'remove clip 3' or 'add fades'."])
    quoted = _TITLE_RE.search(raw)
    title_text = next((g for g in quoted.groups() if g), None) if quoted else None
    lower = raw.lower()
    if quoted:                                        # quoted words are content, not instructions
        lower = lower[:quoted.start()] + " " * (quoted.end() - quoted.start()) + lower[quoted.end():]
    lower = re.sub(r"\b(?:half a second|half second|half a sec|aadha second|aadha sekend)\b", "0.5 second", lower)
    t = _words_to_numbers(lower)
    refs = _resolve(t, clips, timeline)
    ids = [clips[i - 1].clip_id for i in refs.indices]
    scope_all = refs.everything or not ids
    scope_text = (f"all {len(clips)} clips" if scope_all else
                  refs.named if len(ids) == 1 else f"clips {', '.join(str(i) for i in refs.indices)}")

    def result(intent: Intent, ops: list[Operation] | None = None, summary: list[str] | None = None,
               **kw) -> RevisionResult:
        return RevisionResult(intent=intent, operations=ops or [], summary=summary or [],
                              questions=list(refs.problems) + kw.pop("questions", []), **kw)

    if refs.problems and not ids and not refs.everything:
        return result("unknown")

    # 1. undo / redo
    if re.fullmatch(r"\s*(?:undo|undo that|undo it|go back|revert|wapas|pehle jaisa(?: karo)?)\s*[.!]?\s*", t):
        return result("undo", summary=["Undo the last change."])
    if re.fullmatch(r"\s*(?:redo|redo that|redo it)\s*[.!]?\s*", t):
        return result("redo", summary=["Redo the change."])

    # 2. questions about a clip
    if re.search(r"\b(?:why|kyun|kyon)\b", t):
        if not ids:
            return result("explain", questions=["Which clip? e.g. 'why clip 3?'"])
        clip = clips[refs.indices[0] - 1]
        asset = next((a for a in timeline.assets if a.asset_id == clip.asset_id), None)
        name = re.split(r"[\\/]", asset.uri or asset.asset_id)[-1] if asset else clip.asset_id
        if clip.rationale:
            answer = _explain(refs.indices[0], name, clip)
        else:
            answer = (f"Clip {refs.indices[0]} ({name}) was placed by hand or by a Director change, so there is "
                      f"no auto-edit reason.")
        tags = clip_tags(clip, footage or {})
        if tags is not None:
            answer += f" It shows: {tags_sentence(tags)}."
        return result("explain", answer=answer)

    # 2b. what is in the footage
    found = _footage_request(t, clips, refs, ids, footage, pending)
    if found is not None:
        intent, ops, summary, questions, answer = found
        return result(intent, ops, summary, questions=questions, answer=answer)

    # 3. speed changes are not renderable yet
    if re.search(r"\b(?:slow[\s-]?mo(?:tion)?|slowmo|speed up|speed it up|fast[\s-]?forward|reverse|\d(?:\.\d)?x speed|\d(?:\.\d)?x)\b", t) and ids:
        return result("unsupported", questions=["Speed changes (slow motion, 2x) are not supported by the renderer yet."])

    # 4. overall pace -> new auto-edit with another mood
    pace = re.search(r"\b(?:faster|quicker|more cuts|more energy|more energetic|snappier|tez|slower|calmer|fewer cuts|"
                     r"less cuts|more relaxed|dheere|less energy)\b", t)
    if pace and not ids and not re.search(r"\b(?:fade|fades|transition|transitions)\b", t):
        mood = "energetic" if re.search(r"faster|quicker|more cuts|energy|energetic|snappier|tez", pace.group(0)) \
            and not re.search(r"less energy", pace.group(0)) else "calm"
        return result("retime", mood=mood, summary=[f"Re-run the auto-edit as {mood}: the cutter decides the cut "
                                                    f"rhythm, so pace changes need a new auto-edit (you can undo it)."])

    # 5. title
    if re.search(r"\b(?:title|heading|caption|text|naam)\b", t):
        if re.search(r"\b(?:remove|delete|drop|no|hatao|hata do|without)\b", t) and not title_text:
            if not any(c.clip_id == TITLE_CLIP_ID for c in timeline.clips):
                return result("title", questions=["There is no auto title to remove."])
            return result("title", [Operation(op="remove_title")], ["Remove the title."])
        if not title_text:
            return result("title", questions=["Put the title in quotes, e.g. title 'Goa 2026'."])
        if len(title_text) > MAX_TITLE_CHARS:
            return result("title", questions=[f"Titles can be at most {MAX_TITLE_CHARS} characters."])
        at = "end" if re.search(r"\b(?:at the end|end|ending|outro|last|aakhir)\b", t) else "start"
        return result("title", [Operation(op="set_title", text=title_text, at=at)],
                      [f'Title "{title_text}" for {_seconds(min(TITLE_MS, _ms(timeline, timeline.duration_ticks)))} '
                       f"at the {at}."])

    # 5b. the finishing pass: styles, looks, textures, transition families, beat accents
    found = _style_request(t, clips, ids, refs, scope_all, scope_text, footage)
    if found is not None:
        intent, ops, summary, questions = found
        return result(intent, ops, summary, questions=questions)

    # 6. transitions
    if re.search(r"\b(?:fade|fades|faded|crossfade|crossfades|dissolve|transition|transitions|cuts only|hard cuts|"
                 r"just cuts|only cuts|smoother|smooth|softer)\b", t):
        remove = bool(re.search(r"\b(?:remove|delete|no|without|drop|hard cuts|just cuts|only cuts|cuts only|"
                                r"hatao|hata do|get rid of)\b", t))
        if ids:
            ops = [Operation(op="set_transition", clip_ids=[cid], transition=None if remove else "fade")
                   for cid in ids if cid != clips[0].clip_id]
            if not ops:
                return result("transition", questions=["Clip 1 has no clip before it to fade from."])
            numbers = [str(i) for i in refs.indices if i != 1]
            verb = "Remove the fade into" if remove else "Fade into"
            return result("transition", ops, [f"{verb} clip{'s' if len(numbers) > 1 else ''} {', '.join(numbers)}."])
        policy = "cuts_only" if remove else "soft"
        text_ = "Use hard cuts everywhere." if remove else "Soft fades between all clips (where the clips are long enough)."
        return result("transition", [Operation(op="set_transitions", policy=policy)], [text_])

    # 7. remove clips
    if re.search(r"\b(?:remove|delete|drop|cut out|get rid of|hatao|hata do|nikalo|nikal do)\b", t) and ids \
            and not re.search(r"\b(?:blur|effect|effects|zoom|title)\b", t) and _amount_ms(t) is None:
        if len(ids) >= len(clips):
            return result("remove", questions=["That would remove every clip."])
        numbers = ", ".join(str(i) for i in refs.indices)
        return result("remove", [Operation(op="remove_clips", clip_ids=ids)],
                      [f"Remove clip{'s' if len(ids) > 1 else ''} {numbers}; later clips move earlier "
                       f"and the video gets shorter."])

    # 8. overall length
    target = _target_length_ms(t)
    if target is not None and re.search(r"\b(?:video|it|total|whole|overall|edit|shorten|shorter|make|cut|trim)\b", t) and not ids:
        current = _ms(timeline, timeline.duration_ticks)
        if target >= current:
            return result("length", questions=[f"The video is already {_seconds(current)}; "
                                               f"making it longer needs a new auto-edit."])
        return result("length", [Operation(op="set_length", ms=target)],
                      [f"Shorten the video from {_seconds(current)} to {_seconds(target)} by trimming the end."])

    # 9. trim a clip
    if re.search(r"\b(?:shorten|shorter|trim|cut|reduce|chhota|chota|kam|longer|extend|lengthen|lamba)\b", t):
        if re.search(r"\b(?:longer|extend|lengthen|lamba|bada)\b", t):
            return result("trim", questions=["Making clips longer is not supported yet; try a new auto-edit."])
        amount = _amount_ms(t)
        if not ids:
            return result("trim", questions=["Which clip? e.g. 'shorten the intro by 1 second' or 'trim clip 3'."])
        if amount is None:
            amount = 1000
            note = " (no amount given: 1 s)"
        else:
            note = ""
        intro = re.search(r"\b(?:intro|opening|beginning|shuruat)\b", t) and refs.named and refs.named.startswith("the intro")
        ending = re.search(r"\b(?:ending|outro|the end|end of the video|last part)\b", t) and refs.named and refs.named.startswith("the ending")
        if intro or ending:
            where: Literal["start", "end"] = "start" if intro else "end"
            return result("trim", [Operation(op="cut_range", side=where, ms=amount)],
                          [f"Cut {_seconds(amount)} from the {'start' if intro else 'end'} of the video{note}"
                           f"{'; later clips move earlier' if intro else ''}."])
        side: Literal["start", "end"] = "start" if re.search(
            r"\b(?:intro|opening|beginning|start|shuruat|first \d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds?))\b", t) else "end"
        ops = [Operation(op="trim_clip", clip_ids=[cid], side=side, ms=amount) for cid in ids]
        return result("trim", ops, [f"Shorten {scope_text} by {_seconds(amount)} at its {side}{note}; "
                                    f"later clips move earlier."])

    # 10. look
    looks = [
        (r"\b(?:black and white|black & white|b&w|b/w|monochrome|grayscale|greyscale)\b", "saturation", "set", 0.0, "black and white"),
        (r"\b(?:brighter|lighter|more light|roshni|bright)\b", "brightness", "add", BRIGHTNESS_STEP, "brighter"),
        (r"\b(?:darker|dimmer|less bright|dark)\b", "brightness", "add", -BRIGHTNESS_STEP, "darker"),
        (r"\b(?:less contrast|flatter|softer contrast)\b", "contrast", "mul", 1 / CONTRAST_STEP, "with less contrast"),
        (r"\b(?:more contrast|contrasty|punchier|higher contrast|contrast)\b", "contrast", "mul", CONTRAST_STEP, "with more contrast"),
        (r"\b(?:less colou?rful|less saturated|muted|desaturated?|faded colou?rs?)\b", "saturation", "mul", 1 / SATURATION_STEP, "less colourful"),
        (r"\b(?:more colou?rful|colou?rful|vibrant|more saturated|saturated|more colou?r|rangeen)\b", "saturation", "mul", SATURATION_STEP, "more colourful"),
        (r"\b(?:remove (?:the )?blur|unblur|no blur|sharp(?:er|en)?)\b", "blur", "remove", 0.0, "sharp again (no blur)"),
        (r"\b(?:blur|blurry|blurred)\b", "blur", "set", BLUR_RADIUS, "blurred"),
    ]
    if re.search(r"\b(?:remove|clear|reset|no)\s+(?:all\s+)?(?:the\s+)?(?:effects|filters|looks?|colou?r changes)\b", t):
        target_ids = [c.clip_id for c in clips] if scope_all else ids
        return result("look", [Operation(op="clear_look", clip_ids=target_ids)], [f"Remove look effects from {scope_text}."])
    for pattern, effect, mode, amount, label in looks:
        if re.search(pattern, t):
            target_ids = [c.clip_id for c in clips] if scope_all else ids
            op = Operation(op="clear_look" if mode == "remove" else "adjust_look", clip_ids=target_ids,
                           effect_id=effect, amount=amount, text=mode)
            return result("look", [op], [f"Look: {label} on {scope_text}."])

    # 11. motion
    motion = re.search(r"\b(zoom in|zoom out|zoom|pan left|pan right|pan up|pan down)\b", t)
    if motion:
        if not ids:
            return result("motion", questions=["Which clip? e.g. 'zoom in on clip 2'."])
        effect = {"zoom": "zoom_in"}.get(motion.group(1), motion.group(1).replace(" ", "_"))
        return result("motion", [Operation(op="add_motion", clip_ids=ids, effect_id=effect)],
                      [f"Add a slow {motion.group(1).replace('zoom', 'zoom').replace('_', ' ')} to {scope_text}."])

    return result("unknown", questions=[
        "I don't understand that yet. Try: 'remove clip 3', 'shorten the intro by 1 second', "
        "'add fades', 'no transitions', 'make clip 2 brighter', 'black and white', 'zoom in on clip 4', "
        "\"title 'Goa 2026' at the end\", 'make it 20 seconds', 'faster', 'why clip 3?', 'undo'."])


# ---------------------------------------------------------------- applying operations

def _beat_interval_ms(tl: EditorTimeline) -> float | None:
    beats = sorted(m.position_tick for m in tl.markers if m.kind == "beat")
    gaps = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    return _ms(tl, statistics.median(gaps)) if len(gaps) >= 2 else None


def _snap_to_beats(tl: EditorTimeline, ms: float) -> tuple[float, int | None]:
    """Round a removal to whole beats so later cuts stay on the beat (when beats exist)."""
    interval = _beat_interval_ms(tl)
    if not interval:
        return ms, None
    beats = max(1, round(ms / interval))
    return beats * interval, beats


def _quantise(tl: EditorTimeline, ms: float) -> float:
    frame = _frame_ms(tl)
    return max(frame, round(ms / frame) * frame)


def _ripple(tl: EditorTimeline, clips: list[EditorClip], after_tick: int, delta: int) -> list[EditorClip]:
    """Move every non-music clip that starts at/after ``after_tick`` earlier by ``delta`` ticks."""
    out = []
    for c in clips:
        if c.track_id != "music" and c.start_tick >= after_tick:
            c = c.model_copy(update={"start_tick": max(0, c.start_tick - delta)})
        out.append(c)
    return out


def _fit_music_and_length(tl: EditorTimeline, clips: list[EditorClip]) -> EditorTimeline:
    visual_end = max((c.start_tick + c.duration_ticks for c in clips if c.track_id != "music"), default=0)
    fitted = []
    for c in clips:
        if c.track_id == "music" and c.start_tick + c.duration_ticks > visual_end:
            new_dur = max(1, visual_end - c.start_tick)
            update = {"duration_ticks": new_dur}
            if c.source_in_ms is not None:
                update["source_out_ms"] = c.source_in_ms + int(round(_ms(tl, new_dur)))
            c = c.model_copy(update=update)
        elif c.track_id == "overlay" and c.start_tick + c.duration_ticks > visual_end:
            if c.start_tick >= visual_end:
                continue
            c = c.model_copy(update={"duration_ticks": visual_end - c.start_tick})
        fitted.append(c)
    markers = [m for m in tl.markers if m.position_tick < visual_end]
    return tl.model_copy(update={"clips": fitted, "duration_ticks": max(1, visual_end), "markers": markers})


def _drop_invalid_transitions(tl: EditorTimeline) -> tuple[EditorTimeline, int]:
    """After a trim or removal a fade can become too long or lose its neighbour: drop it."""
    clips = list(tl.clips)
    video = sorted((c for c in clips if c.track_id == "video"), key=lambda c: c.start_tick)
    dropped = 0
    by_id = {c.clip_id: c for c in clips}
    for i, c in enumerate(video):
        if c.transition is None:
            continue
        prev = video[i - 1] if i else None
        ok = (prev is not None and prev.start_tick + prev.duration_ticks == c.start_tick and
              c.transition.duration_ticks <= 0.5 * min(prev.duration_ticks, c.duration_ticks))
        if not ok:
            by_id[c.clip_id] = c.model_copy(update={"transition": None})
            dropped += 1
    return tl.model_copy(update={"clips": [by_id[c.clip_id] for c in clips]}), dropped


def _remove_range(tl: EditorTimeline, clip: EditorClip, side: str, ms: float) -> EditorTimeline:
    delta = _ticks(tl, ms)
    clips = []
    for c in tl.clips:
        if c.clip_id == clip.clip_id:
            update: dict = {"duration_ticks": c.duration_ticks - delta}
            if side == "start" and c.source_in_ms is not None:
                update["source_in_ms"] = c.source_in_ms + int(round(ms))
            if side == "end" and c.source_out_ms is not None:
                update["source_out_ms"] = c.source_out_ms - int(round(ms))
            c = c.model_copy(update=update)
        clips.append(c)
    clips = _ripple(tl, clips, clip.start_tick + clip.duration_ticks, delta)
    return _fit_music_and_length(tl, clips)


def _widen_to_clip_edges(tl: EditorTimeline, start: int, end: int) -> tuple[int, int]:
    """Do not leave a sliver (< MIN_CLIP_MS) of a video clip at either edge of a cut range."""
    minimum = _ticks(tl, MIN_CLIP_MS)
    for c in video_clips(tl):
        cs, ce = c.start_tick, c.start_tick + c.duration_ticks
        if cs < start < ce and start - cs < minimum:
            start = cs
        if cs < end < ce and ce - end < minimum:
            end = ce
    return start, end


def _cut_range(tl: EditorTimeline, start: int, end: int) -> EditorTimeline:
    """Remove the time [start, end) from every visual track and close the gap."""
    delta = end - start
    out: list[EditorClip] = []
    for c in tl.clips:
        if c.track_id == "music":
            out.append(c)
            continue
        cs, ce = c.start_tick, c.start_tick + c.duration_ticks
        if ce <= start:
            out.append(c)
        elif cs >= end:
            out.append(c.model_copy(update={"start_tick": cs - delta}))
        elif cs >= start and ce <= end:
            continue                                            # entirely inside: removed
        elif cs < start and ce > end:                           # the range is inside this clip
            update = {"duration_ticks": c.duration_ticks - delta}
            if c.source_out_ms is not None:
                update["source_out_ms"] = c.source_out_ms - int(round(_ms(tl, delta)))
            out.append(c.model_copy(update=update))
        elif cs < start:                                        # clip runs into the range: cut its end
            update = {"duration_ticks": start - cs}
            if c.source_out_ms is not None:
                update["source_out_ms"] = c.source_out_ms - int(round(_ms(tl, ce - start)))
            out.append(c.model_copy(update=update))
        else:                                                   # clip starts inside the range: cut its start
            update = {"start_tick": start, "duration_ticks": ce - end}
            if c.source_in_ms is not None:
                update["source_in_ms"] = c.source_in_ms + int(round(_ms(tl, end - cs)))
            out.append(c.model_copy(update=update))
    return _fit_music_and_length(tl, out)


def apply_operations(timeline: EditorTimeline, operations: list[Operation],
                     footage: Footage | None = None) -> tuple[EditorTimeline, list[str]]:
    """Apply operations to a copy of ``timeline``. Returns (new timeline, extra notes).
    ``footage`` is needed only for "replace_source" (finding shots that show something).

    Raises ValueError for an operation that cannot be applied (e.g. a clip would become
    shorter than 200 ms); the input is never changed.
    """
    tl = timeline.model_copy(deep=True)
    notes: list[str] = []
    for op in operations:
        clips = video_clips(tl)
        by_id = {c.clip_id: c for c in tl.clips}
        gone = [cid for cid in op.clip_ids if cid not in by_id]
        if gone and op.op in ("adjust_look", "clear_look") and len(gone) < len(op.clip_ids):
            op = op.model_copy(update={"clip_ids": [c for c in op.clip_ids if c in by_id]})   # "everything" after a removal
        elif gone:
            raise ValueError("one change refers to a clip that an earlier change in the same request removes")
        if op.op == "remove_clips":
            for cid in sorted(op.clip_ids, key=lambda i: -by_id[i].start_tick):   # last first
                clip = {c.clip_id: c for c in tl.clips}[cid]
                rest = [c for c in tl.clips if c.clip_id != cid]
                rest = _ripple(tl, rest, clip.start_tick + clip.duration_ticks, clip.duration_ticks)
                tl = _fit_music_and_length(tl, rest)
            if tl.markers:
                notes.append("Removed whole clips, so later cuts stay on the beat.")
        elif op.op == "trim_clip":
            for cid in op.clip_ids:
                clip = {c.clip_id: c for c in tl.clips}[cid]
                ms, beats = _snap_to_beats(tl, float(op.ms or 0))
                ms = _quantise(tl, ms)
                if _ms(tl, clip.duration_ticks) - ms < MIN_CLIP_MS:
                    raise ValueError(f"clip {clips.index(clip) + 1} is only {_seconds(_ms(tl, clip.duration_ticks))} "
                                     f"long; it cannot lose {_seconds(ms)}")
                tl = _remove_range(tl, clip, op.side or "end", ms)
                if beats:
                    notes.append(f"Trimmed {_seconds(ms)} ({beats} beat{'s' if beats > 1 else ''}) so later cuts "
                                 f"stay on the beat.")
        elif op.op == "cut_range":
            ms, beats = _snap_to_beats(tl, float(op.ms or 0))
            ms = _quantise(tl, ms)
            total = _ms(tl, tl.duration_ticks)
            if ms >= total - MIN_CLIP_MS:
                raise ValueError(f"the video is only {_seconds(total)} long; it cannot lose {_seconds(ms)}")
            start, end = (0, _ticks(tl, ms)) if op.side == "start" else (tl.duration_ticks - _ticks(tl, ms), tl.duration_ticks)
            start, end = _widen_to_clip_edges(tl, start, end)
            tl = _cut_range(tl, start, end)
            if beats and op.side == "start":
                notes.append(f"Cut {_seconds(_ms(tl, end - start))} (about {beats} beat{'s' if beats > 1 else ''}) so later "
                             f"cuts stay on the beat.")
        elif op.op == "set_length":
            target = _ticks(tl, op.ms or 0)
            while tl.duration_ticks > target:
                clips = video_clips(tl)
                last = clips[-1]
                excess = tl.duration_ticks - target
                if len(clips) > 1 and last.duration_ticks - excess < _ticks(tl, MIN_CLIP_MS):
                    rest = [c for c in tl.clips if c.clip_id != last.clip_id]
                    tl = _fit_music_and_length(tl, rest)
                else:
                    if last.duration_ticks - excess < _ticks(tl, MIN_CLIP_MS):
                        raise ValueError("the video cannot be that short")
                    tl = _remove_range(tl, last, "end", _ms(tl, excess))
        elif op.op == "set_transitions":
            tl = apply_style(tl, transition_policy=op.policy or "soft")
        elif op.op == "set_transition":
            for cid in op.clip_ids:
                index = [c.clip_id for c in clips].index(cid)
                prev, clip = clips[index - 1], clips[index]
                if op.transition is None:
                    new = clip.model_copy(update={"transition": None})
                else:
                    tmp = apply_style(tl.model_copy(update={"clips": [prev, clip.model_copy(update={"transition": None})]}),
                                      transition_policy="soft")
                    fade = {c.clip_id: c for c in tmp.clips}[cid].transition
                    if fade is None:
                        raise ValueError(f"clip {index + 1} or the clip before it is too short (or has no spare "
                                         f"footage) for a fade")
                    new = clip.model_copy(update={"transition": fade})
                tl = tl.model_copy(update={"clips": [new if c.clip_id == cid else c for c in tl.clips]})
        elif op.op in ("adjust_look", "clear_look"):
            updated = []
            for c in tl.clips:
                if c.clip_id in op.clip_ids:
                    c = c.model_copy(update={"effects": _look(c.effects, op)})
                updated.append(c)
            tl = tl.model_copy(update={"clips": updated})
        elif op.op == "add_motion":
            updated = []
            for c in tl.clips:
                if c.clip_id in op.clip_ids:
                    dur = int(_ms(tl, c.duration_ticks))
                    params = ({"from_scale": 1.0, "to_scale": ZOOM_SCALE, "duration_ms": dur} if op.effect_id == "zoom_in" else
                              {"from_scale": ZOOM_SCALE, "to_scale": 1.0, "duration_ms": dur} if op.effect_id == "zoom_out" else
                              {"distance": 0.1, "duration_ms": dur})
                    effects = [e for e in c.effects if e.effect_id not in ("zoom_in", "zoom_out", "pan_left", "pan_right",
                                                                         "pan_up", "pan_down")]
                    effects.append(EffectRef(effect_id=op.effect_id, parameters=params))
                    c = c.model_copy(update={"effects": effects})
                updated.append(c)
            tl = tl.model_copy(update={"clips": updated})
        elif op.op == "set_title":
            start = 0 if op.at == "start" else max(0, tl.duration_ticks - _ticks(tl, TITLE_MS))
            assets = [a for a in tl.assets if a.asset_id != TITLE_ASSET_ID]
            assets.append(MediaAsset(asset_id=TITLE_ASSET_ID, kind="text", text=op.text))
            rest = [c for c in tl.clips if c.clip_id != TITLE_CLIP_ID]
            overlay_busy = [c for c in rest if c.track_id == "overlay" and c.start_tick < start + _ticks(tl, TITLE_MS)
                            and c.start_tick + c.duration_ticks > start]
            if overlay_busy:
                raise ValueError("the overlay track already has something there")
            rest.append(EditorClip(clip_id=TITLE_CLIP_ID, asset_id=TITLE_ASSET_ID, track_id="overlay",
                                   start_tick=start, duration_ticks=min(_ticks(tl, TITLE_MS), tl.duration_ticks)))
            tl = tl.model_copy(update={"assets": assets, "clips": rest})
        elif op.op == "remove_title":
            tl = tl.model_copy(update={"assets": [a for a in tl.assets if a.asset_id != TITLE_ASSET_ID],
                                       "clips": [c for c in tl.clips if c.clip_id != TITLE_CLIP_ID]})
        elif op.op == "apply_style":
            tl, style_notes = plan_style(tl, mood=guess_mood(tl), style=op.style or "auto", look=op.look,
                                         footage=footage, transitions=True)
            notes.extend(style_notes)
        elif op.op == "set_look":
            mood = guess_mood(tl)
            looks = look_effects(op.look or "cinematic", mood, "auto")
            targets = set(op.clip_ids) or {c.clip_id for c in clips}
            updated = []
            for c in tl.clips:
                if c.clip_id in targets:
                    kept = [e for e in c.effects if not e.effect_id.startswith("look_")
                            and e.effect_id not in {x.effect_id for x in looks}]
                    c = c.model_copy(update={"effects": kept + looks})
                updated.append(c)
            tl = tl.model_copy(update={"clips": updated})
        elif op.op == "add_effect":
            targets = set(op.clip_ids) or {c.clip_id for c in clips}
            updated = []
            for c in tl.clips:
                if c.clip_id in targets:
                    kept = [e for e in c.effects if e.effect_id != op.effect_id]
                    if op.amount:
                        kept.append(EffectRef(effect_id=op.effect_id, parameters={"amount": op.amount}))
                    c = c.model_copy(update={"effects": kept})
                updated.append(c)
            tl = tl.model_copy(update={"clips": updated})
        elif op.op == "style_transitions":
            mood = guess_mood(tl)
            pool = FAMILIES.get(op.family or "mix", ())
            if op.family == "mix":
                pool = ENERGETIC_TRANSITIONS if mood == "energetic" else CALM_TRANSITIONS
            limit = 320 if mood == "energetic" else 650
            if op.clip_ids:
                indices = [i for i, c in enumerate(clips) if c.clip_id in op.clip_ids and i > 0]
            elif op.scope == "all":
                indices = list(range(1, len(clips)))
            else:
                indices = [c.index for c in accent_cuts(tl, mood, "auto")]
            tl, placed = set_transitions(tl, indices, pool, limit)
            if not placed:
                raise ValueError("the clips are too short (or have no spare footage) for transitions there")
            if placed < len(indices):
                notes.append(f"Placed {placed} of {len(indices)}; the other clips are too short or have no spare footage.")
        elif op.op == "add_accents":
            mood = guess_mood(tl)
            cuts = [c for c in accent_cuts(tl, mood, "bold" if op.scope == "all" else "auto")]
            if op.scope == "all":
                from poc.director.style import music_cuts
                cuts = [c for c in music_cuts(tl) if c.downbeat] or cuts
            by_id = {c.clip_id: c for c in tl.clips}
            added = 0
            for cut in cuts:
                clip = clips[cut.index]
                ms = _ms(tl, clip.duration_ticks)
                params = {"at_ms": 0, "duration_ms": int(min(350 if op.effect_id != "shake" else 450, ms * 0.8)),
                          "amount": 0.6}
                current = by_id[clip.clip_id]
                kept = [e for e in current.effects if e.effect_id != op.effect_id]
                by_id[clip.clip_id] = current.model_copy(update={"effects": kept + [EffectRef(effect_id=op.effect_id,
                                                                                               parameters=params)]})
                added += 1
            if not added:
                raise ValueError("I could not find strong beats at the cuts")
            tl = tl.model_copy(update={"clips": [by_id[c.clip_id] for c in tl.clips]})
            notes.append(f"Added to {added} cut{'s' if added != 1 else ''}.")
        elif op.op == "replace_source":
            if not footage:
                raise ValueError("the footage has not been looked at yet")
            tl, replaced, missing = replace_sources(tl, op.clip_ids, op.tags, op.avoid, footage)
            if not replaced:
                wanted = " and ".join(TAG_LABELS[t] for t in op.tags) or "different footage"
                raise ValueError(f"there is no unused footage with {wanted} of the right length")
            if missing:
                notes.append(f"Found matching footage for {replaced} of {replaced + missing} clips; "
                             f"the other {missing} stay as they were.")
    tl, dropped = _drop_invalid_transitions(tl)
    if dropped:
        notes.append(f"Dropped {dropped} fade{'s' if dropped > 1 else ''} that no longer fit.")
    return tl, notes


_LOOK_PARAM = {"brightness": "amount", "contrast": "factor", "saturation": "factor", "blur": "radius"}
_LOOK_LIMITS = {"brightness": (-1.0, 1.0), "contrast": (0.0, 3.0), "saturation": (0.0, 3.0), "blur": (0.0, 50.0)}
_NEUTRAL = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "blur": 0.0}


def _look(effects: list[EffectRef], op: Operation) -> list[EffectRef]:
    if op.op == "clear_look":
        drop = {op.effect_id} if op.effect_id else set(_LOOK_PARAM) | STYLE_EFFECTS   # "remove all effects": all of them
        return [e for e in effects if e.effect_id not in drop]
    effect = op.effect_id or ""
    param = _LOOK_PARAM[effect]
    current = next((e for e in effects if e.effect_id == effect), None)
    value = getattr(current.parameters, param) if current is not None else _NEUTRAL[effect]
    if op.text == "add":
        value = value + (op.amount or 0)
    elif op.text == "mul":
        value = value * (op.amount or 1)
    else:
        value = op.amount or 0.0
    low, high = _LOOK_LIMITS[effect]
    value = round(min(high, max(low, value)), 4)
    rest = [e for e in effects if e.effect_id != effect]
    if abs(value - _NEUTRAL[effect]) < 1e-9 and effect != "saturation":
        return rest
    return rest + [EffectRef(effect_id=effect, parameters={param: value})]
