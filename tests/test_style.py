"""Focused tests for poc.decide.style (Step 7), section B.

Exactly the two MVP moods, deterministic weights, no hidden randomness.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from poc.decide.style import CutterParams, style_for_mood


def test_exactly_two_moods_are_supported():
    style_for_mood("energetic")
    style_for_mood("calm")
    with pytest.raises(ValueError):
        style_for_mood("dramatic")  # type: ignore[arg-type]


def test_energetic_weights_match_the_style_table():
    params = style_for_mood("energetic")
    assert params.motion_base == 0.5
    assert params.motion_scale == 0.5


def test_calm_weights_match_the_style_table():
    params = style_for_mood("calm")
    assert params.motion_base == 0.1
    assert params.motion_scale == 0.3


def test_energetic_beats_per_cut_rule():
    params = style_for_mood("energetic")
    assert params.beats_per_cut(0.6) == 1  # e >= 0.6
    assert params.beats_per_cut(1.0) == 1
    assert params.beats_per_cut(0.59) == 2  # otherwise
    assert params.beats_per_cut(0.0) == 2


def test_calm_beats_per_cut_rule():
    params = style_for_mood("calm")
    assert params.beats_per_cut(0.5) == 4  # e <= 0.5
    assert params.beats_per_cut(0.0) == 4
    assert params.beats_per_cut(0.51) == 2  # otherwise
    assert params.beats_per_cut(1.0) == 2


def test_style_for_mood_is_deterministic_across_repeated_calls():
    a = style_for_mood("energetic")
    b = style_for_mood("energetic")
    assert a == b


def test_style_for_mood_returns_independent_copies():
    a = style_for_mood("energetic")
    b = style_for_mood("energetic")
    assert a is not b  # mutating one must never affect the other or the shared table


def test_no_hidden_randomness_in_repeated_beats_per_cut_calls():
    params = style_for_mood("calm")
    results = {params.beats_per_cut(0.3) for _ in range(20)}
    assert results == {4}


def test_cutter_params_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CutterParams(mood="energetic", motion_base=0.5, motion_scale=0.5, extra_field=1)  # type: ignore[call-arg]
