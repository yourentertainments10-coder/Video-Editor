#!/usr/bin/env python
"""
Generate a synthetic 4/4 drum track with a known BPM and write its ground-truth
beat times. Used to prove the beat_flash.py pipeline itself is correct before
judging real music by ear.

  python make_synthetic.py --bpm 128 --seconds 40 --intro 2.0 --out fixtures/synth_128.wav

Writes <out>.wav and <out>.truth.json ({"bpm", "beats_s", "downbeats_s"}).
The intro contains a sustained pad only (no beats) so we can see how the
tracker behaves before the first real beat.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def env_exp(n: int, tau_s: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau_s * SR))


def kick(dur=0.18) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 55 + 120 * np.exp(-t * 35)          # pitch sweep down
    ph = 2 * np.pi * np.cumsum(f) / SR
    return (np.sin(ph) * env_exp(n, 0.06) * 1.0).astype(np.float32)


def snare(dur=0.15, rng=None) -> np.ndarray:
    n = int(dur * SR)
    noise = rng.standard_normal(n).astype(np.float32)
    tone = np.sin(2 * np.pi * 190 * np.arange(n) / SR).astype(np.float32)
    return (0.6 * noise * env_exp(n, 0.04) + 0.5 * tone * env_exp(n, 0.05)) * 0.7


def hat(dur=0.05, rng=None) -> np.ndarray:
    n = int(dur * SR)
    noise = rng.standard_normal(n).astype(np.float32)
    # crude high-pass: first difference
    noise = np.diff(noise, prepend=0.0)
    return (noise * env_exp(n, 0.012) * 0.35).astype(np.float32)


def pad(n: int, root_hz=110.0) -> np.ndarray:
    t = np.arange(n) / SR
    chord = sum(np.sin(2 * np.pi * root_hz * r * t + i) for i, r in enumerate((1.0, 1.5, 2.0, 2.52)))
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.2 * t)
    return (chord / 4 * 0.12 * (0.7 + 0.3 * lfo)).astype(np.float32)


def add(buf: np.ndarray, start_s: float, sig: np.ndarray) -> None:
    s = int(round(start_s * SR))
    e = min(len(buf), s + len(sig))
    if s < len(buf):
        buf[s:e] += sig[: e - s]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bpm", type=float, default=128.0)
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--intro", type=float, default=2.0, help="seconds of pad-only intro before the first beat")
    ap.add_argument("--out", type=Path, default=Path("fixtures/synth_128.wav"))
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    n = int(a.seconds * SR)
    buf = pad(n)
    period = 60.0 / a.bpm
    beats, downbeats = [], []
    k = 0
    t = a.intro
    while t < a.seconds - 0.5:
        beats.append(round(t, 5))
        if k % 4 == 0:
            downbeats.append(round(t, 5))
        add(buf, t, kick())
        if k % 4 in (1, 3):
            add(buf, t, snare(rng=rng))
        add(buf, t, hat(rng=rng))
        add(buf, t + period / 2, hat(rng=rng) * 0.6)   # off-beat hats
        k += 1
        t += period

    buf = np.clip(buf / (np.max(np.abs(buf)) + 1e-9) * 0.9, -1, 1)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(a.out), buf, SR)
    truth = {"bpm": a.bpm, "beats_s": beats, "downbeats_s": downbeats}
    a.out.with_suffix(".truth.json").write_text(json.dumps(truth, indent=1))
    print(f"wrote {a.out} ({a.seconds:.0f}s, {a.bpm} BPM, {len(beats)} beats) and {a.out.with_suffix('.truth.json')}")


if __name__ == "__main__":
    main()
