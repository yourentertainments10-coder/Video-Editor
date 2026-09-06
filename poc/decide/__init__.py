"""poc.decide: pure functions over analysis JSON -- style, grid, cutter, baseline.

No media I/O anywhere in this package: every function here reads
MusicMap/FootageMap data (Step 5/6) and produces a Timeline (Step 2),
never touching FFmpeg or decoding video. See poc/decide/cutter.py for the
core MVP experiment this package exists to run.
"""

from poc.decide.baseline import cut_baseline
from poc.decide.cutter import CutterError, ScoreBreakdown, cut, generate_candidates, score_candidate
from poc.decide.grid import GridError, GridResult, Slot, build_grid, percentile_rank
from poc.decide.style import CutterParams, Mood, style_for_mood

__all__ = [
    "CutterError",
    "CutterParams",
    "GridError",
    "GridResult",
    "Mood",
    "ScoreBreakdown",
    "Slot",
    "build_grid",
    "cut",
    "cut_baseline",
    "generate_candidates",
    "percentile_rank",
    "score_candidate",
    "style_for_mood",
]
