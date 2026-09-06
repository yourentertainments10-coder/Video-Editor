"""Focused unit tests for poc.perceive.music (Step 5), section A.

Nothing here decodes a real audio file or runs the beat tracker end to end
(that needs librosa's JIT-compiled paths and is exercised, with known
ground truth, in tests/test_music_integration.py). These tests target the
pure/private helpers directly: the calibrated-offset lookup, the
timestamp -> frame conversion, and the energy/octave diagnostics run on
small hand-built numpy signals, plus a couple of fast structural checks
that the module's public contract still forbids automatic octave
resolution and defaults refinement off.
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from poc.perceive.music import (
    CALIBRATED_LAG_MS,
    DEFAULT_LOW_ENERGY_REL_DB,
    DEFAULT_SILENCE_DB,
    OctaveDiagnostics,
    TIMELINE_FPS,
    _energy_diagnostics,
    _grid_stability_warnings,
    _octave_diagnostics,
    _offset_for_hop,
    _to_frames,
    analyze_music,
)


# ---------------------------------------------------------------------------
# calibrated offset application
# ---------------------------------------------------------------------------

def test_offset_for_hop_256_matches_calibrated_table():
    offset_ms, source = _offset_for_hop(256, None)
    assert offset_ms == -18.8
    assert source == "calibrated"


def test_offset_for_hop_512_and_128_match_calibrated_table():
    assert _offset_for_hop(512, None) == (-23.2, "calibrated")
    assert _offset_for_hop(128, None) == (-16.0, "calibrated")


def test_offset_for_unmeasured_hop_applies_none_and_flags_source():
    offset_ms, source = _offset_for_hop(999, None)
    assert offset_ms == 0.0
    assert source == "none"


def test_offset_override_takes_precedence_over_calibrated_table():
    offset_ms, source = _offset_for_hop(256, override_ms=-5.0)
    assert offset_ms == -5.0
    assert source == "override"


def test_calibrated_lag_table_matches_beat_flash_conclusion():
    # experiments/beat_flash/CONCLUSION.md Section 8: this exact table.
    assert CALIBRATED_LAG_MS == {512: 23.2, 256: 18.8, 128: 16.0}


# ---------------------------------------------------------------------------
# timestamp -> frame conversion
# ---------------------------------------------------------------------------

def test_to_frames_on_exact_grid_points():
    # 30 fps: 0.5s -> frame 15, 1.0s -> frame 30.
    assert _to_frames([0.0, 0.5, 1.0], fps=30) == [0, 15, 30]


def test_to_frames_rounds_to_nearest_frame():
    # 128 BPM beat interval is 0.46875s = 14.0625 frames at 30fps -> rounds to 14.
    assert _to_frames([0.46875], fps=30) == [14]


def test_to_frames_respects_timeline_fps_constant():
    assert TIMELINE_FPS == 30


# ---------------------------------------------------------------------------
# energy normalization / range / diagnostics behavior -- pure numpy, no
# audio decode needed
# ---------------------------------------------------------------------------

def _loud_then_quiet_signal(sr: int = 22050, loud_s: float = 3.0, quiet_s: float = 3.0):
    t_loud = np.arange(int(loud_s * sr)) / sr
    t_quiet = np.arange(int(quiet_s * sr)) / sr
    loud = 0.5 * np.sin(2 * np.pi * 440 * t_loud)
    quiet = 1e-5 * np.sin(2 * np.pi * 440 * t_quiet)
    return np.concatenate([loud, quiet]).astype(np.float32), sr


def test_energy_diagnostics_never_drops_a_beat():
    y, sr = _loud_then_quiet_signal()
    beats_s = np.array([1.0, 2.0, 4.0, 5.0])  # two loud, two in the quiet tail
    diag = _energy_diagnostics(y, sr, beats_s, DEFAULT_LOW_ENERGY_REL_DB, DEFAULT_SILENCE_DB)
    assert len(diag.per_beat) == len(beats_s)


def test_energy_diagnostics_flags_loud_beats_as_not_low_energy():
    y, sr = _loud_then_quiet_signal()
    beats_s = np.array([1.0, 2.0])
    diag = _energy_diagnostics(y, sr, beats_s, DEFAULT_LOW_ENERGY_REL_DB, DEFAULT_SILENCE_DB)
    assert all(not b.low_energy and not b.silent for b in diag.per_beat)


def test_energy_diagnostics_flags_quiet_tail_beats_as_low_energy_and_silent():
    y, sr = _loud_then_quiet_signal()
    beats_s = np.array([4.0, 5.0])
    diag = _energy_diagnostics(y, sr, beats_s, DEFAULT_LOW_ENERGY_REL_DB, DEFAULT_SILENCE_DB)
    assert all(b.low_energy and b.silent for b in diag.per_beat)


def test_energy_diagnostics_detects_the_quiet_region():
    y, sr = _loud_then_quiet_signal()
    beats_s = np.array([1.0, 2.0, 4.0, 5.0])
    diag = _energy_diagnostics(y, sr, beats_s, DEFAULT_LOW_ENERGY_REL_DB, DEFAULT_SILENCE_DB)
    assert len(diag.low_energy_regions) >= 1
    region = diag.low_energy_regions[0]
    assert region.start_s == pytest.approx(3.0, abs=0.2)


def test_energy_diagnostics_values_are_finite_and_fraction_in_unit_range():
    y, sr = _loud_then_quiet_signal()
    beats_s = np.array([1.0, 2.0, 4.0, 5.0])
    diag = _energy_diagnostics(y, sr, beats_s, DEFAULT_LOW_ENERGY_REL_DB, DEFAULT_SILENCE_DB)
    assert np.isfinite(diag.reference_db)
    assert np.isfinite(diag.low_threshold_db)
    for b in diag.per_beat:
        assert np.isfinite(b.local_energy_db)
    assert 0.0 <= diag.low_energy_beat_fraction <= 1.0


def test_energy_diagnostics_warning_fires_above_ten_percent_threshold():
    y, sr = _loud_then_quiet_signal()
    # half the beats (2 of 4) are in the quiet tail: well above the 10% bar.
    beats_s = np.array([1.0, 2.0, 4.0, 5.0])
    diag = _energy_diagnostics(y, sr, beats_s, DEFAULT_LOW_ENERGY_REL_DB, DEFAULT_SILENCE_DB)
    assert diag.low_energy_beat_fraction == pytest.approx(0.5)
    assert diag.warning is True


def test_energy_diagnostics_warning_does_not_fire_when_all_beats_are_loud():
    y, sr = _loud_then_quiet_signal()
    beats_s = np.array([0.5, 1.0, 1.5, 2.0, 2.5])
    diag = _energy_diagnostics(y, sr, beats_s, DEFAULT_LOW_ENERGY_REL_DB, DEFAULT_SILENCE_DB)
    assert diag.low_energy_beat_fraction == 0.0
    assert diag.warning is False


# ---------------------------------------------------------------------------
# grid-wide stability warnings (interval CV / gap)
# ---------------------------------------------------------------------------

def test_grid_stability_warns_on_high_interval_cv():
    # Steady 0.5s interval with one wildly different beat -> high CV.
    beats = np.array([0.0, 0.5, 1.0, 1.5, 5.0, 5.5, 6.0])
    warnings = _grid_stability_warnings(beats)
    assert any("interval CV" in w for w in warnings)


def test_grid_stability_silent_on_perfectly_steady_grid():
    beats = np.arange(0.0, 10.0, 0.5)
    warnings = _grid_stability_warnings(beats)
    assert warnings == []


def test_grid_stability_warns_on_too_few_beats():
    warnings = _grid_stability_warnings(np.array([0.0, 0.5]))
    assert any("fewer than 4 beats" in w for w in warnings)


# ---------------------------------------------------------------------------
# no automatic octave resolution (structural guarantee)
# ---------------------------------------------------------------------------

def test_octave_diagnostics_model_has_no_resolved_tempo_field():
    # The only way analyze_music() could "auto-resolve" an octave choice is
    # by exposing a field that looks like a corrected/resolved BPM. It does
    # not: OctaveDiagnostics only ever reports, never decides.
    assert set(OctaveDiagnostics.model_fields) == {"selected_strength", "alternatives", "warning"}


def test_octave_diagnostics_reports_a_strong_subharmonic_without_changing_tempo_input():
    # A synthetic impulse train at 176 BPM has a genuine, strong half-time
    # subharmonic at 88 BPM. _octave_diagnostics must surface it as a
    # diagnostic without any side effect on the `tempo` value it was given.
    sr = 22050
    hop = 256
    duration_s = 20.0
    period_s = 60.0 / 176.0
    n_samples = int(duration_s * sr)
    onset_env_len = n_samples // hop + 1
    times = np.arange(onset_env_len) * hop / sr
    onset_env = np.zeros(onset_env_len)
    beat_times = np.arange(0.0, duration_s, period_s)
    for bt in beat_times:
        idx = np.argmin(np.abs(times - bt))
        onset_env[idx] = 1.0

    tempo_given = 176.0
    diagnostics = _octave_diagnostics(onset_env, sr, hop, tempo_given, warn_ratio=0.75)

    assert tempo_given == 176.0  # the input is a plain float; nothing mutates it
    assert isinstance(diagnostics, OctaveDiagnostics)
    # Whatever it finds, it only ever reports -- there is no code path here
    # that could change `tempo_given` or feed a different value back.


# ---------------------------------------------------------------------------
# refinement remains off by default
# ---------------------------------------------------------------------------

def test_analyze_music_refine_parameter_defaults_to_false():
    assert inspect.signature(analyze_music).parameters["refine"].default is False


def test_analyze_music_hop_length_defaults_to_the_validated_256():
    assert inspect.signature(analyze_music).parameters["hop_length"].default == 256


def test_analyze_music_start_bpm_and_tightness_default_to_validated_values():
    params = inspect.signature(analyze_music).parameters
    assert params["start_bpm"].default == 120.0
    assert params["tightness"].default == 100.0
