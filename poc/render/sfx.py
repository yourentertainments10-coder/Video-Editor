"""Sound effects made on this computer (no downloads, no licences): whooshes for transitions,
an impact for the drop, a riser before it.

Each sound is synthesised deterministically with numpy (seeded noise, filters, envelopes) and
written once as a 48 kHz stereo WAV; the same version always gives the same bytes.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 48000
VERSION = "v1"

# name -> (length in ms, where the "hit" is, in ms from the start: the moment to line up with a cut)
SOUNDS: dict[str, tuple[int, int]] = {
    "whoosh": (520, 300),
    "whoosh_soft": (900, 520),
    "impact": (1100, 0),
    "riser": (1600, 1600),
}


def _one_pole_lowpass(signal: np.ndarray, cutoff_hz: np.ndarray) -> np.ndarray:
    """Low-pass with a cutoff that can change every sample (for sweeps)."""
    alpha = 1.0 - np.exp(-2.0 * np.pi * np.clip(cutoff_hz, 20.0, RATE / 2.2) / RATE)
    out = np.empty_like(signal)
    acc = 0.0
    for i, (x, a) in enumerate(zip(signal, alpha)):
        acc += a * (x - acc)
        out[i] = acc
    return out


def _band(signal: np.ndarray, low_hz: np.ndarray, high_hz: np.ndarray) -> np.ndarray:
    return _one_pole_lowpass(signal, high_hz) - _one_pole_lowpass(signal, low_hz)


def _envelope(n: int, peak_at: float, attack_pow: float = 2.0, release_pow: float = 2.0) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n, endpoint=False)
    rise = np.clip(t / max(peak_at, 1e-6), 0, 1) ** attack_pow
    fall = np.clip((1 - t) / max(1 - peak_at, 1e-6), 0, 1) ** release_pow
    return np.where(t < peak_at, rise, fall)


def synthesise(name: str) -> np.ndarray:
    """Stereo float samples (n, 2) in -1..1."""
    length_ms, hit_ms = SOUNDS[name]
    n = int(RATE * length_ms / 1000)
    t = np.arange(n) / RATE
    rng = np.random.default_rng({"whoosh": 11, "whoosh_soft": 12, "impact": 13, "riser": 14}[name])
    noise = rng.standard_normal(n)
    if name in ("whoosh", "whoosh_soft"):
        soft = name == "whoosh_soft"
        peak = hit_ms / length_ms
        sweep = np.where(t / t[-1] < peak, (t / t[-1]) / peak, 1 - (t / t[-1] - peak) / (1 - peak))
        centre = (300 if soft else 450) + (1800 if soft else 3800) * sweep ** 1.5
        mono = _band(noise, centre * 0.45, centre * 1.6) * _envelope(n, peak, 2.2, 1.6)
        pan = 0.6 * (t / t[-1] - 0.5) * 2                              # passes left to right
        left, right = mono * (1 - pan) / 2 + mono / 2, mono * (1 + pan) / 2 + mono / 2
        stereo = np.stack([left, right], axis=1)
    elif name == "impact":
        pitch = 34 + 46 * np.exp(-t * 18)                               # low thump falling in pitch
        phase = 2 * np.pi * np.cumsum(pitch) / RATE
        body = np.sin(phase) * np.exp(-t * 4.2)
        click = _band(noise, np.full(n, 900.0), np.full(n, 6000.0)) * np.exp(-t * 60) * 0.6
        tail = _one_pole_lowpass(noise, np.full(n, 400.0)) * np.exp(-t * 3.0) * 0.5
        mono = body + click + tail
        stereo = np.stack([mono, mono], axis=1)
    elif name == "riser":
        progress = t / t[-1]
        tone = np.sin(2 * np.pi * np.cumsum(180 + 1600 * progress ** 2) / RATE) * 0.35
        hiss = _band(noise, 600 + 3000 * progress, 1500 + 9000 * progress)
        mono = (tone + hiss) * progress ** 2.4
        mono[-int(RATE * 0.01):] *= np.linspace(1, 0, int(RATE * 0.01))  # no click at the end
        stereo = np.stack([mono, mono], axis=1)
    else:
        raise KeyError(name)
    peak = float(np.max(np.abs(stereo))) or 1.0
    return stereo / peak * 0.89                                          # about -1 dBFS


def write_wav(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    partial = path.with_suffix(".partial.wav")
    with wave.open(str(partial), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data.tobytes())
    partial.replace(path)
    return path


def sound_file(folder: Path, name: str) -> Path:
    """The WAV for ``name`` in ``folder`` (made the first time it is needed)."""
    path = Path(folder) / f"sfx-{name}-{VERSION}.wav"
    if not path.is_file():
        write_wav(path, synthesise(name))
    return path
