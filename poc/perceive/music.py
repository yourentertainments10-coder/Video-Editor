"""poc/perceive/music.py: music perception -- a timing grid, not an edit.

This is a clean re-implementation of the validated behavior documented in
experiments/beat_flash/CONCLUSION.md (closed 2026-09-06 after two
iterations). It imports nothing from experiments/beat_flash/; every
parameter value and every algorithmic decision below is carried forward
because that document says it was validated, not because any code was
copied. Where this module and that PoC agree, it is because the same
librosa calls with the same parameters were deliberately used again.

What this module is NOT: it does not decide where to cut. A beat is a
timing candidate, not an editing instruction (CONCLUSION.md Section 5,
"a beat grid is not automatically a cut grid"). Turning this output into
allowed cut points, with pacing and energy-aware density, is the Decide
layer's job (Step 7, poc/decide/grid.py), not this one.

Ten decisions carried forward from CONCLUSION.md Section 6, each pinned to
where it lives below:

 1. librosa is sufficient for the MVP beat grid, at hop 256 with the
    calibrated offset applied -- DEFAULT_HOP_LENGTH, CALIBRATED_LAG_MS.
 2. Never consume raw tracker timestamps directly -- MusicMap always
    carries beats_raw_s *and* beats_corrected_s; downstream code is meant
    to read beats_used_s (or beats_used_frame), never beats_raw_s, for
    timing.
 3. Do not snap beats to nearby transients -- refine_applied defaults to
    False; refinement is exposed only as an explicit, opt-in parameter,
    reusing the same narrow percussive-only technique the PoC validated as
    least harmful, never the full-mix version it found actively harmful.
 4. Store raw, corrected, and diagnostics together -- see the MusicMap
    fields below: offset_ms, offset_source, octave, energy, windows.
 5. Octave choice is a user-visible setting -- OctaveDiagnostics.warning is
    informational; analyze_music() never changes `bpm` based on it.
 6. Energy gating belongs to the grid, not the tracker -- energy flags are
    attached to every beat; no beat is ever removed for being quiet.
 7. Per-region confidence, not a single number -- WindowSummary per 20 s.
 8. The 4/4 bar heuristic does not ship -- no downbeat/bar detection here
    at all (not even the unverified one); MVP v0's grid is beats + energy
    only, per docs/MVP.md Section 7.
 9. Full tracks only as input; the fixture generator and its two synthetic
    tracks (experiments/beat_flash/fixtures/synth_*.wav, kept as regression
    fixtures per that document) are used read-only by this module's tests.
10. Standard way to check a change by ear (60 fps flash render) is out of
    scope here -- not needed for the MVP v0 grid itself.

Decoding: mp3/m4a/etc. need a real audio decoder, and this project
deliberately has no system-wide FFmpeg. Like the renderer (Step 4), this
module decodes through the imageio-ffmpeg bundled binary first, to a mono
WAV at the analysis sample rate, and only then hands the samples to
librosa/soundfile -- never relying on librosa's own decoding backends,
which may or may not find an MP3-capable decoder on a given machine.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import imageio_ffmpeg
import librosa
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from poc.timeline.schema import TIMELINE_FPS, ms_to_frame
from poc.perceive.cache import load_or_compute

__all__ = [
    "SAMPLE_RATE",
    "DEFAULT_HOP_LENGTH",
    "DEFAULT_START_BPM",
    "DEFAULT_TIGHTNESS",
    "CALIBRATED_LAG_MS",
    "OCTAVE_PLAUSIBLE_BPM_RANGE",
    "DEFAULT_OCTAVE_WARN_RATIO",
    "DEFAULT_LOW_ENERGY_REL_DB",
    "DEFAULT_SILENCE_DB",
    "WINDOW_SECONDS",
    "OctaveAlternative",
    "OctaveDiagnostics",
    "LowEnergyRegion",
    "BeatEnergy",
    "EnergyDiagnostics",
    "WindowSummary",
    "MusicMap",
    "analyze_music",
]

# ---------------------------------------------------------------------------
# validated constants -- experiments/beat_flash/CONCLUSION.md Section 4 and
# the calibration measurement in its README ("Offset calibration"). Values,
# not code, are what is carried forward.
# ---------------------------------------------------------------------------

SAMPLE_RATE = 22050
DEFAULT_HOP_LENGTH = 256
DEFAULT_START_BPM = 120.0
DEFAULT_TIGHTNESS = 100.0

# Calibrated tracker lag, in ms, by analysis hop (samples at 22050 Hz),
# measured on synthetic ground truth and cross-checked on real tracks.
# Hops without a measurement get no correction, per CONCLUSION.md's rule
# ("unmeasured hops apply no correction and raise a warning").
CALIBRATED_LAG_MS: dict[int, float] = {512: 23.2, 256: 18.8, 128: 16.0}

OCTAVE_PLAUSIBLE_BPM_RANGE = (50.0, 200.0)
DEFAULT_OCTAVE_WARN_RATIO = 0.75

_ENERGY_FRAME_LENGTH = 2048
_ENERGY_HOP_LENGTH = 512
DEFAULT_LOW_ENERGY_REL_DB = -12.0
DEFAULT_SILENCE_DB = -50.0
_MIN_LOW_ENERGY_REGION_S = 1.0

WINDOW_SECONDS = 20.0

_INTERVAL_CV_WARNING_THRESHOLD = 0.05
_GAP_WARNING_RATIO = 1.8
_LOW_ENERGY_BEAT_FRACTION_WARNING = 0.10

_FINE_HOP_FOR_REFINEMENT = 64
_DEFAULT_REFINE_WINDOW_S = 0.020


# ---------------------------------------------------------------------------
# output structure -- deliberately its own shape, not the Timeline schema.
# Adapting a MusicMap into Timeline beats (Step 7/8) is a later, separate
# step; nothing here is coupled to poc.timeline.schema.Timeline.
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OctaveAlternative(_Strict):
    label: Literal["half_time", "double_time"]
    bpm: float
    strength_ratio: float  # tempogram strength at this bpm, relative to the selected tempo's
    plausible_range: bool  # whether this alternative falls inside OCTAVE_PLAUSIBLE_BPM_RANGE


class OctaveDiagnostics(_Strict):
    """Never resolved automatically (decision 5): `bpm` on MusicMap is set
    from the tracker's own estimate regardless of what is reported here."""

    selected_strength: float | None = None
    alternatives: list[OctaveAlternative] = Field(default_factory=list)
    warning: bool = False


