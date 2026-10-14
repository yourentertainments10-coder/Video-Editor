"""Local Director: requests about what is IN the footage ("remove the dark clips", "only
close-ups", "what's in clip 3?", "replace clip 4 with a close-up").

Footage facts come from poc.perceive.shots (faces, shot size, light, shake, movement), measured
offline per video file. This module only reads words and picks footage; the timeline change is
an ordinary Operation applied by poc.director.revise.apply_operations.
"""
from __future__ import annotations

import re
from collections import Counter

from poc.perceive.shots import NEEDS_FACES, TAG_LABELS, ShotMap, ShotTags, describe, find_segments
from poc.timeline import EditorClip, EditorTimeline, MediaAsset

# asset_id -> (the asset, its measured footage)
Footage = dict[str, tuple[MediaAsset, ShotMap]]

# word patterns -> tag. Shot-size nouns stand alone ("close-ups"); adjectives need a noun after
# them ("dark clips") or an "only/more" before them, so "make it darker" stays a colour change.
_NOUN_TAGS = [
    (r"close[\s-]?ups?|closeups?|close shots?|face shots?", "close-up"),
    (r"medium shots?|mid shots?", "medium"),
    (r"wide shots?|long shots?|wide angles?|crowd shots?|crowd", "wide"),
    (r"group shots?", "group"),
]
_ADJ_TAGS = [
    (r"without (?:faces?|people)|no[\s-](?:faces?|people)|faceless|empty", "no-faces"),
    (r"with (?:faces?|people)|faces?|people|log|chehre|chehra", "faces"),
    (r"dark|darker|dim|andhere|andhera|low[\s-]light", "dark"),
    (r"bright|brighter|roshni wale", "bright"),
    (r"shaky|shaking|wobbly|unstable|hilte hue|hilne wale|jerky", "shaky"),
    (r"steady|stable|smooth", "steady"),
    (r"busy|action|fast[\s-]moving", "busy"),
    (r"still|static|calm", "still"),
]
_NOUN = r"(?:clips?|shots?|parts?|ones|footage|scenes?|moments?|wale|waale|vale)"
_LIMIT = r"(?:only|sirf|just|mostly|more|zyada|jyada|keep)"


