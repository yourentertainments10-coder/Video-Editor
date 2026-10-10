"""poc/evaluate/ratings.py: rating data models and ingestion.

Four rating instruments, one per MVP.md Section 10 human-judged metric
(M1 is mechanical -- see poc.evaluate.metrics.compute_m1 -- and needs no
human rating at all):

  M2 (the core hypothesis): PairwiseRating -- "Which is the better edit?"
  M3: MoodIdentificationRating -- "Which one was asked to be energetic?"
  M4: "Would you post this after small manual tweaks?" (yes/no)
  M5: "Do the cuts feel in time with the music?" (1-5)

M4 and M5 are both asked per single output (not a pairwise comparison),
so they share one model, OutputRating, with two independent optional
fields -- a rating sheet may ask one question, the other, or both in the
same pass.

Ratings are never fabricated here (there is no code path that invents a
rating) and never silently mutated once ingested: ``append_ratings_jsonl``
only ever appends to a JSONL file, and re-ingesting the same CSV twice
into that log produces two entries -- validation (below) is what surfaces
that as a duplicate, not something this module hides.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "PairwiseRating",
    "OutputRating",
    "MoodIdentificationRating",
    "RatingValidationResult",
    "SubjectRatingValidationResult",
    "load_pairwise_ratings_csv",
    "load_mood_identification_ratings_csv",
    "load_output_ratings_csv",
    "validate_pairwise_ratings",
    "validate_mood_identification_ratings",
    "validate_output_ratings",
    "append_ratings_jsonl",
    "append_mood_identification_ratings_jsonl",
    "append_output_ratings_jsonl",
    "load_ratings_jsonl",
    "load_mood_identification_ratings_jsonl",
    "load_output_ratings_jsonl",
]


class PairwiseRating(BaseModel):
    """M2: one rater's judgment on one blind pair. MVP.md's question is a
    plain binary choice with no preference-strength scale specified, so
    none is added here (per this step's instruction: implement exactly
    what MVP.md specifies, nothing invented)."""

    model_config = ConfigDict(extra="forbid")

    pair_id: str
    rater_id: str
    chosen_video: Literal["A", "B"]
    reason: str | None = None
    timestamp: str


class OutputRating(BaseModel):
    """M4 (``would_post``) and/or M5 (``in_time_rating``, 1-5) for one
    single, non-paired output. ``output_id`` is an opaque id -- never a
    string that reveals cutter/baseline (see poc.evaluate.generate)."""

    model_config = ConfigDict(extra="forbid")

    output_id: str
    rater_id: str
    would_post: bool | None = None
    in_time_rating: int | None = None
    timestamp: str

    @field_validator("in_time_rating")
    @classmethod
    def _rating_in_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 5):
            raise ValueError(f"in_time_rating must be 1..5, got {v}")
        return v


class MoodIdentificationRating(BaseModel):
    """M3: shown the energetic and calm outputs of the same footage+music
    side by side (as "A"/"B", blinded the same way as M2's pairs), guess
    which one is energetic."""

    model_config = ConfigDict(extra="forbid")

    mood_pair_id: str
    rater_id: str
    guessed_energetic: Literal["A", "B"]
    timestamp: str


# ---------------------------------------------------------------------------
# CSV ingestion (the human-facing input format) -> validated models
# ---------------------------------------------------------------------------


def load_pairwise_ratings_csv(path: str | Path) -> list[PairwiseRating]:
    """Parse a rating CSV (columns: pair_id, rater_id, chosen_video,
    reason, timestamp). Raises ValueError naming the exact row on any
    malformed entry rather than skipping it silently."""
    path = Path(path)
    ratings: list[PairwiseRating] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # header is row 1
            try:
                ratings.append(
                    PairwiseRating(
                        pair_id=row["pair_id"].strip(),
                        rater_id=row["rater_id"].strip(),
                        chosen_video=row["chosen_video"].strip().upper(),
                        reason=(row.get("reason") or "").strip() or None,
                        timestamp=row["timestamp"].strip(),
                    )
                )
            except Exception as e:
                raise ValueError(f"row {i} in {path}: {e}") from e
    return ratings


class RatingValidationResult(BaseModel):
    """The outcome of validating a batch of ratings against what the
    dataset actually contains."""

    model_config = ConfigDict(extra="forbid")

    valid_ratings: list[PairwiseRating]
    unknown_pair_errors: list[str] = []
    duplicate_keys: list[tuple[str, str]] = []  # (pair_id, rater_id) seen more than once
    missing: list[tuple[str, str]] = []  # (pair_id, rater_id) expected but absent

    @property
    def is_complete(self) -> bool:
        return not self.missing

    @property
    def is_clean(self) -> bool:
        return not self.unknown_pair_errors and not self.duplicate_keys


class SubjectRatingValidationResult(BaseModel):
    """Validation result for M3/M4/M5 subject-based ratings."""

    model_config = ConfigDict(extra="forbid")

    valid_ratings: list[OutputRating | MoodIdentificationRating]
    unknown_subject_errors: list[str] = []
    duplicate_keys: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []

    @property
    def is_complete(self) -> bool:
        return not self.missing

    @property
    def is_clean(self) -> bool:
        return not self.unknown_subject_errors and not self.duplicate_keys


def validate_pairwise_ratings(
    ratings: list[PairwiseRating],
    expected_pair_ids: set[str],
    expected_rater_ids: set[str] | None = None,
) -> RatingValidationResult:
    """Check a batch of ratings for the failure modes this step must
    detect: a rating for a pair_id that does not exist in the dataset,
    the same (pair_id, rater_id) rated twice, and -- when the expected
    rater set is known -- which (pair_id, rater_id) combinations are
    still missing. The first occurrence of a duplicate key is kept as the
    valid rating; later ones are reported, not silently dropped or merged.
    """
    unknown_pair_errors: list[str] = []
    duplicate_keys: list[tuple[str, str]] = []
    seen: dict[tuple[str, str], PairwiseRating] = {}

    for r in ratings:
        if r.pair_id not in expected_pair_ids:
            unknown_pair_errors.append(f"unknown pair_id {r.pair_id!r} (rater {r.rater_id!r})")
            continue
        key = (r.pair_id, r.rater_id)
        if key in seen:
            duplicate_keys.append(key)
            continue
        seen[key] = r

    missing: list[tuple[str, str]] = []
    if expected_rater_ids is not None:
        for pid in sorted(expected_pair_ids):
            for rid in sorted(expected_rater_ids):
                if (pid, rid) not in seen:
                    missing.append((pid, rid))

    return RatingValidationResult(
        valid_ratings=list(seen.values()),
        unknown_pair_errors=unknown_pair_errors,
        duplicate_keys=duplicate_keys,
        missing=missing,
    )


def load_mood_identification_ratings_csv(path: str | Path) -> list[MoodIdentificationRating]:
    """Load M3 rows with columns mood_pair_id, rater_id,
    guessed_energetic, timestamp."""
    csv_path = Path(path)
    ratings: list[MoodIdentificationRating] = []
    with open(csv_path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row_number, row in enumerate(reader, start=2):
            try:
                ratings.append(MoodIdentificationRating(
                    mood_pair_id=row["mood_pair_id"].strip(),
                    rater_id=row["rater_id"].strip(),
                    guessed_energetic=row["guessed_energetic"].strip().upper(),
                    timestamp=row["timestamp"].strip(),
                ))
            except Exception as error:
                raise ValueError(f"row {row_number} in {csv_path}: {error}") from error
    return ratings


def load_output_ratings_csv(path: str | Path, metric: Literal["M4", "M5"]) -> list[OutputRating]:
    """Load one M4 or M5 instrument; output_id and rater_id identify a
    unique response, and each instrument has its own CSV/log."""
    csv_path = Path(path)
    ratings: list[OutputRating] = []
    with open(csv_path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row_number, row in enumerate(reader, start=2):
            try:
                values: dict[str, object] = {
                    "output_id": row["output_id"].strip(),
                    "rater_id": row["rater_id"].strip(),
                    "timestamp": row["timestamp"].strip(),
                }
                if metric == "M4":
                    answer = row["would_post"].strip().lower()
                    if answer not in {"yes", "no", "true", "false", "1", "0"}:
                        raise ValueError("would_post must be yes/no or true/false")
                    values["would_post"] = answer in {"yes", "true", "1"}
                else:
                    values["in_time_rating"] = int(row["in_time_rating"].strip())
                ratings.append(OutputRating(**values))
            except Exception as error:
                raise ValueError(f"row {row_number} in {csv_path}: {error}") from error
    return ratings


def _validate_subject_ratings(
    ratings: list[OutputRating | MoodIdentificationRating],
    expected_subject_ids: set[str],
    expected_rater_ids: set[str] | None,
    subject_field: Literal["output_id", "mood_pair_id"],
    required_field: Literal["would_post", "in_time_rating", "guessed_energetic"],
) -> SubjectRatingValidationResult:
    valid: list[OutputRating | MoodIdentificationRating] = []
    unknown: list[str] = []
    duplicates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rating in ratings:
        subject_id = getattr(rating, subject_field)
        if subject_id not in expected_subject_ids:
            unknown.append(f"unknown {subject_field} {subject_id!r} (rater {rating.rater_id!r})")
            continue
        if getattr(rating, required_field) is None:
            continue
        key = (subject_id, rating.rater_id)
        if key in seen:
            duplicates.append(key)
            continue
        seen.add(key)
        valid.append(rating)

    missing: list[tuple[str, str]] = []
    if expected_rater_ids is not None:
        missing = [
            (subject_id, rater_id)
            for subject_id in sorted(expected_subject_ids)
            for rater_id in sorted(expected_rater_ids)
            if (subject_id, rater_id) not in seen
        ]
    return SubjectRatingValidationResult(
        valid_ratings=valid,
        unknown_subject_errors=unknown,
        duplicate_keys=duplicates,
        missing=missing,
    )


def validate_mood_identification_ratings(
    ratings: list[MoodIdentificationRating],
    expected_mood_pair_ids: set[str],
    expected_rater_ids: set[str] | None = None,
) -> SubjectRatingValidationResult:
    return _validate_subject_ratings(
        ratings, expected_mood_pair_ids, expected_rater_ids, "mood_pair_id", "guessed_energetic"
    )


def validate_output_ratings(
    ratings: list[OutputRating],
    expected_output_ids: set[str],
    expected_rater_ids: set[str] | None = None,
    *,
    metric: Literal["M4", "M5"],
) -> SubjectRatingValidationResult:
    field = "would_post" if metric == "M4" else "in_time_rating"
    return _validate_subject_ratings(
        ratings, expected_output_ids, expected_rater_ids, "output_id", field
    )


# ---------------------------------------------------------------------------
# immutable raw log: append-only, never rewritten
# ---------------------------------------------------------------------------


def append_ratings_jsonl(ratings: list[PairwiseRating], jsonl_path: str | Path) -> None:
    """Append validated ratings to the raw, immutable log. Never
    truncates or edits existing lines -- re-running ingestion on the same
    CSV appends the same ratings again, which validate_pairwise_ratings
    will then report as duplicates rather than this function silently
    deduplicating them."""
    _append_jsonl(ratings, jsonl_path)


def _append_jsonl(ratings: list[BaseModel], jsonl_path: str | Path) -> None:
    jsonl_path = Path(jsonl_path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        for r in ratings:
            f.write(r.model_dump_json() + "\n")


def load_ratings_jsonl(jsonl_path: str | Path) -> list[PairwiseRating]:
    jsonl_path = Path(jsonl_path)
    if not jsonl_path.exists():
        return []
    ratings = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                ratings.append(PairwiseRating.model_validate_json(line))
    return ratings


def append_mood_identification_ratings_jsonl(
    ratings: list[MoodIdentificationRating], jsonl_path: str | Path
) -> None:
    _append_jsonl(ratings, jsonl_path)


def append_output_ratings_jsonl(ratings: list[OutputRating], jsonl_path: str | Path) -> None:
    _append_jsonl(ratings, jsonl_path)


def _load_jsonl(jsonl_path: str | Path, model: type[BaseModel]) -> list:
    path = Path(jsonl_path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as file:
        return [model.model_validate_json(line) for line in file if line.strip()]


def load_mood_identification_ratings_jsonl(
    jsonl_path: str | Path,
) -> list[MoodIdentificationRating]:
    return _load_jsonl(jsonl_path, MoodIdentificationRating)


def load_output_ratings_jsonl(jsonl_path: str | Path) -> list[OutputRating]:
    return _load_jsonl(jsonl_path, OutputRating)
