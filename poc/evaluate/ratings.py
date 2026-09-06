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
    "load_pairwise_ratings_csv",
    "validate_pairwise_ratings",
    "append_ratings_jsonl",
    "load_ratings_jsonl",
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
    if expected_rater_ids:
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


# ---------------------------------------------------------------------------
# immutable raw log: append-only, never rewritten
# ---------------------------------------------------------------------------


def append_ratings_jsonl(ratings: list[PairwiseRating], jsonl_path: str | Path) -> None:
    """Append validated ratings to the raw, immutable log. Never
    truncates or edits existing lines -- re-running ingestion on the same
    CSV appends the same ratings again, which validate_pairwise_ratings
    will then report as duplicates rather than this function silently
    deduplicating them."""
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
