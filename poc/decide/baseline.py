"""poc/decide/baseline.py: the random control.

This is mandatory, not optional (docs/MVP.md Section 2, item 7): "Without
it a good-looking output proves nothing." It exists to let Step 9 test the
actual hypothesis --

  "Does intelligent selection beat random selection?"

-- and not the much weaker, uninteresting question "does a beat-synced
video beat an unsynchronized one?" To make that comparison fair, this
module shares everything with poc.decide.cutter EXCEPT the scoring
function itself:

  - the SAME grid (poc.decide.grid.build_grid, same mood, same duration,
    so slot boundaries and count are identical)
  - the SAME candidate generation (poc.decide.cutter.generate_candidates,
    same 250 ms step / 200 ms margin rule)
  - the SAME hard validity constraint: a candidate overlapping a source
    range already used by this baseline run is excluded, exactly like the
    cutter (this is not a "fairness nicety" -- it is required for the
    output to pass poc.timeline.validate's NO_SOURCE_REUSE check at all)
  - the SAME post-generation validation call

The only thing removed is quality/motion_match/reuse/coverage scoring:
among whatever candidates remain valid for a slot, one is chosen with a
seeded, uniform random draw instead of an argmax. No mood-driven
preference, no quality signal, no editorial judgment -- selection quality
under test is exactly the cutter's scoring function, isolated from every
other factor the two outputs would otherwise share.

``mood`` is still a required parameter here, purely to reproduce the
identical slot/grid structure the cutter would build for the same
request -- it has no effect on which candidate is picked (motion_base/
motion_scale are never read in this module).
"""
from __future__ import annotations

import random
from collections import defaultdict

from poc.decide.cutter import CutterError, _overlaps_any, generate_candidates
from poc.decide.grid import build_grid
from poc.decide.style import Mood, style_for_mood
from poc.perceive.footage import FootageMap
from poc.perceive.music import MusicMap
from poc.timeline.schema import TIMELINE_FPS, Clip, Music, Provenance, Source, Timeline, frame_to_ms
from poc.timeline.validate import validate

__all__ = ["cut_baseline"]


def cut_baseline(
    music_map: MusicMap,
    music_path: str,
    footage_maps: dict[str, FootageMap],
    mood: Mood,
    duration_s: float,
    *,
    seed: int,
    start_beat_index: int = 0,
) -> Timeline:
    """Build a Timeline on the same grid/candidates as poc.decide.cutter.cut,
    but pick each slot's clip uniformly at random (seeded) instead of by
    score. Always validates the result before returning, exactly like the
    cutter.
    """
    params = style_for_mood(mood)  # only to reproduce the identical grid
    grid = build_grid(music_map, params, duration_s, start_beat_index=start_beat_index)
    rng = random.Random(seed)

    used_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    clips: list[Clip] = []

    for slot in grid.slots:
        slot_length_ms = frame_to_ms(slot.frames, fps=TIMELINE_FPS)
        candidates_by_file = generate_candidates(footage_maps, slot.frames)

        valid: list[tuple[str, int]] = []
        for path, in_points in candidates_by_file.items():
            for in_ms in in_points:
                out_ms = in_ms + slot_length_ms
                if not _overlaps_any(in_ms, out_ms, used_ranges[path]):
                    valid.append((path, in_ms))

        if not valid:
            raise CutterError(
                f"no valid candidate for slot {slot.index} "
                f"(start_frame={slot.start_frame}, {slot.frames} frames) in baseline"
            )

        path, in_ms = rng.choice(valid)
        clips.append(
            Clip(
                id=f"b{slot.index:03d}",
                start_frame=slot.start_frame,
                end_frame=slot.end_frame,
                source=Source(path=path, in_ms=in_ms),
            )
        )
        used_ranges[path].append((in_ms, in_ms + slot_length_ms))

    timeline = Timeline(
        duration_frames=grid.duration_frames,
        music=Music(path=music_path, in_ms=grid.music_in_ms),
        beats=grid.timeline_beats,
        clips=clips,
        provenance=Provenance(
            generator="baseline-v0",
            params={"mood": mood, "duration_s": duration_s, "seed": seed},
            seed=seed,
        ),
    )

    violations = validate(
        timeline,
        source_durations_ms={path: fm.duration_ms for path, fm in footage_maps.items()},
        music_duration_ms=int(round(music_map.duration_s * 1000)),
    )
    if violations:
        raise CutterError(
            "baseline produced an invalid timeline (this should not happen by construction): "
            + "; ".join(str(v) for v in violations)
        )
    return timeline
