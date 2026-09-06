"""poc.evaluate: Step 9, an isolated measurement layer around the frozen
Steps 1-8. Nothing here modifies the cutter, renderer, perception, or
pipeline; this package only generates paired evaluation data, blinds it,
ingests ratings, and computes docs/MVP.md Section 10's metrics exactly.
"""

from poc.evaluate.blind import AnswerKey, BlindAssignment, assign_blind_labels
from poc.evaluate.generate import (
    EvaluationGenerationError,
    FootageSetSpec,
    MoodPairManifest,
    MusicTrackSpec,
    build_blind_dataset,
    generate_dataset,
    generate_mood_identification_pair,
    generate_pair,
)
from poc.evaluate.manifest import PairManifest, get_git_commit
from poc.evaluate.metrics import (
    M1Result,
    M2Result,
    M3Result,
    M4Result,
    M5Result,
    compute_m1,
    compute_m2,
    compute_m3,
    compute_m4,
    compute_m5,
    decide,
    wilson_interval,
)
from poc.evaluate.ratings import (
    MoodIdentificationRating,
    OutputRating,
    PairwiseRating,
    RatingValidationResult,
    append_ratings_jsonl,
    load_pairwise_ratings_csv,
    load_ratings_jsonl,
    validate_pairwise_ratings,
)
from poc.evaluate.report import EvaluationReport, RaterBreakdown, TrackMoodBreakdown, build_m2_report, format_report

__all__ = [
    "AnswerKey",
    "BlindAssignment",
    "EvaluationGenerationError",
    "EvaluationReport",
    "FootageSetSpec",
    "M1Result",
    "M2Result",
    "M3Result",
    "M4Result",
    "M5Result",
    "MoodIdentificationRating",
    "MoodPairManifest",
    "MusicTrackSpec",
    "OutputRating",
    "PairManifest",
    "PairwiseRating",
    "RaterBreakdown",
    "RatingValidationResult",
    "TrackMoodBreakdown",
    "append_ratings_jsonl",
    "assign_blind_labels",
    "build_blind_dataset",
    "build_m2_report",
    "compute_m1",
    "compute_m2",
    "compute_m3",
    "compute_m4",
    "compute_m5",
    "decide",
    "format_report",
    "generate_dataset",
    "generate_mood_identification_pair",
    "generate_pair",
    "get_git_commit",
    "load_pairwise_ratings_csv",
    "load_ratings_jsonl",
    "validate_pairwise_ratings",
    "wilson_interval",
]
