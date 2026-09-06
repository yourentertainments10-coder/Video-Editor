"""Shared factories for poc.decide tests (Step 7).

Not a test module itself (no test_ prefix) -- pytest will not collect it.
Every helper here builds MusicMap/FootageMap objects directly in memory,
with no video/audio decoding, matching the Decide layer's own contract:
it reads perception data, it never touches media.
"""
from __future__ import annotations

from poc.perceive.footage import FootageMap, FootageSample
from poc.perceive.music import BeatEnergy, EnergyDiagnostics, MusicMap, OctaveDiagnostics
from poc.timeline.schema import ms_to_frame


def make_music_map(
    beats_s: list[float],
    beat_energy_db: list[float] | None = None,
    *,
    low_energy: list[bool] | None = None,
    silent: list[bool] | None = None,
    duration_s: float | None = None,
    bpm: float = 120.0,
) -> MusicMap:
    """A MusicMap with a hand-chosen beat grid and per-beat energy, all
    other fields filled with reasonable, fixed bookkeeping values."""
    n = len(beats_s)
    if beat_energy_db is None:
        beat_energy_db = [-15.0] * n
    if low_energy is None:
        low_energy = [False] * n
    if silent is None:
        silent = [False] * n
    per_beat = [
        BeatEnergy(local_energy_db=beat_energy_db[i], low_energy=low_energy[i], silent=silent[i])
        for i in range(n)
    ]
    if duration_s is None:
        duration_s = beats_s[-1] + 2.0 if beats_s else 10.0

    return MusicMap(
        sample_rate=22050,
        hop_length=256,
        start_bpm_prior=120.0,
        tightness=100.0,
        percussive_onset_source=False,
        duration_s=duration_s,
        bpm=bpm,
        beats_raw_s=list(beats_s),
        beats_corrected_s=list(beats_s),
        offset_ms=-18.8,
        offset_source="calibrated",
        beats_used_s=list(beats_s),
        beats_used_frame=[ms_to_frame(b * 1000.0) for b in beats_s],
        octave=OctaveDiagnostics(),
        energy=EnergyDiagnostics(
            reference_db=-15.0, low_threshold_db=-27.0, silence_threshold_db=-50.0, per_beat=per_beat
        ),
    )


def make_footage_map(
    n_samples: int,
    *,
    motion: float | list[float] = 0.3,
    sharpness: float | list[float] = 500.0,
    luma: float | list[float] = 128.0,
    clip_frac: float | list[float] = 0.05,
    duration_ms: int | None = None,
    fps: float = 30.0,
    width: int = 320,
    height: int = 240,
) -> FootageMap:
    """A FootageMap with n_samples at the standard 4 Hz spacing. Each
    signal may be a constant or a per-sample list."""

    def _at(value, i):
        return value[i] if isinstance(value, list) else value

    samples = [
        FootageSample(
            t_s=i / 4.0,
            motion=_at(motion, i),
            sharpness=_at(sharpness, i),
            luma=_at(luma, i),
            clip_frac=_at(clip_frac, i),
        )
        for i in range(n_samples)
    ]
    if duration_ms is None:
        duration_ms = int(round((n_samples / 4.0) * 1000)) + 500  # a little slack past the last sample

    return FootageMap(
        duration_ms=duration_ms,
        fps=fps,
        width=width,
        height=height,
        sample_rate_hz=4.0,
        analysis_width=width,
        samples=samples,
    )
