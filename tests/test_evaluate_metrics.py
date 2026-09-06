"""Tests for poc.evaluate.metrics (Step 9): metric calculations, the
exact 70% M2 threshold boundary, and below-threshold failure.
"""
from __future__ import annotations

from poc.evaluate.blind import AnswerKey, BlindAssignment
from poc.evaluate.metrics import (
    M2_PREFERENCE_THRESHOLD,
    M3_MOOD_ID_THRESHOLD,
    M4_MIN_OUTPUT_FRACTION,
    M4_MIN_RATERS_YES,
    M5_MEAN_THRESHOLD,
    compute_m2,
    compute_m3,
    compute_m4,
    compute_m5,
    decide,
    wilson_interval,
)
from poc.evaluate.metrics import M1Result
from poc.evaluate.ratings import MoodIdentificationRating, OutputRating, PairwiseRating


def _pw(pair_id, rater_id, chosen):
    return PairwiseRating(pair_id=pair_id, rater_id=rater_id, chosen_video=chosen, timestamp="2026-01-01T00:00:00Z")


def _answer_key(a_is_cutter_by_pair: dict[str, bool]) -> AnswerKey:
    return AnswerKey(
        seed=0,
        assignments={pid: BlindAssignment(pair_id=pid, a_is_cutter=v) for pid, v in a_is_cutter_by_pair.items()},
    )


# ---------------------------------------------------------------------------
# M2: exact threshold value confirmed, and the 70% boundary itself
# ---------------------------------------------------------------------------

def test_m2_threshold_constant_matches_mvp_spec():
    assert M2_PREFERENCE_THRESHOLD == 0.70


def test_m2_computes_preference_rate_correctly():
    key = _answer_key({"p1": True, "p2": False})  # p1: A=cutter, p2: A=baseline
    ratings = [
        _pw("p1", "alice", "A"),  # chose cutter
        _pw("p2", "alice", "B"),  # chose cutter (B is cutter here)
    ]
    result = compute_m2(ratings, key)
    assert result.total_judgments == 2
    assert result.cutter_wins == 2
    assert result.baseline_wins == 0
    assert result.preference_rate == 1.0


def test_m2_exactly_at_70_percent_passes():
    # 21 of 30 = exactly 70%.
    key = _answer_key({f"p{i}": True for i in range(30)})  # A is always cutter
    ratings = [_pw(f"p{i}", "r", "A" if i < 21 else "B") for i in range(30)]
    result = compute_m2(ratings, key)
    assert result.total_judgments == 30
    assert result.cutter_wins == 21
    assert result.preference_rate == 0.70
    assert result.passed is True


def test_m2_just_below_70_percent_fails():
    # 20 of 30 = 66.67%, just under threshold.
    key = _answer_key({f"p{i}": True for i in range(30)})
    ratings = [_pw(f"p{i}", "r", "A" if i < 20 else "B") for i in range(30)]
    result = compute_m2(ratings, key)
    assert result.preference_rate < 0.70
    assert result.passed is False


def test_m2_at_50_percent_fails():
    key = _answer_key({f"p{i}": True for i in range(10)})
    ratings = [_pw(f"p{i}", "r", "A" if i < 5 else "B") for i in range(10)]
    result = compute_m2(ratings, key)
    assert result.preference_rate == 0.50
    assert result.passed is False


def test_m2_with_zero_judgments_fails_and_reports_zero():
    key = _answer_key({"p1": True})
    result = compute_m2([], key)
    assert result.total_judgments == 0
    assert result.preference_rate == 0.0
    assert result.passed is False
    assert result.confidence_interval_95 is None


def test_m2_ignores_ratings_for_pairs_not_in_the_answer_key():
    key = _answer_key({"p1": True})
    ratings = [_pw("p1", "alice", "A"), _pw("p999", "alice", "A")]
    result = compute_m2(ratings, key)
    assert result.total_judgments == 1  # the unknown pair is silently excluded here;
    # validate_pairwise_ratings (tested separately) is what surfaces it as an error


def test_wilson_interval_contains_the_point_estimate():
    lo, hi = wilson_interval(21, 30)
    assert lo < 21 / 30 < hi


def test_wilson_interval_widens_with_fewer_judgments():
    lo_small, hi_small = wilson_interval(7, 10)
    lo_large, hi_large = wilson_interval(70, 100)
    assert (hi_small - lo_small) > (hi_large - lo_large)


def test_wilson_interval_zero_total_returns_zero_zero():
    assert wilson_interval(0, 0) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# M3
# ---------------------------------------------------------------------------

def _mood_rating(mood_pair_id, rater_id, guessed):
    return MoodIdentificationRating(
        mood_pair_id=mood_pair_id, rater_id=rater_id, guessed_energetic=guessed, timestamp="2026-01-01T00:00:00Z"
    )


def test_m3_threshold_constant_matches_mvp_spec():
    assert M3_MOOD_ID_THRESHOLD == 0.80