class LowEnergyRegion(_Strict):
    start_s: float
    end_s: float
    mean_db: float


class BeatEnergy(_Strict):
    """Attached to every beat, never used to remove it (decision 6)."""

    local_energy_db: float
    low_energy: bool
    silent: bool


class EnergyDiagnostics(_Strict):
    reference_db: float
    low_threshold_db: float
    silence_threshold_db: float
    low_energy_regions: list[LowEnergyRegion] = Field(default_factory=list)
    per_beat: list[BeatEnergy] = Field(default_factory=list)
    low_energy_beat_fraction: float = 0.0
    warning: bool = False


class WindowSummary(_Strict):
    """Per-region confidence (decision 7), not a single track-wide number."""

    start_s: float
    end_s: float
    beat_count: int
    median_interval_ms: float | None = None
    interval_cv: float | None = None
    mean_energy_db: float | None = None
    low_energy_beat_count: int = 0


class MusicMap(_Strict):
    """Everything the grid/cutter (Step 7) needs to reason about this
    track's timing and confidence, computed once and reusable without
    re-running analysis (it is plain, serializable data)."""

    schema_version: Literal["0.1"] = "0.1"
    analyzer_version: str = "poc-music-v0.1"

    # analysis parameters, kept for reproducibility
    sample_rate: int
    hop_length: int
    start_bpm_prior: float
    tightness: float
    percussive_onset_source: bool
    duration_s: float

    # tempo -- the tracker's own estimate, never adjusted for octave warnings
    bpm: float

    # beat timing: raw is always retained (decision 2), never the only output
    beats_raw_s: list[float]
    beats_corrected_s: list[float]
    offset_ms: float
    offset_source: Literal["calibrated", "override", "none"]

    # refinement: off unless explicitly requested (decision 3)
    refine_applied: bool = False
    beats_used_s: list[float]
    beats_used_frame: list[int]

    # diagnostics: data, never automatic decisions (decisions 5, 6, 7)
    octave: OctaveDiagnostics
    energy: EnergyDiagnostics
    windows: list[WindowSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "MusicMap":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# decoding -- through the bundled FFmpeg, same reasoning as poc.execute.render
# ---------------------------------------------------------------------------


def _decode_to_mono_wav(src: Path, dst: Path, sr: int, ffmpeg_exe: str) -> None:
    cmd = [
        ffmpeg_exe, "-y", "-loglevel", "error",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(sr),
        str(dst),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


# ---------------------------------------------------------------------------
# offset correction (decisions 1, 2, 4)
# ---------------------------------------------------------------------------


def _offset_for_hop(hop_length: int, override_ms: float | None) -> tuple[float, Literal["calibrated", "override", "none"]]:
    if override_ms is not None:
        return override_ms, "override"
    calibrated_lag = CALIBRATED_LAG_MS.get(hop_length)
    if calibrated_lag is None:
        return 0.0, "none"
    return -calibrated_lag, "calibrated"


# ---------------------------------------------------------------------------
# optional refinement (decision 3) -- off by default, never the normal path
# ---------------------------------------------------------------------------


def _refine_to_percussive_transients(beats_s: np.ndarray, y: np.ndarray, sr: int, window_s: float) -> np.ndarray:
    percussive = librosa.effects.percussive(y, margin=3.0)
    envelope = librosa.onset.onset_strength(y=percussive, sr=sr, hop_length=_FINE_HOP_FOR_REFINEMENT)
    times = librosa.frames_to_time(np.arange(len(envelope)), sr=sr, hop_length=_FINE_HOP_FOR_REFINEMENT)
    refined = beats_s.copy()
    for i, beat in enumerate(beats_s):
        lo, hi = np.searchsorted(times, [beat - window_s, beat + window_s])
        if hi - lo >= 3:
            refined[i] = times[lo + int(np.argmax(envelope[lo:hi]))]
    return refined


# ---------------------------------------------------------------------------
# octave diagnostics (decision 5) -- reported, never resolved
# ---------------------------------------------------------------------------


def _octave_diagnostics(
    onset_env: np.ndarray, sr: int, hop_length: int, tempo: float, warn_ratio: float
) -> OctaveDiagnostics:
    tempogram = librosa.feature.tempogram(onset_envelope=onset_env, sr=sr, hop_length=hop_length)
    strength = tempogram.mean(axis=1)
    bpms = librosa.tempo_frequencies(tempogram.shape[0], sr=sr, hop_length=hop_length)
    finite = np.isfinite(bpms)

    def strength_near(bpm: float) -> float | None:
        if not (30.0 <= bpm <= 300.0):
            return None
        mask = finite & (np.abs(bpms - bpm) / bpm <= 0.04)
        return float(strength[mask].max()) if mask.any() else None

    selected_strength = strength_near(tempo)
    alternatives: list[OctaveAlternative] = []
    warning = False
    if selected_strength:
        for label, alt_bpm in (("half_time", tempo / 2.0), ("double_time", tempo * 2.0)):
            alt_strength = strength_near(alt_bpm)
            if alt_strength is None:
                continue
            ratio = alt_strength / selected_strength
            plausible = OCTAVE_PLAUSIBLE_BPM_RANGE[0] <= alt_bpm <= OCTAVE_PLAUSIBLE_BPM_RANGE[1]
            alternatives.append(
                OctaveAlternative(label=label, bpm=alt_bpm, strength_ratio=ratio, plausible_range=plausible)
            )
            if ratio >= warn_ratio and plausible:
                warning = True

    return OctaveDiagnostics(selected_strength=selected_strength, alternatives=alternatives, warning=warning)


# ---------------------------------------------------------------------------
# energy diagnostics (decision 6) -- flags every beat, removes none
# ---------------------------------------------------------------------------


def _energy_diagnostics(
    y: np.ndarray, sr: int, beats_s: np.ndarray, low_energy_rel_db: float, silence_db: float
) -> EnergyDiagnostics:
    rms = librosa.feature.rms(y=y, frame_length=_ENERGY_FRAME_LENGTH, hop_length=_ENERGY_HOP_LENGTH)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=_ENERGY_HOP_LENGTH)
    db = 20 * np.log10(rms + 1e-9)

    audible = db[db > silence_db]
    reference_db = float(np.median(audible)) if len(audible) else float(np.median(db))
    threshold_db = max(reference_db + low_energy_rel_db, silence_db)

    low_mask = db < threshold_db
    regions: list[LowEnergyRegion] = []
    region_start: int | None = None
    for i, flagged in enumerate(np.append(low_mask, False)):
        if flagged and region_start is None:
            region_start = i
        elif not flagged and region_start is not None:
            if times[i - 1] - times[region_start] >= _MIN_LOW_ENERGY_REGION_S:
                regions.append(
                    LowEnergyRegion(
                        start_s=float(times[region_start]),
                        end_s=float(times[i - 1]),
                        mean_db=float(db[region_start:i].mean()),
                    )
                )
            region_start = None

    beat_db = _local_energy_at(beats_s, times, db)
    beat_low = beat_db < threshold_db
    beat_silent = beat_db < silence_db
    per_beat = [
        BeatEnergy(local_energy_db=float(d), low_energy=bool(low), silent=bool(silent))
        for d, low, silent in zip(beat_db, beat_low, beat_silent)
    ]
    fraction_low = float(beat_low.mean()) if len(beats_s) else 0.0

    return EnergyDiagnostics(
        reference_db=reference_db,
        low_threshold_db=threshold_db,
        silence_threshold_db=silence_db,
        low_energy_regions=regions,
        per_beat=per_beat,
        low_energy_beat_fraction=fraction_low,
        warning=fraction_low > _LOW_ENERGY_BEAT_FRACTION_WARNING,
    )


def _local_energy_at(beats_s: np.ndarray, times: np.ndarray, db: np.ndarray, half_window_s: float = 0.1) -> np.ndarray:
    out = np.empty(len(beats_s))
    for k, beat in enumerate(beats_s):
        lo, hi = np.searchsorted(times, [beat - half_window_s, beat + half_window_s])
        out[k] = db[lo:hi].mean() if hi > lo else db[min(lo, len(db) - 1)]
    return out


# ---------------------------------------------------------------------------
# per-window confidence (decision 7)
# ---------------------------------------------------------------------------


def _window_summary(beats_s: np.ndarray, beat_db: np.ndarray, beat_low: np.ndarray, duration_s: float) -> list[WindowSummary]:
    rows: list[WindowSummary] = []
    for start in np.arange(0.0, max(duration_s, WINDOW_SECONDS), WINDOW_SECONDS):
        end = start + WINDOW_SECONDS
        mask = (beats_s >= start) & (beats_s < end)
        count = int(mask.sum())
        if count == 0:
            rows.append(WindowSummary(start_s=float(start), end_s=float(end), beat_count=0))
            continue
        window_beats = beats_s[mask]
        intervals = np.diff(window_beats) if count > 1 else np.array([])
        median_interval_ms = float(np.median(intervals) * 1000.0) if len(intervals) else None
        interval_cv = float(np.std(intervals) / np.median(intervals)) if len(intervals) > 1 and np.median(intervals) > 0 else None
        rows.append(
            WindowSummary(
                start_s=float(start),
                end_s=float(end),
                beat_count=count,
                median_interval_ms=median_interval_ms,
                interval_cv=interval_cv,
                mean_energy_db=float(beat_db[mask].mean()),
                low_energy_beat_count=int(beat_low[mask].sum()),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# grid-wide warnings (mirrors CONCLUSION.md's two interval-stability rules)
# ---------------------------------------------------------------------------


def _grid_stability_warnings(beats_s: np.ndarray) -> list[str]:
    if len(beats_s) < 4:
        return ["fewer than 4 beats detected: grid confidence is low"]
    intervals = np.diff(beats_s)
    median_interval = float(np.median(intervals))
    warnings: list[str] = []
    if median_interval > 0:
        cv = float(np.std(intervals) / median_interval)
        if cv > _INTERVAL_CV_WARNING_THRESHOLD:
            warnings.append(
                f"interval CV {cv:.1%} exceeds {_INTERVAL_CV_WARNING_THRESHOLD:.0%}: "
                "unstable grid (tempo drift or lost beats)"
            )
        max_gap = float(intervals.max())
        if max_gap > _GAP_WARNING_RATIO * median_interval:
            warnings.append(
                f"a gap of {max_gap * 1000:.0f} ms exceeds {_GAP_WARNING_RATIO}x the median interval: "
                "possible dropped beat(s)"
            )
    return warnings


def _to_frames(beats_s: list[float], fps: int) -> list[int]:
    return [ms_to_frame(t * 1000.0, fps=fps) for t in beats_s]


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def _analyze_music_uncached(
    path: str | Path,
    *,
    start_bpm: float = DEFAULT_START_BPM,
    tightness: float = DEFAULT_TIGHTNESS,
    hop_length: int = DEFAULT_HOP_LENGTH,
    percussive_onset_source: bool = False,
    offset_ms: float | None = None,
    refine: bool = False,
    refine_window_s: float = _DEFAULT_REFINE_WINDOW_S,
    octave_warn_ratio: float = DEFAULT_OCTAVE_WARN_RATIO,
    low_energy_rel_db: float = DEFAULT_LOW_ENERGY_REL_DB,
    silence_db: float = DEFAULT_SILENCE_DB,
    ffmpeg_exe: str | None = None,
) -> MusicMap:
    """Analyze one music track and return its MusicMap.

    Decodes ``path`` (any container FFmpeg can read) to mono WAV at
    ``SAMPLE_RATE`` through the bundled FFmpeg binary, then runs the
    validated librosa configuration from
    experiments/beat_flash/CONCLUSION.md: median-aggregated onset strength,
    ``beat_track(..., trim=False)``, the calibrated offset for
    ``hop_length``, and the octave/energy diagnostics -- never an automatic
    octave resolution, never a beat removed for being quiet, and refinement
    left off unless explicitly requested.
    """
    path = Path(path)
    ffmpeg_exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()

    with tempfile.TemporaryDirectory(prefix="poc_music_") as tmp:
        wav_path = Path(tmp) / "audio.wav"
        _decode_to_mono_wav(path, wav_path, SAMPLE_RATE, ffmpeg_exe)
        y, sr = librosa.load(str(wav_path), sr=SAMPLE_RATE, mono=True)

    duration_s = len(y) / sr

    onset_source = librosa.effects.percussive(y) if percussive_onset_source else y
    onset_env = librosa.onset.onset_strength(y=onset_source, sr=sr, hop_length=hop_length, aggregate=np.median)
    tempo, beats_raw = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        start_bpm=start_bpm,
        tightness=tightness,
        units="time",
        trim=False,
    )
    tempo = float(np.atleast_1d(tempo)[0])
    beats_raw = np.asarray(beats_raw, dtype=float)

    applied_offset_ms, offset_source = _offset_for_hop(hop_length, offset_ms)
    beats_corrected = np.clip(beats_raw + applied_offset_ms / 1000.0, 0.0, None)

    beats_used = _refine_to_percussive_transients(beats_corrected, y, sr, refine_window_s) if refine else beats_corrected
    beats_used_list = beats_used.tolist()
    beats_used_frame = _to_frames(beats_used_list, TIMELINE_FPS)

    octave = _octave_diagnostics(onset_env, sr, hop_length, tempo, octave_warn_ratio)
    energy = _energy_diagnostics(y, sr, beats_used, low_energy_rel_db, silence_db)
    beat_db = np.array([b.local_energy_db for b in energy.per_beat])
    beat_low = np.array([b.low_energy for b in energy.per_beat])
    windows = _window_summary(beats_used, beat_db, beat_low, duration_s)

    warnings = _grid_stability_warnings(beats_used)
    for alt in octave.alternatives:
        if alt.strength_ratio >= octave_warn_ratio and alt.plausible_range:
            warnings.append(
                f"{alt.label.replace('_', '-')} alternative {alt.bpm:.1f} BPM has "
                f"{alt.strength_ratio:.0%} of the selected {tempo:.1f} BPM tempogram strength: "
                "octave ambiguity, confirm the felt tempo by tapping"
            )
    if energy.warning:
        warnings.append(
            f"{energy.low_energy_beat_fraction:.1%} of beats fall in low-energy regions: "
            "the tracker emits a grid there but a listener may hear no beat"
        )
    if offset_source == "none":
        warnings.append(
            f"no calibrated offset measurement for hop_length={hop_length}: raw beat timing used uncorrected"
        )

    return MusicMap(
        sample_rate=sr,
        hop_length=hop_length,
        start_bpm_prior=start_bpm,
        tightness=tightness,
        percussive_onset_source=percussive_onset_source,
        duration_s=duration_s,
        bpm=tempo,
        beats_raw_s=beats_raw.tolist(),
        beats_corrected_s=beats_corrected.tolist(),
        offset_ms=applied_offset_ms,
        offset_source=offset_source,
        refine_applied=refine,
        beats_used_s=beats_used_list,
        beats_used_frame=beats_used_frame,
        octave=octave,
        energy=energy,
        windows=windows,
        warnings=warnings,
    )


def analyze_music(
    path: str | Path,
    *,
    start_bpm: float = DEFAULT_START_BPM,
    tightness: float = DEFAULT_TIGHTNESS,
    hop_length: int = DEFAULT_HOP_LENGTH,
    percussive_onset_source: bool = False,
    offset_ms: float | None = None,
    refine: bool = False,
    refine_window_s: float = _DEFAULT_REFINE_WINDOW_S,
    octave_warn_ratio: float = DEFAULT_OCTAVE_WARN_RATIO,
    low_energy_rel_db: float = DEFAULT_LOW_ENERGY_REL_DB,
    silence_db: float = DEFAULT_SILENCE_DB,
    ffmpeg_exe: str | None = None,
    cache_dir: str | Path | None = None,
) -> MusicMap:
    """Analyze music, reusing a content- and parameter-keyed JSON map."""
    path = Path(path)
    ffmpeg_exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()
    parameters = {
        "start_bpm": start_bpm,
        "tightness": tightness,
        "hop_length": hop_length,
        "percussive_onset_source": percussive_onset_source,
        "offset_ms": offset_ms,
        "refine": refine,
        "refine_window_s": refine_window_s,
        "octave_warn_ratio": octave_warn_ratio,
        "low_energy_rel_db": low_energy_rel_db,
        "silence_db": silence_db,
        "ffmpeg_exe": ffmpeg_exe,
    }
    return load_or_compute(
        path,
        namespace="music",
        analyzer_version="poc-music-v0.1",
        parameters=parameters,
        model=MusicMap,
        compute=lambda: _analyze_music_uncached(
            path,
            start_bpm=start_bpm,
            tightness=tightness,
            hop_length=hop_length,
            percussive_onset_source=percussive_onset_source,
            offset_ms=offset_ms,
            refine=refine,
            refine_window_s=refine_window_s,
            octave_warn_ratio=octave_warn_ratio,
            low_energy_rel_db=low_energy_rel_db,
            silence_db=silence_db,
            ffmpeg_exe=ffmpeg_exe,
        ),
        cache_dir=cache_dir,
    )
