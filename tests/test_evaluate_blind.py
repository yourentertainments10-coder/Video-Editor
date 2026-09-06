"""Tests for poc.evaluate.blind (Step 9): A/B randomization reproducibility
and that generator identity cannot leak from the blind assignment itself.
"""
from __future__ import annotations

from poc.evaluate.blind import assign_blind_labels


def test_blind_assignment_is_reproducible_with_the_same_seed():
    pair_ids = [f"pair{i:03d}" for i in range(1, 21)]
    key_a = assign_blind_labels(pair_ids, seed=42)
    key_b = assign_blind_labels(pair_ids, seed=42)
    assert key_a.assignments == key_b.assignments


def test_blind_assignment_differs_with_a_different_seed():
    pair_ids = [f"pair{i:03d}" for i in range(1, 21)]
    key_a = assign_blind_labels(pair_ids, seed=1)
    key_b = assign_blind_labels(pair_ids, seed=2)
    a_values = [a.a_is_cutter for a in key_a.assignments.values()]
    b_values = [a.a_is_cutter for a in key_b.assignments.values()]
    assert a_values != b_values


def test_blind_assignment_is_independent_of_pair_order_and_batch_composition():
    # Same pair, same seed, regardless of what other pairs are in the batch.
    key_alone = assign_blind_labels(["pair005"], seed=7)
    key_in_batch = assign_blind_labels([f"pair{i:03d}" for i in range(1, 21)], seed=7)
    assert key_alone.assignments["pair005"].a_is_cutter == key_in_batch.assignments["pair005"].a_is_cutter


def test_blind_assignment_produces_a_reasonable_mix_not_all_one_side():
    # Not a statistical rigor test -- just confirms the RNG is actually
    # being used (not, say, always returning True).
    pair_ids = [f"pair{i:03d}" for i in range(1, 101)]
    key = assign_blind_labels(pair_ids, seed=123)
    n_a_cutter = sum(1 for a in key.assignments.values() if a.a_is_cutter)
    assert 20 < n_a_cutter < 80  # generous bounds around the expected ~50


def test_source_of_maps_both_letters_correctly_when_a_is_cutter():
    key = assign_blind_labels(["p1"], seed=1)
    # Force both directions explicitly rather than relying on the seed's
    # actual draw for this pair.
    from poc.evaluate.blind import BlindAssignment

    a_true = BlindAssignment(pair_id="p1", a_is_cutter=True)
    assert a_true.source_of == {"A": "cutter", "B": "baseline"}
    a_false = BlindAssignment(pair_id="p1", a_is_cutter=False)
    assert a_false.source_of == {"A": "baseline", "B": "cutter"}


def test_answer_key_round_trips_through_json(tmp_path):
    from poc.evaluate.blind import AnswerKey

    key = assign_blind_labels(["p1", "p2", "p3"], seed=5)
    path = tmp_path / "answer_key.json"
    key.save(path)
    reloaded = AnswerKey.load(path)
    assert reloaded == key
