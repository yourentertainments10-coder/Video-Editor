"""Apply the style parts of a brief (transitions, title) to an auto-edited timeline.

The frozen v0 cutter only takes a mood and a length, so a brief's other wishes are applied
to its result here, as ordinary editor data the user can still change.
"""
from __future__ import annotations

from poc.timeline import EditorClip, EditorTimeline, MediaAsset, TransitionRef

SOFT_FADE_MS = 400          # longest fade used for "soft" transitions
TITLE_MS = 3000             # how long an auto title stays on screen
TITLE_ASSET_ID = "director_title"
TITLE_CLIP_ID = "director_title_clip"


def _fade_ticks(timeline: EditorTimeline, previous: EditorClip, clip: EditorClip) -> int:
    """Longest centred fade (<= SOFT_FADE_MS) both clips and their source handles allow, in ticks."""
    tps = timeline.time_base.ticks_per_second
    fps = timeline.settings.fps_num / timeline.settings.fps_den
    ms = lambda ticks: ticks * 1000 / tps  # noqa: E731
    durations = {a.asset_id: a.duration_ms for a in timeline.assets}
    limit_ms = min(SOFT_FADE_MS, ms(previous.duration_ticks) / 2, ms(clip.duration_ticks) / 2)
    # a centred fade needs half its length of unused source after the outgoing clip ...
    if previous.source_out_ms is not None:
        source_len = durations.get(previous.asset_id)
        if source_len is None:
            return 0
        limit_ms = min(limit_ms, 2 * (source_len - previous.source_out_ms))
    # ... and before the incoming clip
    if clip.source_in_ms is not None:
        limit_ms = min(limit_ms, 2 * clip.source_in_ms)
    frames = 2 * int(limit_ms * fps / 2000)          # even number of whole frames
    if frames < 2:
        return 0
    return int(frames * 1000 / fps * tps / 1000)


def apply_style(timeline: EditorTimeline, *, transition_policy: str = "auto",
                title: str | None = None) -> EditorTimeline:
    """Return a new timeline with the brief's transitions and title. Never mutates the input."""
    if transition_policy not in ("auto", "cuts_only", "soft"):
        raise ValueError(f"unknown transition policy: {transition_policy}")
    clips = [clip.model_copy() for clip in timeline.clips]
    video = sorted((c for c in clips if c.track_id == "video"), key=lambda c: c.start_tick)
    if transition_policy == "cuts_only":
        for clip in video:
            clip.transition = None
    elif transition_policy == "soft":
        for previous, clip in zip(video, video[1:]):
            if previous.start_tick + previous.duration_ticks != clip.start_tick:
                continue                                   # a gap: nothing to blend
            ticks = _fade_ticks(timeline, previous, clip)
            clip.transition = TransitionRef(transition_id="fade", duration_ticks=ticks) if ticks else None
    assets = list(timeline.assets)
    if title:
        assets = [a for a in assets if a.asset_id != TITLE_ASSET_ID]
        clips = [c for c in clips if c.clip_id != TITLE_CLIP_ID]
        assets.append(MediaAsset(asset_id=TITLE_ASSET_ID, kind="text", text=title))
        clips.append(EditorClip(clip_id=TITLE_CLIP_ID, asset_id=TITLE_ASSET_ID, track_id="overlay",
                                start_tick=0, duration_ticks=min(TITLE_MS, timeline.duration_ticks)))
    return timeline.model_copy(update={"clips": clips, "assets": assets})
