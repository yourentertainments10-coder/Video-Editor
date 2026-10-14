"""Local Director, part 1: turn a short written brief into auto-edit settings.

Offline and rule-based (docs/LOCAL_DIRECTOR.md): a normaliser, whole-word phrase
matching against ``lexicon.json`` and a few regular expressions. The same text always
gives the same result. Anything it cannot settle (missing length or format, conflicting
words) becomes a question instead of a guess.

Example::

    parse_brief("20 second energetic reel, no transitions, title 'Goa 2026'", assets)
    -> duration_s=20, aspect="9:16", profile="reels_9x16", mood="energetic",
       transition_policy="cuts_only", title="Goa 2026"
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, Field

Aspect = Literal["16:9", "9:16", "1:1"]
Mood = Literal["energetic", "calm"]
TransitionPolicy = Literal["auto", "cuts_only", "soft"]

MIN_DURATION_S = 1.0
MAX_DURATION_S = 600.0
MAX_TITLE_CHARS = 80
DEFAULT_MOOD: Mood = "energetic"

_ASPECT_NAMES = {"16:9": "YouTube 16:9", "9:16": "Reels 9:16", "1:1": "Square 1:1"}
_POLICY_NAMES = {"cuts_only": "cuts only", "soft": "soft fades"}


class CreativeBrief(BaseModel):
    """What the brief asks for. ``None`` means "not given"; the UI keeps its own value."""

    duration_s: float | None = None
    aspect: Aspect | None = None
    profile: str | None = None
    mood: Mood | None = None
    transition_policy: TransitionPolicy = "auto"
    title: str | None = None
    only_asset_ids: list[str] = Field(default_factory=list)
    exclude_asset_ids: list[str] = Field(default_factory=list)
    prefer_tags: list[str] = Field(default_factory=list)   # footage to use ("close-up", ...; poc.perceive.shots)
    avoid_tags: list[str] = Field(default_factory=list)    # footage to keep out ("dark", "shaky", ...)


class BriefResult(BaseModel):
    brief: CreativeBrief
    understood: list[str] = Field(default_factory=list)   # what was read, in plain words
    defaults: list[str] = Field(default_factory=list)     # values chosen because none was given
    questions: list[str] = Field(default_factory=list)    # what the user still has to decide
    unknown_words: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class AssetRef:
    asset_id: str
    file_name: str
    kind: str = "video"


@lru_cache(maxsize=1)
def lexicon() -> dict:
    return json.loads((Path(__file__).with_name("lexicon.json")).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- normalising

_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'", " ": " "}
_TITLE_RE = re.compile(
    r"\b(?:title|heading|caption|text|naam|name)\b\s*(?:[:=]|\bis\b|\bshould be\b|\bsaying\b|\bthat says\b)?\s*"
    r"(?:\"([^\"]{1,200})\"|'([^']{1,200})')",
    re.IGNORECASE,
)
_TITLE_COLON_RE = re.compile(r"\b(?:title|heading|caption)\s*:\s*([^,.;\n\"]{1,200})", re.IGNORECASE)
_UNIT_S = r"(?:s|sec|secs|second|seconds|sekend|seconds?)"
_UNIT_M = r"(?:m|min|mins|minute|minutes|minat)"


def _normalise(text: str) -> str:
    for src, dst in _QUOTES.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip()


_ENGLISH_NUMBERS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                    "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
                    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}


def _words_to_numbers(text: str) -> str:
    """'twenty five seconds' -> '25 seconds', 'ek minute' -> '1 minute'.

    English number words are converted anywhere ("twenty or thirty seconds"); short or
    Hindi words that are also ordinary words ("a", "do", "das", "teen") only in front of a
    time unit.
    """
    numbers = lexicon()["number_words"]

    def convert(match: re.Match) -> str:
        parts = re.split(r"[\s-]+", match.group(1).lower())
        values = [numbers[p] for p in parts]
        if len(values) == 2 and values[0] % 10 == 0 and values[0] >= 20 and values[1] < 10:
            return f"{values[0] + values[1]} "
        return f"{values[-1]} "

    english = "|".join(sorted(_ENGLISH_NUMBERS, key=len, reverse=True))
    text = re.sub(rf"\b((?:{english})(?:[\s-]+(?:{english}))?)\b\s*", convert, text, flags=re.IGNORECASE)
    every = "|".join(sorted((re.escape(w) for w in numbers), key=len, reverse=True))
    unit = re.compile(rf"\b((?:{every}))\s+(?=(?:{_UNIT_S}|{_UNIT_M})\b)", re.IGNORECASE)
    return unit.sub(convert, text)


# ---------------------------------------------------------------- matching helpers

def _phrase_spans(text_lower: str, phrases: Sequence[str]) -> list[tuple[int, int, str]]:
    """Whole-word matches, longest phrase first, without overlaps. Returns (start, end, phrase)."""
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, int, str]] = []
    for phrase in sorted(set(phrases), key=lambda p: (-len(p), p)):
        for m in re.finditer(rf"(?<![\w:]){re.escape(phrase.lower())}(?![\w:])", text_lower):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), m.end(), phrase))
    return sorted(found)


def _preceded_by(text_lower: str, start: int, words: Sequence[str], window: int = 3) -> bool:
    """True when one of ``words`` is among the few words before ``start`` in the same clause."""
    clause = re.split(r"[,.;!?\n]| but | lekin ", text_lower[:start])[-1]
    before = re.findall(r"[\w']+", clause)[-window:]
    joined = " ".join(before)
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", joined) for w in words)


# ---------------------------------------------------------------- the parser

def parse_brief(text: str, assets: Sequence[AssetRef] = ()) -> BriefResult:
    lex = lexicon()
    brief = CreativeBrief()
    result = BriefResult(brief=brief)
    raw = _normalise(text or "")
    if not raw:
        result.questions.append("Describe the video, e.g. '30 second energetic reel'.")
        return result

    consumed: list[tuple[int, int]] = []          # character spans already explained
    lower = raw.lower()

    # --- title (taken first so its words are not read as instructions)
    title_match = _TITLE_RE.search(raw) or _TITLE_COLON_RE.search(raw)
    if title_match:
        title = next(g for g in title_match.groups() if g).strip()
        if len(title) > MAX_TITLE_CHARS:
            result.questions.append(f"The title is longer than {MAX_TITLE_CHARS} characters; please shorten it.")
        elif title:
            brief.title = title
            result.understood.append(f'Title: "{title}"')
        consumed.append(title_match.span())
    masked = "".join(" " if any(s <= i < e for s, e in consumed) else ch for i, ch in enumerate(lower))

    # --- asset names (before other words: file names can contain anything)
    matched_assets: list[tuple[int, int, AssetRef]] = []
    for asset in assets:
        if asset.kind != "video":
            continue
        name = asset.file_name.lower()
        stem = name.rsplit(".", 1)[0] if "." in name else name
        for candidate in (name, stem):
            if len(candidate) < 3:
                continue
            m = re.search(rf"(?<![\w]){re.escape(candidate)}(?![\w])", masked)
            if m:
                matched_assets.append((m.start(), m.end(), asset))
                break
    for start, end, asset in sorted(matched_assets, key=lambda item: item[0]):
        consumed.append((start, end))
        if _preceded_by(masked, start, lex["exclude_words"]):
            brief.exclude_asset_ids.append(asset.asset_id)
            result.understood.append(f"Leaving out: {asset.file_name}")
        elif _preceded_by(masked, start, lex["only_words"]):
            brief.only_asset_ids.append(asset.asset_id)
            result.understood.append(f"Only using: {asset.file_name}")
        else:
            result.defaults.append(f"{asset.file_name} is already used (all videos are used unless you say 'only' or 'skip').")
    masked = "".join(" " if any(s <= i < e for s, e in consumed) else ch for i, ch in enumerate(masked))

    # --- unsupported phrases: say so instead of silently ignoring them
    for start, end, phrase in _phrase_spans(masked, list(lex["unsupported"])):
        result.questions.append(f"'{phrase}': {lex['unsupported'][phrase]}.")
        consumed.append((start, end))
    masked = "".join(" " if any(s <= i < e for s, e in consumed) else ch for i, ch in enumerate(masked))

    # --- duration
    durations: list[float] = []
    spans: list[tuple[int, int]] = []
    ranged = False

    def add_duration(value: float, span: tuple[int, int]) -> None:
        durations.append(round(value, 3))
        spans.append(span)

    # "half a minute" must be read before "a minute" becomes "1 minute"
    half_spans = [m.span() for m in re.finditer(
        r"\b(?:half a minute|half minute|aadha minute|aadhe minute|aadha minat)\b", masked)]
    durations.extend(30.0 for _ in half_spans)
    masked = "".join(" " if any(s <= i < e for s, e in half_spans) else ch for i, ch in enumerate(masked))
    numeric = _words_to_numbers(masked)
    # "20 or 30 seconds", "20-30 sec": a range is a question, not a choice we make
    for m in re.finditer(rf"\b(\d+(?:\.\d+)?)\s*(?:or|to|-|\u2013)\s*(\d+(?:\.\d+)?)\s*(?:{_UNIT_S}|{_UNIT_M})\b", numeric):
        ranged = True
        spans.append(m.span())

    for m in re.finditer(rf"\b(\d+(?:\.\d+)?)\s*{_UNIT_M}\s*(?:and\s*)?(\d+(?:\.\d+)?)\s*{_UNIT_S}\b", numeric):
        add_duration(float(m.group(1)) * 60 + float(m.group(2)), m.span())
    for m in re.finditer(r"\b(\d{1,2}):([0-5]\d)\b(?!\s*(?:ratio|aspect))", numeric):
        if not any(s <= m.start() < e for s, e in spans) and m.group(0) not in ("9:16", "16:9", "1:1", "4:5", "4:3"):
            add_duration(float(int(m.group(1)) * 60 + int(m.group(2))), m.span())
    for m in re.finditer(rf"\b(\d+(?:\.\d+)?)\s*-?\s*{_UNIT_M}\b", numeric):
        if not any(s <= m.start() < e for s, e in spans):
            add_duration(float(m.group(1)) * 60, m.span())
    for m in re.finditer(rf"\b(\d+(?:\.\d+)?)\s*-?\s*{_UNIT_S}\b", numeric):
        if not any(s <= m.start() < e for s, e in spans):
            add_duration(float(m.group(1)), m.span())
    unique = sorted(set(durations))
    if ranged:
        result.questions.append("Which exact length? The brief gives a range.")
    elif len(unique) == 1:
        value = unique[0]
        if MIN_DURATION_S <= value <= MAX_DURATION_S:
            brief.duration_s = value
            result.understood.append(f"Length: {value:g} s")
        else:
            result.questions.append(f"{value:g} s is outside the supported length "
                                    f"({MIN_DURATION_S:g}-{MAX_DURATION_S:g} s).")
    elif len(unique) > 1:
        result.questions.append("Which length? I found " + ", ".join(f"{v:g} s" for v in unique) + ".")
    else:
        result.questions.append("How long should it be? e.g. '30 seconds'.")
    # blank out the duration text so its words are not read again
    numeric_masked = list(numeric)
    for s, e in spans:
        for i in range(s, e):
            numeric_masked[i] = " "
    masked = "".join(numeric_masked)

    # --- transitions (before mood: "no transitions" / "soft transitions" are phrases)
    policies: dict[str, list[str]] = {}
    for start, end, phrase in _phrase_spans(masked, [p for ps in lex["transitions"].values() for p in ps]):
        policy = next(k for k, ps in lex["transitions"].items() if phrase in ps)
        if policy == "soft" and _preceded_by(masked, start, lex["negations"], window=2):
            policy = "cuts_only"                   # "no fades"
        policies.setdefault(policy, []).append(phrase)
        if " " in phrase or phrase in ("smooth", "fades", "fade", "crossfade", "crossfades", "dissolve", "dissolves"):
            consumed.append((start, end))         # pure transition words are fully explained
    if len(policies) == 1:
        brief.transition_policy = next(iter(policies))  # type: ignore[assignment]
        words = ", ".join(f"'{w}'" for w in policies[brief.transition_policy])
        result.understood.append(f"Transitions: {_POLICY_NAMES[brief.transition_policy]} (from {words})")
    elif len(policies) > 1:
        result.questions.append("Cuts only or soft fades? The brief asks for both.")
    masked = "".join(" " if any(s <= i < e for s, e in consumed) else ch for i, ch in enumerate(masked))

    # --- aspect / format
    aspects: dict[str, list[str]] = {}
    for start, end, phrase in _phrase_spans(masked, [p for ps in lex["aspect"].values() for p in ps]):
        aspect = next(k for k, ps in lex["aspect"].items() if phrase in ps)
        aspects.setdefault(aspect, []).append(phrase)
        consumed.append((start, end))
    if len(aspects) == 1:
        aspect = next(iter(aspects))
        brief.aspect = aspect  # type: ignore[assignment]
        brief.profile = lex["profiles"][aspect]
        result.understood.append(f"Format: {_ASPECT_NAMES[aspect]} (from '{aspects[aspect][0]}')")
    elif len(aspects) > 1:
        result.questions.append("Which format? The brief mentions " +
                                " and ".join(_ASPECT_NAMES[a] for a in aspects) + ".")
    else:
        result.questions.append("Which format: YouTube 16:9, Reels 9:16 or Square 1:1?")
    masked = "".join(" " if any(s <= i < e for s, e in consumed) else ch for i, ch in enumerate(masked))

    # --- what the footage should (not) show: "mostly close-ups", "no dark shots"
    from poc.director.footage import tag_spans          # here: keeps the brief parser importable alone
    from poc.perceive.shots import TAG_LABELS
    for start, end, tag in tag_spans(masked):
        consumed.append((start, end))
        negated = _preceded_by(masked, start, lex["negations"] + ["avoid", "less", "kam"], window=2) or \
            bool(re.match(r"\s*(?:no|without|avoid)\b", masked[start:end]))
        target = brief.avoid_tags if negated else brief.prefer_tags
        if tag not in brief.prefer_tags and tag not in brief.avoid_tags:
            target.append(tag)
    if brief.prefer_tags:
        result.understood.append("Shots: " + ", ".join(TAG_LABELS[t] for t in brief.prefer_tags))
    if brief.avoid_tags:
        result.understood.append("Leave out: " + ", ".join(TAG_LABELS[t] for t in brief.avoid_tags))
    masked = "".join(" " if any(s <= i < e for s, e in consumed) else ch for i, ch in enumerate(masked))

    # --- mood
    moods: dict[str, list[str]] = {}
    opposite = {"energetic": "calm", "calm": "energetic"}
    for start, end, phrase in _phrase_spans(masked, [p for ps in lex["mood"].values() for p in ps]):
        consumed.append((start, end))
        mood = next(k for k, ps in lex["mood"].items() if phrase in ps)
        if _preceded_by(masked, start, lex["negations"], window=2):
            mood, phrase = opposite[mood], f"not {phrase}"     # "not too slow" -> energetic
        moods.setdefault(mood, []).append(phrase)
    if len(moods) == 1:
        brief.mood = next(iter(moods))  # type: ignore[assignment]
        words = ", ".join(f"'{w}'" for w in moods[brief.mood])
        result.understood.append(f"Mood: {brief.mood} (from {words})")
    elif len(moods) > 1:
        result.questions.append("Energetic or calm? The brief has words for both.")
    else:
        brief.mood = DEFAULT_MOOD
        result.defaults.append(f"Mood not given; using {DEFAULT_MOOD}.")
    masked = "".join(" " if any(s <= i < e for s, e in consumed) else ch for i, ch in enumerate(masked))

    # --- leftover words we did not understand (informational)
    stop = set(lex["stopwords"]) | set(lex["negations"]) | set(lex["exclude_words"]) | set(lex["only_words"])
    seen: set[str] = set()
    for word in re.findall(r"[a-z][a-z'\-]{2,}", masked):
        if word not in stop and word not in seen and not word.isdigit():
            seen.add(word)
            result.unknown_words.append(word)

    if brief.only_asset_ids and brief.exclude_asset_ids:
        overlap = set(brief.only_asset_ids) & set(brief.exclude_asset_ids)
        if overlap:
            result.questions.append("The same video is both 'only' and 'skip'.")
    return result


def selected_video_ids(brief: CreativeBrief, assets: Sequence[AssetRef]) -> list[str]:
    """The videos auto-edit should use for this brief (all videos unless 'only'/'skip')."""
    videos = [a.asset_id for a in assets if a.kind == "video"]
    chosen = [v for v in videos if v in brief.only_asset_ids] if brief.only_asset_ids else videos
    return [v for v in chosen if v not in brief.exclude_asset_ids]
