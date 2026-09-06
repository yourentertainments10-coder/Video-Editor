"""poc/evaluate/report.py: assemble the Step 9 analysis report.

Combines whatever metrics have actually been measured (M2 is the only
one this module requires -- M1/M3/M4/M5 are optional and simply omitted
from the verdict when not supplied, never assumed) into the breakdown
docs/MVP.md's evaluation asks for: total comparisons, wins, preference
rate, confidence interval, a per-rater breakdown, and a per-(footage set,
music, mood) breakdown, plus the overall pass/fail verdict.

Ties/skips: MVP.md's M2 question ("which is the better edit?") is a
plain binary choice with no tie option, and poc.evaluate.ratings.
PairwiseRating.chosen_video is typed Literal["A", "B"] to match -- so
there is nothing to count here. This is reported explicitly as
"not supported by the rating schema", not silently omitted, satisfying
requirement 7's "ties/skips if supported" without inventing a tie
mechanism MVP.md never specified.
"""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict

from poc.evaluate.blind import AnswerKey
from poc.evaluate.manifest import PairManifest
from poc.evaluate.metrics import (
    M1Result,
    M2Result,
    M3Result,
    M4Result,
    M5Result,
    M2_PREFERENCE_THRESHOLD,
    compute_m2,
    decide,
)
from poc.evaluate.ratings import PairwiseRating

__all__ = ["RaterBreakdown", "TrackMoodBreakdown", "EvaluationReport", "build_m2_report", "format_report"]


class RaterBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rater_id: str
    total: int
    cutter_wins: int

    @property
    def preference_rate(self) -> float:
        return self.cutter_wins / self.total if self.total else 0.0


class TrackMoodBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    footage_set_id: str
    music_id: str
    mood: str
    total: int
    cutter_wins: int

    @property
    def preference_rate(self) -> float:
        return self.cutter_wins / self.total if self.total else 0.0


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    m2: M2Result
    m1: M1Result | None = None
    m3: M3Result | None = None
    m4: M4Result | None = None
    m5: M5Result | None = None
    per_rater: list[RaterBreakdown]
    per_track_mood: list[TrackMoodBreakdown]
    ties_supported: bool = False
    verdict: str


def build_m2_report(
    ratings: list[PairwiseRating],
    answer_key: AnswerKey,
    manifests: list[PairManifest],
    *,
    m1: M1Result | None = None,
    m3: M3Result | None = None,
    m4: M4Result | None = None,
    m5: M5Result | None = None,
) -> EvaluationReport:
    m2 = compute_m2(ratings, answer_key)
    manifest_by_pair = {m.pair_id: m for m in manifests}

    def _is_cutter_win(r: PairwiseRating) -> bool:
        return answer_key.assignments[r.pair_id].source_of[r.chosen_video] == "cutter"

    valid_ratings = [r for r in ratings if r.pair_id in answer_key.assignments]

    by_rater: dict[str, list[PairwiseRating]] = defaultdict(list)
    for r in valid_ratings:
        by_rater[r.rater_id].append(r)
    per_rater = [
        RaterBreakdown(
            rater_id=rid, total=len(rs), cutter_wins=sum(1 for r in rs if _is_cutter_win(r))
        )
        for rid, rs in sorted(by_rater.items())
    ]

    by_key: dict[tuple[str, str, str], list[PairwiseRating]] = defaultdict(list)
    for r in valid_ratings:
        m = manifest_by_pair.get(r.pair_id)
        if m is not None:
            by_key[(m.footage_set_id, m.music_id, m.mood)].append(r)
    per_track_mood = [
        TrackMoodBreakdown(
            footage_set_id=fsid, music_id=mid, mood=mood,
            total=len(rs), cutter_wins=sum(1 for r in rs if _is_cutter_win(r)),
        )
        for (fsid, mid, mood), rs in sorted(by_key.items())
    ]

    if m1 is not None:
        verdict = decide(m1, m2, m3, m4, m5)
    else:
        verdict = (
            f"M2 preference rate {m2.preference_rate:.1%} over {m2.total_judgments} judgments "
            f"({'PASS' if m2.passed else 'FAIL'} against the {M2_PREFERENCE_THRESHOLD:.0%} threshold). "
            "M1 not supplied -- docs/MVP.md's full decision rule needs it; this is the M2-only verdict."
        )

    return EvaluationReport(
        m2=m2, m1=m1, m3=m3, m4=m4, m5=m5,
        per_rater=per_rater, per_track_mood=per_track_mood,
        ties_supported=False, verdict=verdict,
    )


def format_report(report: EvaluationReport) -> str:
    lines: list[str] = []
    m2 = report.m2
    lines.append("=== M2: pairwise preference (the core hypothesis) ===")
    lines.append(f"total comparisons : {m2.total_judgments}")
    lines.append(f"cutter wins       : {m2.cutter_wins}")
    lines.append(f"baseline wins     : {m2.baseline_wins}")
    lines.append("ties/skips        : not supported by the rating schema (MVP.md's M2 question is binary)")
    lines.append(f"preference rate   : {m2.preference_rate:.1%}")
    if m2.confidence_interval_95:
        lo, hi = m2.confidence_interval_95
        lines.append(f"95% CI (Wilson)   : [{lo:.1%}, {hi:.1%}]")
    lines.append(f"threshold         : {M2_PREFERENCE_THRESHOLD:.0%}")
    lines.append(f"M2 result         : {'PASS' if m2.passed else 'FAIL'}")
    lines.append("")

    if report.per_rater:
        lines.append("--- per rater ---")
        for rb in report.per_rater:
            lines.append(f"  {rb.rater_id:<12} {rb.cutter_wins}/{rb.total}  ({rb.preference_rate:.1%})")
        lines.append("")

    if report.per_track_mood:
        lines.append("--- per footage set / music / mood ---")
        for tb in report.per_track_mood:
            lines.append(
                f"  {tb.footage_set_id:<10} {tb.music_id:<10} {tb.mood:<10} "
                f"{tb.cutter_wins}/{tb.total}  ({tb.preference_rate:.1%})"
            )
        lines.append("")

    for label, result in (("M1 (mechanical)", report.m1), ("M3 (mood id)", report.m3),
                           ("M4 (usability)", report.m4), ("M5 (sync)", report.m5)):
        if result is not None:
            lines.append(f"{label}: {'PASS' if result.passed else 'FAIL'}")

    lines.append("")
    lines.append("=== Verdict ===")
    lines.append(report.verdict)
    return "\n".join(lines)