def tag_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, tag) for every footage phrase in ``text``, in order."""
    found: list[tuple[int, int, str]] = []

    def add(m: re.Match, tag: str) -> None:
        found.append((m.start(), m.end(), tag))

    for pattern, tag in _NOUN_TAGS:
        for m in re.finditer(rf"\b(?:{pattern})\b", text):
            add(m, tag)
    for pattern, tag in _ADJ_TAGS:
        for m in re.finditer(rf"\b(?:{pattern})\s+{_NOUN}\b|\b{_LIMIT}\s+(?:the\s+)?(?:{pattern})\b|"
                             rf"\b(?:with|use)\s+(?:a|an|the|some)?\s*(?:{pattern})\s+(?:one|{_NOUN})\b", text):
            add(m, tag)
        # "which clips are dark", "clips that are shaky"
        for m in re.finditer(rf"\b{_NOUN}\s+(?:that\s+|which\s+|jo\s+)?(?:are|is|look|looks|hai|hain)\s+"
                             rf"(?:too\s+|very\s+)?(?:{pattern})\b", text):
            add(m, tag)
    for m in re.finditer(rf"\b(?:{_NOUN}|video|reel|edit)\s+(with|without|having|jisme|jinme)\s+(?:a\s+)?(?:faces?|people|log)\b", text):
        add(m, "no-faces" if m.group(1) == "without" else "faces")
    # "which clips have faces?", "shots that show no people", "clips that don't have faces"
    for m in re.finditer(rf"\b{_NOUN}\s+(?:that\s+|which\s+)?(do(?:n't|nt| not)\s+|does(?:n't|nt| not)\s+)?"
                         rf"(?:have|has|show|shows|contain|contains)\s+(no\s+)?(?:any\s+)?(?:faces?|people)\b", text):
        add(m, "no-faces" if m.group(1) or m.group(2) else "faces")
    return sorted(found)


def tags_in(text: str) -> list[str]:
    """Footage tags a message talks about, in order of appearance."""
    seen: list[str] = []
    for _, _, tag in tag_spans(text):
        if tag not in seen and not (tag == "faces" and "no-faces" in seen):
            seen.append(tag)
    return seen


def clip_tags(clip: EditorClip, footage: Footage) -> ShotTags | None:
    entry = footage.get(clip.asset_id)
    if entry is None:
        return None
    start = clip.source_in_ms or 0
    end = clip.source_out_ms if clip.source_out_ms is not None else start + 1000
    return describe(entry[1], start, end)


def tags_sentence(tags: ShotTags) -> str:
    labels = tags.labels()
    if not labels:
        return "nothing stands out"
    return ", ".join(labels[:-1]) + (" and " if len(labels) > 1 else "") + labels[-1]


def _needs_faces_but_missing(tags: list[str], footage: Footage) -> bool:
    return any(t in NEEDS_FACES for t in tags) and footage and not any(m.faces_measured for _, m in footage.values())


def replace_sources(tl: EditorTimeline, clip_ids: list[str], wanted: list[str], avoid: list[str],
                    footage: Footage) -> tuple[EditorTimeline, int, int]:
    """Give each clip new footage of the same length that has the wanted tags (and none of the
    avoided ones); position, length, effects and transitions stay. Returns (timeline, replaced,
    not found). Footage already on the timeline is not used twice."""
    tl = tl.model_copy(deep=True)
    video = sorted((c for c in tl.clips if c.track_id == "video"), key=lambda c: c.start_tick)
    taken: dict[str, list[tuple[int, int]]] = {}
    for c in video:
        taken.setdefault(c.asset_id, []).append((c.source_in_ms or 0, c.source_out_ms or 0))
    replaced = missing = 0
    known_assets = {a.asset_id for a in tl.assets}
    for index, clip in enumerate(video):
        if clip.clip_id not in clip_ids:
            continue
        length = (clip.source_out_ms or 0) - (clip.source_in_ms or 0)
        if length <= 0:
            missing += 1
            continue
        own = (clip.source_in_ms or 0, clip.source_out_ms or 0)
        neighbours = {video[i].asset_id for i in (index - 1, index + 1) if 0 <= i < len(video)}
        # files that differ from the neighbours first, then the clip's own file, then the rest
        order = sorted(footage, key=lambda aid: (aid in neighbours, aid != clip.asset_id, aid))
        choice = None
        for aid in order:
            asset, shots = footage[aid]
            busy = [r for r in taken.get(aid, []) if not (aid == clip.asset_id and r == own)]
            starts = find_segments(shots, length, wanted, avoid=avoid, taken=busy)
            if starts:
                choice = (aid, asset, starts[0])
                break
        if choice is None:
            missing += 1
            continue
        aid, asset, start = choice
        if aid == clip.asset_id and own in taken.get(aid, []):
            taken[aid].remove(own)
        elif own in taken.get(clip.asset_id, []):
            taken[clip.asset_id].remove(own)
        taken.setdefault(aid, []).append((start, start + length))
        clip.asset_id = aid
        clip.source_in_ms = start
        clip.source_out_ms = start + length
        clip.score = None
        clip.rationale = {}
        if aid not in known_assets:
            tl.assets.append(asset)
            known_assets.add(aid)
        replaced += 1
    return tl, replaced, missing


def summarise_footage(shots: ShotMap, window_ms: int = 2000) -> list[str]:
    """A few words for a whole file: the labels that hold for most of it."""
    counts: Counter[str] = Counter()
    windows = 0
    for start in range(0, max(1, shots.duration_ms - window_ms // 2), window_ms):
        windows += 1
        tags = describe(shots, start, start + window_ms)
        for tag in ("close-up", "medium", "wide", "faces", "dark", "bright", "shaky", "busy", "still"):
            if tags.has(tag):
                counts[tag] += 1
    out = []
    scales = [tag for tag, _ in counts.most_common() if tag in ("close-up", "medium", "wide")]
    for tag, n in counts.most_common():
        if tag in scales[1:]:
            continue                                   # one shot size per file: the commonest
        share = n / max(1, windows)
        if share >= 0.5 or (tag in ("close-up", "dark", "shaky") and share >= 0.25):
            out.append(("mostly " if share < 0.85 and tag in ("close-up", "medium", "wide") else "") + TAG_LABELS[tag])
    return out[:4]