def test_m3_computes_accuracy():
    energetic_side = {"mp1": "A", "mp2": "B"}
    ratings = [_mood_rating("mp1", "alice", "A"), _mood_rating("mp2", "alice", "A")]  # 1 correct, 1 wrong
    result = compute_m3(ratings, energetic_side)
    assert result.total_judgments == 2
    assert result.correct == 1
    assert result.accuracy == 0.5
    assert result.passed is False


def test_m3_passes_at_80_percent():
    energetic_side = {f"mp{i}": "A" for i in range(10)}
    ratings = [_mood_rating(f"mp{i}", "r", "A" if i < 8 else "B") for i in range(10)]
    result = compute_m3(ratings, energetic_side)
    assert result.accuracy == 0.80
    assert result.passed is True


# ---------------------------------------------------------------------------
# M4
# ---------------------------------------------------------------------------

def _out_rating(output_id, rater_id, would_post):
    return OutputRating(output_id=output_id, rater_id=rater_id, would_post=would_post, timestamp="2026-01-01T00:00:00Z")


def test_m4_constants_match_mvp_spec():
    assert M4_MIN_RATERS_YES == 3
    assert M4_MIN_OUTPUT_FRACTION == 0.50


def test_m4_output_passes_with_at_least_3_of_5_yes():
    ratings = [_out_rating("o1", f"r{i}", would_post=(i < 3)) for i in range(5)]  # 3 yes, 2 no
    result = compute_m4(ratings)
    assert result.yes_count_by_output["o1"] == 3
    assert result.passing_outputs == 1
    assert result.total_outputs == 1
    assert result.passed is True


def test_m4_output_fails_with_only_2_of_5_yes():
    ratings = [_out_rating("o1", f"r{i}", would_post=(i < 2)) for i in range(5)]
    result = compute_m4(ratings)
    assert result.passing_outputs == 0
    assert result.passed is False


def test_m4_overall_passes_when_half_of_outputs_qualify():
    # o1 and o2 qualify (>=3 yes), o3 and o4 do not -> 2 of 4 = 50%.
    ratings = []
    for oid, n_yes in (("o1", 3), ("o2", 4), ("o3", 1), ("o4", 0)):
        ratings += [_out_rating(oid, f"r{i}", would_post=(i < n_yes)) for i in range(5)]
    result = compute_m4(ratings)
    assert result.total_outputs == 4
    assert result.passing_outputs == 2
    assert result.passing_fraction == 0.5
    assert result.passed is True


# ---------------------------------------------------------------------------
# M5
# ---------------------------------------------------------------------------

def _sync_rating(output_id, rater_id, rating):
    return OutputRating(output_id=output_id, rater_id=rater_id, in_time_rating=rating, timestamp="2026-01-01T00:00:00Z")


def test_m5_threshold_matches_mvp_spec():
    assert M5_MEAN_THRESHOLD == 4.0


def test_m5_mean_at_or_above_threshold_passes():
    ratings = [_sync_rating("o1", f"r{i}", 4) for i in range(5)]
    result = compute_m5(ratings)
    assert result.mean_rating == 4.0
    assert result.passed is True


def test_m5_mean_below_threshold_fails():
    ratings = [_sync_rating("o1", f"r{i}", 3) for i in range(5)]
    result = compute_m5(ratings)
    assert result.mean_rating == 3.0
    assert result.passed is False


# ---------------------------------------------------------------------------
# decision rule
# ---------------------------------------------------------------------------

def _m1_pass():
    return M1Result(total_outputs=60, passing_outputs=60, failures=[])


def test_decide_all_pass():
    key = _answer_key({f"p{i}": True for i in range(30)})
    ratings = [_pw(f"p{i}", "r", "A" if i < 25 else "B") for i in range(30)]
    m2 = compute_m2(ratings, key)
    verdict = decide(_m1_pass(), m2)
    assert "hypothesis validated" in verdict


def test_decide_m2_passes_m4_fails():
    key = _answer_key({f"p{i}": True for i in range(30)})
    ratings = [_pw(f"p{i}", "r", "A" if i < 25 else "B") for i in range(30)]
    m2 = compute_m2(ratings, key)
    m4 = compute_m4([_out_rating("o1", f"r{i}", would_post=False) for i in range(5)])
    verdict = decide(_m1_pass(), m2, m4=m4)
    assert "not good enough to ship" in verdict


def test_decide_m2_fails_near_50():
    key = _answer_key({f"p{i}": True for i in range(10)})
    ratings = [_pw(f"p{i}", "r", "A" if i < 5 else "B") for i in range(10)]
    m2 = compute_m2(ratings, key)
    verdict = decide(_m1_pass(), m2)
    assert "adds nothing" in verdict


def test_decide_m2_fails_after_iteration_with_faces():
    key = _answer_key({f"p{i}": True for i in range(10)})
    ratings = [_pw(f"p{i}", "r", "A" if i < 5 else "B") for i in range(10)]
    m2 = compute_m2(ratings, key)
    verdict = decide(_m1_pass(), m2, iterated_with_faces=True)
    assert "Stop and rethink perception" in verdict
