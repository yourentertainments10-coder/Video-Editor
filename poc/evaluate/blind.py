"""poc/evaluate/blind.py: blind A/B presentation.

A rater must never be able to tell, from anything they are shown, whether
"A" or "B" is the cutter-v0 or baseline-v0 output. This module is the one
place that mapping exists, and it is designed to be kept in a file
completely separate from anything a rater sees (poc.evaluate.generate
writes it to <out>/answer_key.json, never into the <out>/dataset/ folder
that gets handed to raters).

Assignment is deterministic per (pair_id, seed): each pair gets its own
independent random source seeded from the pair_id and a single evaluation
run seed, so the same seed always reproduces the same A/B assignment for
a given pair regardless of what other pairs exist in the run or what
order they are processed in -- reproducible, and not fragile to batch
composition changing.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "BlindAssignment",
    "AnswerKey",
    "MoodBlindAssignment",
    "MoodAnswerKey",
    "assign_blind_labels",
]


class BlindAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    a_is_cutter: bool

    @property
    def source_of(self) -> dict[Literal["A", "B"], Literal["cutter", "baseline"]]:
        return (
            {"A": "cutter", "B": "baseline"}
            if self.a_is_cutter
            else {"A": "baseline", "B": "cutter"}
        )


class AnswerKey(BaseModel):
    """The private mapping for a whole evaluation run. Never write this
    into the rater-facing dataset directory."""

    model_config = ConfigDict(extra="forbid")

    seed: int
    assignments: dict[str, BlindAssignment]

    @classmethod
    def load(cls, path: str | Path) -> "AnswerKey":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


class MoodBlindAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood_pair_id: str
    a_is_energetic: bool


class MoodAnswerKey(BaseModel):
    """Private M3 A/B mapping, kept outside the rater-facing dataset."""

    model_config = ConfigDict(extra="forbid")

    seed: int
    assignments: dict[str, MoodBlindAssignment]

    @property
    def energetic_side(self) -> dict[str, Literal["A", "B"]]:
        return {
            mood_pair_id: "A" if assignment.a_is_energetic else "B"
            for mood_pair_id, assignment in self.assignments.items()
        }

    @classmethod
    def load(cls, path: str | Path) -> "MoodAnswerKey":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _pair_rng(pair_id: str, seed: int) -> random.Random:
    return random.Random(f"{seed}:{pair_id}")


def assign_blind_labels(pair_ids: Iterable[str], seed: int) -> AnswerKey:
    """Deterministically assign, for every pair_id, whether "A" is the
    cutter or the baseline output. Same pair_id + same seed always
    produces the same assignment."""
    assignments = {
        pair_id: BlindAssignment(pair_id=pair_id, a_is_cutter=_pair_rng(pair_id, seed).random() < 0.5)
        for pair_id in pair_ids
    }
    return AnswerKey(seed=seed, assignments=assignments)
