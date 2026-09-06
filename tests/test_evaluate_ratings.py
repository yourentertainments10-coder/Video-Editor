"""Tests for poc.evaluate.ratings (Step 9): ingestion validation,
duplicates, incomplete ratings, and immutability of the raw log.
"""
from __future__ import annotations

import csv

import pytest
from pydantic import ValidationError

from poc.evaluate.ratings import (
    OutputRating,
    PairwiseRating,
    append_ratings_jsonl,
    load_pairwise_ratings_csv,
    load_ratings_jsonl,
    validate_pairwise_ratings,
)


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["pair_id", "rater_id", "chosen_video", "reason", "timestamp"])
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# CSV ingestion
# ---------------------------------------------------------------------------

def test_load_valid_csv(tmp_path):
    path = tmp_path / "ratings.csv"
    _write_csv(path, [["pair001", "alice", "A", "clear winner", "2026-01-01T00:00:00Z"]])
    ratings = load_pairwise_ratings_csv(path)
    assert len(ratings) == 1
    assert ratings[0].pair_id == "pair001"
    assert ratings[0].chosen_video == "A"
    assert ratings[0].reason == "clear winner"


def test_load_csv_normalizes_chosen_video_case(tmp_path):
    path = tmp_path / "ratings.csv"
    _write_csv(path, [["pair001", "alice", "a", "", "2026-01-01T00:00:00Z"]])
    ratings = load_pairwise_ratings_csv(path)
    assert ratings[0].chosen_video == "A"


def test_load_csv_raises_a_clear_error_naming_the_bad_row(tmp_path):
    path = tmp_path / "ratings.csv"
    _write_csv(path, [["pair001", "alice", "C", "", "2026-01-01T00:00:00Z"]])  # invalid chosen_video
    with pytest.raises(ValueError, match="row 2"):
        load_pairwise_ratings_csv(path)


def test_load_csv_treats_blank_reason_as_none(tmp_path):
    path = tmp_path / "ratings.csv"
    _write_csv(path, [["pair001", "alice", "A", "", "2026-01-01T00:00:00Z"]])
    ratings = load_pairwise_ratings_csv(path)
    assert ratings[0].reason is None


# ---------------------------------------------------------------------------
# validation: unknown pairs, duplicates, completeness
# ---------------------------------------------------------------------------

def _rating(pair_id, rater_id, chosen="A"):
    return PairwiseRating(pair_id=pair_id, rater_id=rater_id, chosen_video=chosen, timestamp="2026-01-01T00:00:00Z")


def test_validate_accepts_ratings_for_known_pairs():
    ratings = [_rating("pair001", "alice"), _rating("pair002", "alice")]
    result = validate_pairwise_ratings(ratings, expected_pair_ids={"pair001", "pair002"})
    assert len(result.valid_ratings) == 2
    assert result.is_clean


def test_validate_flags_unknown_pair_id():
    ratings = [_rating("pair999", "alice")]
    result = validate_pairwise_ratings(ratings, expected_pair_ids={"pair001"})
    assert len(result.valid_ratings) == 0
    assert len(result.unknown_pair_errors) == 1
    assert not result.is_clean


def test_validate_flags_duplicate_rating_and_keeps_the_first():
    first = _rating("pair001", "alice", chosen="A")
    duplicate = _rating("pair001", "alice", chosen="B")
    result = validate_pairwise_ratings([first, duplicate], expected_pair_ids={"pair001"})
    assert len(result.valid_ratings) == 1
    assert result.valid_ratings[0].chosen_video == "A"  # first occurrence kept
    assert result.duplicate_keys == [("pair001", "alice")]
    assert not result.is_clean


def test_validate_reports_incomplete_ratings():
    ratings = [_rating("pair001", "alice")]
    result = validate_pairwise_ratings(
        ratings, expected_pair_ids={"pair001", "pair002"}, expected_rater_ids={"alice", "bob"}
    )
    assert not result.is_complete
    assert ("pair001", "bob") in result.missing
    assert ("pair002", "alice") in result.missing
    assert ("pair002", "bob") in result.missing
    assert ("pair001", "alice") not in result.missing


def test_validate_is_complete_when_every_combination_is_present():
    ratings = [_rating("pair001", "alice"), _rating("pair001", "bob")]
    result = validate_pairwise_ratings(
        ratings, expected_pair_ids={"pair001"}, expected_rater_ids={"alice", "bob"}
    )
    assert result.is_complete


# ---------------------------------------------------------------------------
# immutable raw log: append-only
# ---------------------------------------------------------------------------

def test_append_ratings_jsonl_is_append_only(tmp_path):
    path = tmp_path / "ratings.jsonl"
    append_ratings_jsonl([_rating("pair001", "alice")], path)
    append_ratings_jsonl([_rating("pair002", "bob")], path)
    loaded = load_ratings_jsonl(path)
    assert len(loaded) == 2
    assert loaded[0].pair_id == "pair001"
    assert loaded[1].pair_id == "pair002"


def test_reingesting_the_same_rating_appends_again_not_overwrites(tmp_path):
    path = tmp_path / "ratings.jsonl"
    rating = _rating("pair001", "alice")
    append_ratings_jsonl([rating], path)
    append_ratings_jsonl([rating], path)
    loaded = load_ratings_jsonl(path)
    assert len(loaded) == 2  # both present -- validate_pairwise_ratings is what catches the duplicate


def test_load_ratings_jsonl_on_missing_file_returns_empty_list(tmp_path):
    assert load_ratings_jsonl(tmp_path / "does_not_exist.jsonl") == []


# ---------------------------------------------------------------------------
# OutputRating (M4/M5) validation
# ---------------------------------------------------------------------------

def test_output_rating_accepts_valid_in_time_rating():
    r = OutputRating(output_id="o1", rater_id="alice", in_time_rating=4, timestamp="2026-01-01T00:00:00Z")
    assert r.in_time_rating == 4


def test_output_rating_rejects_out_of_range_in_time_rating():
    with pytest.raises(ValidationError):
        OutputRating(output_id="o1", rater_id="alice", in_time_rating=6, timestamp="2026-01-01T00:00:00Z")
    with pytest.raises(ValidationError):
        OutputRating(output_id="o1", rater_id="alice", in_time_rating=0, timestamp="2026-01-01T00:00:00Z")
