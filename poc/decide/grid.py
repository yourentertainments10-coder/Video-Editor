"""poc/decide/grid.py: MusicMap beats -> candidate output slots.

Implements docs/MVP.md's slot generation exactly:

  "Slot generation walks the beats from the window start, advancing by
  'beats per cut' evaluated at the current beat, and stops at the last
  beat at or before the requested duration. The timeline duration
  therefore snaps to a beat, which is what a human editor would do too."

Beats are timing candidates, not mandatory cuts (experiments/beat_flash/
CONCLUSION.md decision 3 / "a beat grid is not automatically a cut
grid"): a slot boundary is placed only every ``beats_per_cut`` beats, and
that count itself already varies with energy (docs/MVP.md's style table),
so this module does not cut on every beat and cut density already
responds to how energetic the music is at each point. No downbeat/bar
detection is used or needed.

Two disclosed bridging decisions were required to connect Step 5's
MusicMap (built to experiments/beat_flash/CONCLUSION.md's validated
shape) to docs/MVP.md's style table and Appendix, which were written
against a slightly different, only-partially-implemented music analysis
design. Both are the simplest deterministic choice available, not a
model, per this step's explicit fallback instruction.

1. **No music window selection (no downbeat-aligned energy scan).**
   docs/MVP.md's style table has a third row: "music window: highest (or
   lowest) energy window starting on a downbeat". That requires downbeat
   detection, which experiments/beat_flash/CONCLUSION.md decision 8
   explicitly excludes from MVP v0 ("The 4/4 bar heuristic does not
   ship... no downbeat/bar detection here at all"), and which this step's
   task list does not ask for (only Grid/Style/Candidates/Cutter/Baseline
   were requested). This module therefore does not pick a sub-window of a
   long track by energy. Instead, the "window start" is simply the first
   beat MusicMap detected (or an explicit ``start_beat_index`` override),
   and the analysis proceeds from there. Choosing which portion of a long
   track to excerpt is left to a caller (Step 8) or a future step; nothing
   here prevents it being added later, since ``start_beat_index`` is
   already the hook for it.

2. **Per-beat energy is normalized here, not by the analyzer.** docs/
   MVP.md Section 7 originally assumed the music analyzer itself would
   output "energy_per_beat[] ... normalized to 0..1 by percentile across
   the track". Step 5's MusicMap does not do this -- it stores raw
   ``local_energy_db`` (dB) plus boolean low_energy/silent flags, because
   experiments/beat_flash/CONCLUSION.md decision 6 places energy gating
   at the grid layer, not the tracker ("Energy gating belongs to the
   grid, not the tracker"). This module is therefore where that
   normalization now happens: exactly the same percentile-rank technique
   docs/MVP.md Section 6 already prescribes for footage motion/sharpness
   ("converted to percentile ranks across the whole ... set"), applied
   here to each beat's local_energy_db across the beats being used. The
   resulting 0..1 value is what the style table's "e" thresholds compare
   against.

Low-energy/silent diagnostics are threaded onto every Slot precisely so
later code (or a human reviewing rationale) can see where a slot sits
relative to a quiet passage -- available, per this step's instruction, not
used to invent an undocumented skip/avoidance rule beyond what the style
table's beats-per-cut mechanism already does. See poc/decide/cutter.py for
where percentile_rank() is reused for footage signals.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

import numpy as np

from poc.decide.style import CutterParams
from poc.perceive.music import MusicMap
from poc.timeline.schema import TIMELINE_FPS, Beat, ms_to_frame

__all__ = ["GridError", "Slot", "GridResult", "percentile_rank", "build_grid"]


class GridError(RuntimeError):
    """Raised when a valid grid cannot be built from the given inputs
    (too few beats, or beats_per_cut too large for the beats available)."""


def percentile_rank(values: np.ndarray) -> np.ndarray:
    """Rank each value by its position among all values, scaled to 0..1.

    Deterministic, no randomness: rank 0 is the smallest value, rank 1 is
    the largest. Ties receive the *average* of the rank positions they
    span (the standard "average rank" convention: N identical values,
    however many there are, all land on the same 0..1 rank -- notably,
    an entirely uniform input ranks every value at exactly 0.5, not
    spread out by array position). A single-element input ranks at the
    midpoint (0.5); an empty input returns an empty array.
    """
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return np.array([])
    if n == 1:
        return np.array([0.5])
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    group_end = np.cumsum(counts)
    group_start = group_end - counts
    avg_rank_per_group = (group_start + group_end - 1) / 2.0
    ranks = avg_rank_per_group[inverse]
    return ranks / (n - 1)


class Slot(BaseModel):
    """One candidate output slot: a timeline frame range with the energy
    context that produced it. Slots are internal to the Decide layer --
    never serialized into a Timeline Document; only the Clips the cutter
    or baseline picks to fill them are."""

    model_config = ConfigDict(extra="forbid")

    index: int
    start_frame: int
    end_frame: int  # exclusive
    energy: float = Field(ge=0.0, le=1.0)  # mean normalized energy over the slot's beats
    low_energy: bool  # true if the slot's starting beat was flagged low-energy
    silent: bool  # true if the slot's starting beat was flagged silent

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


class GridResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    music_in_ms: int  # -> Timeline.music.in_ms
    duration_frames: int  # snapped to the last usable beat, per docs/MVP.md
    timeline_beats: list[Beat]  # ready to drop into Timeline.beats
    slots: list[Slot]


def build_grid(
    music_map: MusicMap,
    params: CutterParams,
    duration_s: float,
    *,
    start_beat_index: int = 0,
) -> GridResult:
    """Walk MusicMap's beats from ``start_beat_index``, advancing by
    ``params.beats_per_cut(energy)`` each step, stopping at the last beat
    at or before ``duration_s``. Raises GridError if not even one slot
    can be built (too few beats past start_beat_index, or the very first
    beats_per_cut step already exceeds duration_s).
    """
    beats_s = music_map.beats_used_s[start_beat_index:]
    per_beat_energy = music_map.energy.per_beat[start_beat_index:]

    if len(beats_s) < 2 or len(per_beat_energy) < 2:
        raise GridError(
            f"not enough beats after start_beat_index={start_beat_index} "
            f"({len(beats_s)} available) to build even one slot"
        )

    beat_db = np.array([b.local_energy_db for b in per_beat_energy])
    energy_norm = percentile_rank(beat_db)

    window_start_s = beats_s[0]
    target_end_s = window_start_s + duration_s

    boundary_indices = [0]
    i = 0
    while True:
        n = params.beats_per_cut(float(energy_norm[i]))
        next_i = i + n
        if next_i >= len(beats_s):
            break
        if beats_s[next_i] > target_end_s:
            break
        boundary_indices.append(next_i)
        i = next_i

    if len(boundary_indices) < 2:
        raise GridError(
            f"could not build any slot within duration_s={duration_s}: "
            "not enough beats, or beats_per_cut too large for the available grid"
        )

    slots: list[Slot] = []
    for j in range(len(boundary_indices) - 1):
        bi_start, bi_end = boundary_indices[j], boundary_indices[j + 1]
        start_frame = ms_to_frame((beats_s[bi_start] - window_start_s) * 1000.0, fps=TIMELINE_FPS)
        end_frame = ms_to_frame((beats_s[bi_end] - window_start_s) * 1000.0, fps=TIMELINE_FPS)
        slots.append(
            Slot(
                index=j,
                start_frame=start_frame,
                end_frame=end_frame,
                energy=float(np.mean(energy_norm[bi_start:bi_end])),
                low_energy=bool(per_beat_energy[bi_start].low_energy),
                silent=bool(per_beat_energy[bi_start].silent),
            )
        )

    final_bi = boundary_indices[-1]
    seen_frames: set[int] = set()
    timeline_beats: list[Beat] = []
    for k in range(0, final_bi + 1):
        frame = ms_to_frame((beats_s[k] - window_start_s) * 1000.0, fps=TIMELINE_FPS)
        if frame in seen_frames:
            continue
        seen_frames.add(frame)
        timeline_beats.append(Beat(frame=frame, downbeat=False, energy=float(energy_norm[k])))

    return GridResult(
        music_in_ms=int(round(window_start_s * 1000.0)),
        duration_frames=slots[-1].end_frame,
        timeline_beats=timeline_beats,
        slots=slots,
    )
