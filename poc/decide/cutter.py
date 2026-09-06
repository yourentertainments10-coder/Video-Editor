"""poc/decide/cutter.py: the deterministic greedy cutter.

Implements docs/MVP.md's Appendix scoring function verbatim (quoted in
full below, next to its implementation) and the candidate generation rule
from docs/MVP.md Section 6. This is the core MVP experiment: whether this
documented, deterministic scoring beats picking randomly on the same grid
(poc/decide/baseline.py). Nothing here is a more sophisticated system than
what is written in MVP.md -- no beam search, no optimization solver, no
embeddings, no ML.

Candidate generation (docs/MVP.md Section 6):

  "Candidates are not precomputed. For a slot of length L, the candidate
  set is every (file, in_ms) at 250 ms steps where in_ms + L fits inside
  the file with a 200 ms margin at each end."

Scoring (docs/MVP.md Appendix, "the cutter's scoring function"):

  For slot s with length L frames and mean music energy e_s:
    target_motion = params.motion_base + params.motion_scale * e_s

  For candidate (file f, in_ms i) with normalized curves over [i, i + L]:
    quality      = 0.6 * mean(sharpness_pct) + 0.4 * (1 - mean(clip_frac))
    motion_match = 1 - |mean(motion_pct) - target_motion|
    reuse        = 1.0 if f was used in the previous slot else 0.5 if in
                   the one before else 0
    coverage     = uses_so_far[f] / max(1, max(uses_so_far))

    score = 0.35*quality + 0.40*motion_match - 0.15*reuse - 0.10*coverage
    (candidates overlapping an already-used source range are excluded,
    not penalized)

  Pick the argmax per slot, in timeline order (greedy). Record the four
  terms as rationale.

``motion_pct``/``sharpness_pct`` are percentile ranks computed once across
every sample of every footage file in the set (docs/MVP.md Section 6:
"motion and sharpness are converted to percentile ranks across the whole
footage set"), reusing poc.decide.grid.percentile_rank -- the same
technique docs/MVP.md already applies to beat energy in that module.
``clip_frac`` is used as-is (already a 0..1 fraction, not
footage-set-relative like motion/sharpness), and ``luma`` is not part of
the v0 scoring formula at all -- neither of these is an omission, both
match the Appendix exactly as written.

One disclosed interpretation, since docs/MVP.md's Appendix does not spell
out the half-open interval convention: a candidate window [in_ms,
in_ms+L) is scored from whichever FootageMap samples have
``in_ms/1000 <= t_s < (in_ms+L)/1000`` -- the same half-open convention
used everywhere else in this project (Clip.start_frame/end_frame). A
candidate with zero samples in its window (possible only at very high
tempo relative to the 4 Hz sampling rate) is excluded, the same way an
overlapping-with-already-used candidate is excluded, rather than scored
with fabricated data.

Architecture constraints this module upholds: it reads only MusicMap and
FootageMap (never decodes video, never invokes FFmpeg, never mutates
either input); it always calls poc.timeline.validate() on the Timeline it
produces before returning; it has no knowledge of how the Timeline will
later be rendered.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from poc.decide.grid import build_grid, percentile_rank
from poc.decide.style import Mood, CutterParams, style_for_mood
from poc.perceive.footage import FootageMap
from poc.perceive.music import MusicMap
from poc.timeline.schema import TIMELINE_FPS, Clip, Music, Provenance, Source, Timeline, frame_to_ms
from poc.timeline.validate import validate

__all__ = [
    "CANDIDATE_STEP_MS",
    "CANDIDATE_MARGIN_MS",
    "CutterError",
    "ScoreBreakdown",
    "generate_candidates",
    "score_candidate",
    "cut",
]

CANDIDATE_STEP_MS = 250
CANDIDATE_MARGIN_MS = 200


class CutterError(RuntimeError):
    """Raised when no valid candidate exists for a slot, or the produced
    Timeline fails validation (which should not happen by construction,
    but is always checked rather than assumed)."""


class ScoreBreakdown(NamedTuple):
    quality: float
    motion_match: float
    reuse: float
    coverage: float
    score: float


# ---------------------------------------------------------------------------
# candidate generation (shared with poc.decide.baseline)
# ---------------------------------------------------------------------------


def generate_candidates(
    footage_maps: dict[str, FootageMap],
    slot_frames: int,
    *,
    fps: int = TIMELINE_FPS,
    margin_ms: int = CANDIDATE_MARGIN_MS,
    step_ms: int = CANDIDATE_STEP_MS,
) -> dict[str, list[int]]:
    """Every (file, in_ms) at ``step_ms`` steps where the slot fits inside
    the file with ``margin_ms`` at each end. A file too short for this
    slot length contributes an empty list, not an error -- the whole
    footage set may still have other files with room."""
    slot_length_ms = frame_to_ms(slot_frames, fps=fps)
    candidates: dict[str, list[int]] = {}
    for path, footage_map in footage_maps.items():
        max_in_ms = footage_map.duration_ms - margin_ms - slot_length_ms
        if max_in_ms < margin_ms:
            candidates[path] = []
            continue
        candidates[path] = list(range(margin_ms, max_in_ms + 1, step_ms))
    return candidates


def _overlaps_any(in_ms: int, out_ms: int, used_ranges: list[tuple[int, int]]) -> bool:
    return any(in_ms < used_out and used_in < out_ms for used_in, used_out in used_ranges)


# ---------------------------------------------------------------------------
# scoring (docs/MVP.md Appendix, quoted above)
# ---------------------------------------------------------------------------


def score_candidate(
    footage_map: FootageMap,
    in_ms: int,
    slot_length_ms: int,
    motion_pct_by_index: dict[int, float],
    sharpness_pct_by_index: dict[int, float],
    target_motion: float,
    reuse: float,
    coverage: float,
) -> ScoreBreakdown | None:
    """Score one candidate window. Returns None if no FootageMap sample
    falls inside [in_ms, in_ms + slot_length_ms) -- excluded, not scored
    with fabricated data (see module docstring)."""
    start_s = in_ms / 1000.0
    end_s = (in_ms + slot_length_ms) / 1000.0
    indices = [idx for idx, sample in enumerate(footage_map.samples) if start_s <= sample.t_s < end_s]
    if not indices:
        return None

    mean_sharpness_pct = float(np.mean([sharpness_pct_by_index[idx] for idx in indices]))
    mean_motion_pct = float(np.mean([motion_pct_by_index[idx] for idx in indices]))
    mean_clip_frac = float(np.mean([footage_map.samples[idx].clip_frac for idx in indices]))

    quality = 0.6 * mean_sharpness_pct + 0.4 * (1.0 - mean_clip_frac)
    motion_match = 1.0 - abs(mean_motion_pct - target_motion)
    score = 0.35 * quality + 0.40 * motion_match - 0.15 * reuse - 0.10 * coverage

    return ScoreBreakdown(quality=quality, motion_match=motion_match, reuse=reuse, coverage=coverage, score=score)


def _reuse_term(path: str, recent_files: list[str]) -> float:
    if len(recent_files) >= 1 and recent_files[-1] == path:
        return 1.0
    if len(recent_files) >= 2 and recent_files[-2] == path:
        return 0.5
    return 0.0


def _coverage_term(path: str, uses_so_far: dict[str, int]) -> float:
    max_uses = max(uses_so_far.values()) if uses_so_far else 0
    return uses_so_far.get(path, 0) / max(1, max_uses)


@dataclass
class _RankedFootage:
    """Percentile ranks of motion/sharpness, computed once across every
    sample of every file in the set (docs/MVP.md Section 6), indexed back
    by (path, local sample index) for O(1) lookup during scoring."""

    motion_pct: dict[str, dict[int, float]]
    sharpness_pct: dict[str, dict[int, float]]


def _rank_footage_set(footage_maps: dict[str, FootageMap]) -> _RankedFootage:
    paths: list[str] = []
    local_indices: list[int] = []
    motions: list[float] = []
    sharpnesses: list[float] = []
    for path, footage_map in footage_maps.items():
        for idx, sample in enumerate(footage_map.samples):
            paths.append(path)
            local_indices.append(idx)
            motions.append(sample.motion)
            sharpnesses.append(sample.sharpness)

    motion_pct_flat = percentile_rank(np.array(motions))
    sharpness_pct_flat = percentile_rank(np.array(sharpnesses))

    motion_pct: dict[str, dict[int, float]] = defaultdict(dict)
    sharpness_pct: dict[str, dict[int, float]] = defaultdict(dict)
    for path, idx, mp, sp in zip(paths, local_indices, motion_pct_flat, sharpness_pct_flat):
        motion_pct[path][idx] = float(mp)
        sharpness_pct[path][idx] = float(sp)

    return _RankedFootage(motion_pct=dict(motion_pct), sharpness_pct=dict(sharpness_pct))


# ---------------------------------------------------------------------------
# the greedy cutter
# ---------------------------------------------------------------------------


def cut(
    music_map: MusicMap,
    music_path: str,
    footage_maps: dict[str, FootageMap],
    mood: Mood,
    duration_s: float,
    *,
    start_beat_index: int = 0,
) -> Timeline:
    """Build a Timeline by greedy, deterministic scoring: for each slot,
    in timeline order, score every valid candidate and pick the argmax.
    Always validates the result before returning (docs/MVP.md: "After
    generation, always run the Timeline validator").
    """
    params: CutterParams = style_for_mood(mood)
    grid = build_grid(music_map, params, duration_s, start_beat_index=start_beat_index)
    ranked = _rank_footage_set(footage_maps)

    used_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    uses_so_far: dict[str, int] = defaultdict(int)
    recent_files: list[str] = []
    clips: list[Clip] = []

    for slot in grid.slots:
        slot_length_ms = frame_to_ms(slot.frames, fps=TIMELINE_FPS)
        target_motion = params.motion_base + params.motion_scale * slot.energy
        candidates_by_file = generate_candidates(footage_maps, slot.frames)

        best: tuple[ScoreBreakdown, str, int] | None = None
        for path, in_points in candidates_by_file.items():
            reuse = _reuse_term(path, recent_files)
            coverage = _coverage_term(path, uses_so_far)
            for in_ms in in_points:
                out_ms = in_ms + slot_length_ms
                if _overlaps_any(in_ms, out_ms, used_ranges[path]):
                    continue
                breakdown = score_candidate(
                    footage_maps[path], in_ms, slot_length_ms,
                    ranked.motion_pct[path], ranked.sharpness_pct[path],
                    target_motion, reuse, coverage,
                )
                if breakdown is None:
                    continue
                if best is None or breakdown.score > best[0].score:
                    best = (breakdown, path, in_ms)

        if best is None:
            raise CutterError(
                f"no valid candidate for slot {slot.index} "
                f"(start_frame={slot.start_frame}, {slot.frames} frames); "
                "footage set may be too small, too short, or too uniform"
            )

        breakdown, path, in_ms = best
        clips.append(
            Clip(
                id=f"c{slot.index:03d}",
                start_frame=slot.start_frame,
                end_frame=slot.end_frame,
                source=Source(path=path, in_ms=in_ms),
                score=breakdown.score,
                rationale={
                    "quality": breakdown.quality,
                    "motion_match": breakdown.motion_match,
                    "reuse": breakdown.reuse,
                    "coverage": breakdown.coverage,
                },
            )
        )
        used_ranges[path].append((in_ms, in_ms + slot_length_ms))
        uses_so_far[path] += 1
        recent_files.append(path)

    timeline = Timeline(
        duration_frames=grid.duration_frames,
        music=Music(path=music_path, in_ms=grid.music_in_ms),
        beats=grid.timeline_beats,
        clips=clips,
        provenance=Provenance(generator="cutter-v0", params={"mood": mood, "duration_s": duration_s}),
    )

    violations = validate(
        timeline,
        source_durations_ms={path: fm.duration_ms for path, fm in footage_maps.items()},
        music_duration_ms=int(round(music_map.duration_s * 1000)),
    )
    if violations:
        raise CutterError(
            "cutter produced an invalid timeline (this should not happen by construction): "
            + "; ".join(str(v) for v in violations)
        )
    return timeline
