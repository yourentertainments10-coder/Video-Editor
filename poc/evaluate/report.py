"""poc/evaluate/report.py: assemble the Step 9 analysis report.

Combines measured M1-M5 values with expected dataset/rater identities.
Partial data is shown with missing counts and cannot issue an official
verdict; development and preliminary datasets remain diagnostic-only.
The report includes rating breakdowns, metric thresholds, source-version
provenance, and the frozen decision rule when eligible.

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

from poc.evaluate.blind import AnswerKey, MoodAnswerKey
from poc.evaluate.manifest import PairManifest
from poc.evaluate.metrics import (
    M1Result,
    M2Result,
    M3Result,
    M4Result,
    M5Result,
    M2_PREFERENCE_THRESHOLD,
    M3_MOOD_ID_THRESHOLD,
    M4_MIN_OUTPUT_FRACTION,
    M5_MEAN_THRESHOLD,
    compute_m2,
    compute_m3,
    compute_m4,
    compute_m5,
    decide,
)
from poc.evaluate.ratings import (
    MoodIdentificationRating,
    OutputRating,
    PairwiseRating,
    validate_mood_identification_ratings,
    validate_output_ratings,
    validate_pairwise_ratings,
)

__all__ = ["RaterBreakdown", "TrackMoodBreakdown", "EvaluationReport", "build_m2_report", "format_report"]

EXPECTED_RATER_COUNT = 5


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
    expected_rater_ids: list[str] | None = None
    expected_response_counts: dict[str, int | None]
    missing_by_metric: dict[str, list[tuple[str, str]]]
    duplicate_counts: dict[str, int]
    unknown_subject_counts: dict[str, int]
    collection_complete: bool
    evaluation_phase: str
    official_verdict_eligible: bool
    version_records: list[str]
    version_consistent: bool
    verdict: str


def build_m2_report(
    ratings: list[PairwiseRating],
    answer_key: AnswerKey,
    manifests: list[PairManifest],
    *,
    m1: M1Result | None = None,
    mood_answer_key: MoodAnswerKey | None = None,
    m3_ratings: list[MoodIdentificationRating] | None = None,
    m4_ratings: list[OutputRating] | None = None,
    m5_ratings: list[OutputRating] | None = None,
    expected_rater_ids: set[str] | None = None,
) -> EvaluationReport:
    pair_ids = set(answer_key.assignments)
    manifest_by_pair = {m.pair_id: m for m in manifests}
    phases = {manifest.evaluation_phase for manifest in manifests}
    evaluation_phase = next(iter(phases)) if len(phases) == 1 else "mixed" if phases else "unknown"
    version_profiles = {
        (
            manifest.git_commit,
            manifest.source_code_fingerprint,
            manifest.software_version,
            manifest.analysis_version,
            manifest.cutter_version,
            manifest.baseline_version,
            manifest.git_worktree_dirty,
        )
        for manifest in manifests
    }
    version_consistent = len(version_profiles) == 1 and bool(manifests)
    version_profile = next(iter(version_profiles), None)
    version_metadata_complete = (
        version_profile is not None
        and version_profile[0] is not None
        and version_profile[1] is not None
        and version_profile[6] is False
    )
    version_records = sorted({
        f"phase={manifest.evaluation_phase}; cutter={manifest.cutter_version}; "
        f"baseline={manifest.baseline_version}; package={manifest.software_version}; "
        f"analysis={manifest.analysis_version}; commit={manifest.git_commit or 'unknown'}; "
        f"source={manifest.source_code_fingerprint or 'unknown'}; "
        f"tracked_dirty={manifest.git_worktree_dirty}"
        for manifest in manifests
    })
    m2_validation = validate_pairwise_ratings(ratings, pair_ids, expected_rater_ids)
    m2 = compute_m2(m2_validation.valid_ratings, answer_key)

    def _is_cutter_win(r: PairwiseRating) -> bool:
        return answer_key.assignments[r.pair_id].source_of[r.chosen_video] == "cutter"

    valid_ratings = m2_validation.valid_ratings

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

    output_ids = {f"{m.pair_id}_{side}" for m in manifests for side in ("A", "B")}
    m4_validation = validate_output_ratings(
        m4_ratings or [], output_ids, expected_rater_ids, metric="M4"
    )
    m5_validation = validate_output_ratings(
        m5_ratings or [], output_ids, expected_rater_ids, metric="M5"
    )
    m4 = compute_m4(m4_validation.valid_ratings, output_ids) if manifests else None
    m5 = compute_m5(m5_validation.valid_ratings) if manifests else None

    m3_validation = None
    if mood_answer_key is not None:
        mood_pair_ids = set(mood_answer_key.assignments)
        m3_validation = validate_mood_identification_ratings(
            m3_ratings or [], mood_pair_ids, expected_rater_ids
        )
        m3 = compute_m3(m3_validation.valid_ratings, mood_answer_key.energetic_side)
    else:
        m3 = None

    missing_by_metric: dict[str, list[tuple[str, str]]] = {
        "M2": m2_validation.missing,
        "M4": m4_validation.missing,
        "M5": m5_validation.missing,
    }
    if m3_validation is not None:
        missing_by_metric["M3"] = m3_validation.missing
    rater_count = len(expected_rater_ids) if expected_rater_ids is not None else None
    expected_response_counts = {
        "M2": len(pair_ids) * rater_count if rater_count is not None else None,
        "M3": len(mood_answer_key.assignments) * rater_count
        if mood_answer_key is not None and rater_count is not None else None,
        "M4": len(output_ids) * rater_count if rater_count is not None else None,
        "M5": len(output_ids) * rater_count if rater_count is not None else None,
    }
    duplicate_counts = {
        "M2": len(m2_validation.duplicate_keys),
        "M3": len(m3_validation.duplicate_keys) if m3_validation is not None else 0,
        "M4": len(m4_validation.duplicate_keys),
        "M5": len(m5_validation.duplicate_keys),
    }
    unknown_subject_counts = {
        "M2": len(m2_validation.unknown_pair_errors),
        "M3": len(m3_validation.unknown_subject_errors) if m3_validation is not None else 0,
        "M4": len(m4_validation.unknown_subject_errors),
        "M5": len(m5_validation.unknown_subject_errors),
    }
    collection_complete = (
        expected_rater_ids is not None
        and len(expected_rater_ids) == EXPECTED_RATER_COUNT
        and bool(pair_ids)
        and bool(output_ids)
        and mood_answer_key is not None
        and bool(mood_answer_key.assignments)
        and m1 is not None
        and m3 is not None
        and m2_validation.is_complete
        and m4_validation.is_complete
        and m5_validation.is_complete
        and m3_validation is not None
        and m3_validation.is_complete
    )
    official_verdict_eligible = (
        collection_complete
        and evaluation_phase == "official"
        and version_consistent
        and version_metadata_complete
    )
    if official_verdict_eligible:
        verdict = decide(m1, m2, m3, m4, m5)
    elif collection_complete:
        if evaluation_phase == "official":
            verdict = "INCOMPLETE: official source-version metadata is missing, dirty, or inconsistent."
        else:
            verdict = (
                f"DIAGNOSTIC ONLY: all provided metrics are measured for phase={evaluation_phase}; "
                "no official MVP verdict issued."
            )
    else:
        missing_metrics = [name for name, result in (("M1", m1), ("M3", m3), ("M4", m4), ("M5", m5)) if result is None]
        missing_counts = [f"{name}={len(items)}" for name, items in missing_by_metric.items()]
        if expected_rater_ids is None:
            missing_metrics.append("declared rater set")
        elif len(expected_rater_ids) != EXPECTED_RATER_COUNT:
            missing_metrics.append(f"exactly {EXPECTED_RATER_COUNT} declared raters")
        if missing_metrics:
            missing_counts.extend(f"{name}=not generated" for name in missing_metrics)
        verdict = "INCOMPLETE: no final verdict issued; missing responses " + ", ".join(missing_counts)

    return EvaluationReport(
        m2=m2, m1=m1, m3=m3, m4=m4, m5=m5,
        per_rater=per_rater, per_track_mood=per_track_mood,
        ties_supported=False,
        expected_rater_ids=sorted(expected_rater_ids) if expected_rater_ids is not None else None,
        expected_response_counts=expected_response_counts,
        missing_by_metric=missing_by_metric,
        duplicate_counts=duplicate_counts,
        unknown_subject_counts=unknown_subject_counts,
        collection_complete=collection_complete,
        evaluation_phase=evaluation_phase,
        official_verdict_eligible=official_verdict_eligible,
        version_records=version_records,
        version_consistent=version_consistent,
        verdict=verdict,
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
    expected_m2 = report.expected_response_counts["M2"]
    lines.append(
        f"M2 valid responses: {m2.total_judgments}/{expected_m2 if expected_m2 is not None else 'unchecked'}"
    )
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

    if report.m1 is not None:
        lines.append(
            f"M1 (mechanical): {report.m1.passing_outputs}/{report.m1.total_outputs} outputs pass"
        )
        lines.extend(f"  M1 failure: {failure}" for failure in report.m1.failures)
    if report.m3 is not None:
        expected_m3 = report.expected_response_counts["M3"]
        lines.append(
            f"M3 (mood id): {report.m3.correct}/{report.m3.total_judgments} correct "
            f"({report.m3.accuracy:.1%}; {report.m3.total_judgments}/"
            f"{expected_m3 if expected_m3 is not None else 'unchecked'} responses; "
            f"threshold {M3_MOOD_ID_THRESHOLD:.0%}; {'PASS' if report.m3.passed else 'FAIL'})"
        )
    if report.m4 is not None:
        lines.append(
            f"M4 (usability): {report.m4.passing_outputs}/{report.m4.total_outputs} outputs pass "
            f"({report.m4.passing_fraction:.1%}; {report.m4.rated_outputs} outputs rated; "
            f"{report.m4.total_responses}/{report.expected_response_counts['M4'] if report.expected_response_counts['M4'] is not None else 'unchecked'} "
            f"responses; threshold {M4_MIN_OUTPUT_FRACTION:.0%}; {'PASS' if report.m4.passed else 'FAIL'})"
        )
    if report.m5 is not None:
        lines.append(
            f"M5 (sync): mean {report.m5.mean_rating:.2f}/5 from {report.m5.total_judgments} ratings "
            f"({report.m5.total_judgments}/"
            f"{report.expected_response_counts['M5'] if report.expected_response_counts['M5'] is not None else 'unchecked'} "
            f"responses; threshold {M5_MEAN_THRESHOLD:.1f}; {'PASS' if report.m5.passed else 'FAIL'})"
        )
    lines.append(f"collection complete: {'yes' if report.collection_complete else 'no'}")
    lines.append(f"evaluation phase: {report.evaluation_phase}")
    lines.append(f"official verdict eligible: {'yes' if report.official_verdict_eligible else 'no'}")
    lines.append(f"source versions consistent: {'yes' if report.version_consistent else 'no'}")
    for record in report.version_records:
        lines.append(f"source version: {record}")
    lines.append(
        f"declared raters: {len(report.expected_rater_ids) if report.expected_rater_ids is not None else 0}/"
        f"{EXPECTED_RATER_COUNT}"
    )
    if report.expected_rater_ids is None:
        lines.append("missing responses: unchecked (declare the expected raters)")
    else:
        for metric, missing in sorted(report.missing_by_metric.items()):
            lines.append(f"missing {metric} responses: {len(missing)}")
    for metric in sorted(report.duplicate_counts):
        lines.append(
            f"duplicate {metric} ratings ignored: {report.duplicate_counts[metric]}; "
            f"unknown subjects: {report.unknown_subject_counts[metric]}"
        )

    lines.append("")
    lines.append("=== Verdict ===")
    lines.append(report.verdict)
    return "\n".join(lines)
