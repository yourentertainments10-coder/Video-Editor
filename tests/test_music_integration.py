"""Integration tests for poc.perceive.music (Step 5), sections B/C.

Ground truth: the two synthetic fixtures generated and committed by the
Step 01 Beat Flash PoC (experiments/beat_flash/fixtures/synth_*.wav, kept
there explicitly as regression fixtures per CONCLUSION.md Section 6 point
9). They are read here as plain test data -- no code from that experiment
is imported.

Real-track checks run against experiments/beat_flash/fixtures/real/*.mp3
when present. That directory is git-ignored (the audio is copyrighted), so
these tests skip themselves when the files are not on disk -- satisfying
"at least one real/local music fixture if available" without making the
suite depend on media that will not exist in a fresh checkout or CI.
CONCLUSION.md Section 8 records exact reference numbers for these two
tracks from the original PoC; this module is expected to reproduce them
closely since it runs the same validated librosa configuration.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from poc.perceive.music import analyze_music

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "experiments" / "beat_flash" / "fixtures"
REAL_DIR = FIXTURES_DIR / "real"

CHOR_MP3 = REAL_DIR / "Chor.mp3"
BYE_MP3 = REAL_DIR / "BYE - Aditya Bhardwaj (Official Music Video).mp3"


def _nearest_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(b) == 0:
        return np.full(len(a), np.inf)
    idx = np.searchsorted(b, a)
    left = np.abs(a - b[np.clip(idx - 1, 0, len(b) - 1)])
    right = np.abs(b[np.clip(idx, 0, len(b) - 1)] - a)
    return np.minimum(left, right)


def _f_measure_and_median_offset(detected: np.ndarray, truth: np.ndarray, tol_s: float = 0.07):
    d_det = _nearest_distance(detected, truth)
    d_tru = _nearest_distance(truth, detected)
    precision = float(np.mean(d_det <= tol_s)) if len(detected) else 0.0
    recall = float(np.mean(d_tru <= tol_s)) if len(truth) else 0.0
    f = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    idx = np.searchsorted(truth, detected)
    cand_lo = truth[np.clip(idx - 1, 0, len(truth) - 1)]
    cand_hi = truth[np.clip(idx, 0, len(truth) - 1)]
    nearest = np.where(np.abs(cand_lo - detected) <= np.abs(cand_hi - detected), cand_lo, cand_hi)
    signed = (detected - nearest)[np.abs(detected - nearest) <= tol_s]
    median_offset_ms = float(np.median(signed) * 1000.0) if len(signed) else None
    return f, median_offset_ms


# ---------------------------------------------------------------------------
# synthetic ground truth
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synth_128():
    truth = json.loads((FIXTURES_DIR / "synth_128.truth.json").read_text())
    result = analyze_music(FIXTURES_DIR / "synth_128.wav")
    return result, truth


@pytest.fixture(scope="module")
def synth_92():
    truth = json.loads((FIXTURES_DIR / "synth_92.truth.json").read_text())
    result = analyze_music(FIXTURES_DIR / "synth_92.wav")
    return result, truth


def test_synth_128_detected_bpm_close_to_truth(synth_128):
    result, truth = synth_128
    assert result.bpm == pytest.approx(truth["bpm"], rel=0.02)  # within 2%


def test_synth_128_beat_count_close_to_truth(synth_128):
    result, truth = synth_128
    assert len(result.beats_used_s) == pytest.approx(len(truth["beats_s"]), abs=3)


def test_synth_128_corrected_timestamps_match_truth_within_5ms_median(synth_128):
    result, truth = synth_128
    f, median_offset_ms = _f_measure_and_median_offset(
        np.array(result.beats_corrected_s), np.array(truth["beats_s"])
    )
    assert f >= 0.98
    assert median_offset_ms is not None
    assert abs(median_offset_ms) <= 5.0  # CONCLUSION.md's own regression bound


def test_synth_128_frame_positions_match_ms_to_frame_of_used_beats(synth_128):
    from poc.timeline.schema import ms_to_frame

    result, _ = synth_128
    expected = [ms_to_frame(t * 1000.0, fps=30) for t in result.beats_used_s]
    assert result.beats_used_frame == expected


def test_synth_128_octave_diagnostics_present_but_tempo_unchanged(synth_128):
    # CONCLUSION.md: "The octave diagnostic warns on both synthetic tracks
    # (half-time at 88%, double-time at 91%) because straight eighth-note
    # hats create genuine sub-harmonics." bpm must still be the ~128 estimate.
    result, truth = synth_128
    assert result.octave.warning is True
    assert result.bpm == pytest.approx(truth["bpm"], rel=0.02)


def test_synth_128_refine_defaults_off_and_used_equals_corrected(synth_128):
    result, _ = synth_128
    assert result.refine_applied is False
    assert result.beats_used_s == result.beats_corrected_s


def test_synth_128_raw_beats_preserved_alongside_corrected(synth_128):
    result, _ = synth_128
    assert len(result.beats_raw_s) == len(result.beats_corrected_s)
    # correction is a constant additive shift (the calibrated offset), so
    # raw and corrected differ by exactly offset_ms for every beat.
    diffs = np.array(result.beats_corrected_s) - np.array(result.beats_raw_s)
    assert diffs == pytest.approx(result.offset_ms / 1000.0, abs=1e-9)


def test_synth_92_detected_bpm_close_to_truth(synth_92):
    result, truth = synth_92
    assert result.bpm == pytest.approx(truth["bpm"], rel=0.02)


def test_synth_92_corrected_timestamps_match_truth(synth_92):
    result, truth = synth_92
    f, median_offset_ms = _f_measure_and_median_offset(
        np.array(result.beats_corrected_s), np.array(truth["beats_s"])
    )
    assert f >= 0.98
    assert abs(median_offset_ms) <= 5.0


def test_synth_92_no_beats_removed_for_low_energy(synth_92):
    result, truth = synth_92
    # decision 6: energy never removes a beat, even the drumless-intro ones.
    assert len(result.energy.per_beat) == len(result.beats_used_s)


# ---------------------------------------------------------------------------
# refinement can be explicitly requested but is never the default path
# ---------------------------------------------------------------------------

def test_refine_true_is_opt_in_and_changes_the_used_list_source():
    result_default = analyze_music(FIXTURES_DIR / "synth_92.wav")
    result_refined = analyze_music(FIXTURES_DIR / "synth_92.wav", refine=True)
    assert result_default.refine_applied is False
    assert result_refined.refine_applied is True
    assert result_default.beats_used_s == result_default.beats_corrected_s
    # Refinement is still bounded (a narrow window), never wildly different.
    diffs = np.abs(np.array(result_refined.beats_used_s) - np.array(result_refined.beats_corrected_s))
    assert np.all(diffs <= 0.020 + 1e-9)


# ---------------------------------------------------------------------------
# real, local tracks (skipped when not present -- gitignored, copyrighted)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not CHOR_MP3.exists(), reason="real music fixture not present locally (gitignored)")
def test_real_track_chor_matches_beat_flash_reference_diagnostics():
    result = analyze_music(CHOR_MP3)
    # CONCLUSION.md Section 8 reference: 120.2 BPM, 404 beats, CV 4.6%,
    # 48/404 (11.9%) beats in low-energy regions, 3 in silence.
    assert result.bpm == pytest.approx(120.2, abs=1.0)
    assert len(result.beats_used_s) == pytest.approx(404, abs=5)
    intervals = np.diff(result.beats_used_s)
    cv = float(np.std(intervals) / np.median(intervals))
    assert cv == pytest.approx(0.046, abs=0.015)
    assert result.energy.low_energy_beat_fraction == pytest.approx(0.119, abs=0.03)
    n_silent = sum(1 for b in result.energy.per_beat if b.silent)
    assert n_silent >= 1


@pytest.mark.skipif(not BYE_MP3.exists(), reason="real music fixture not present locally (gitignored)")
def test_real_track_bye_reproduces_the_documented_octave_ambiguity():
    result = analyze_music(BYE_MP3)
    # CONCLUSION.md Section 8 reference: 166.7 BPM, 475 beats, half-time
    # alternative at 94% strength -- the canonical octave-ambiguity case.
    assert result.bpm == pytest.approx(166.7, abs=1.0)
    assert len(result.beats_used_s) == pytest.approx(475, abs=5)
    assert result.octave.warning is True
    half_time = next(a for a in result.octave.alternatives if a.label == "half_time")
    assert half_time.bpm == pytest.approx(83.4, abs=1.0)
    assert half_time.strength_ratio == pytest.approx(0.94, abs=0.05)
    # The critical behavior: bpm is NOT silently flipped to the half-time value.
    assert result.bpm > 150.0


@pytest.mark.skipif(not CHOR_MP3.exists(), reason="real music fixture not present locally (gitignored)")
def test_real_track_chor_musicmap_round_trips_through_json(tmp_path: Path):
    result = analyze_music(CHOR_MP3)
    out_path = tmp_path / "chor_musicmap.json"
    result.save(out_path)
    from poc.perceive.music import MusicMap

    reloaded = MusicMap.load(out_path)
    assert reloaded == result
