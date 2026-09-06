#!/usr/bin/env python
"""
Beat Flash Test (iteration 2)
=============================
Detect beats in a music track with librosa and render a video that flashes on
every detected beat, so alignment can be judged by eye and ear.

Isolated experiment. No dependency on the main project.

Usage
-----
  python beat_flash.py track1.mp3 [track2.wav ...] [options]

Options
-------
  --out DIR              output directory (default: results)
  --fps N                video frame rate (default 60; flash quantization <= 8.3 ms)
  --size WxH             video size (default 640x360)
  --start-bpm N          tempo prior for librosa (default 120)
  --tightness N          librosa beat tracker tightness (default 100)
  --percussive           track beats on the HPSS percussive component only
  --hop N                analysis hop in samples at 22050 Hz (default 256 = 11.6 ms)
  --offset-ms X          constant correction ADDED to every raw beat time, in ms.
                         Default: minus the calibrated tracker lag for the chosen hop
                         (see CALIBRATED_LAG_MS). Pass 0 to disable.
  --refine               OFF by default. When given, snap each corrected beat to the
                         strongest PERCUSSIVE transient within +/- --refine-window-ms.
  --refine-window-ms X   refinement search window (default 20 ms)
  --octave-warn-ratio R  warn when a half/double-time tempo has >= R of the selected
                         tempo's tempogram strength (default 0.75)
  --low-energy-db X      a region is "low energy" when its RMS is more than |X| dB
                         below the track's median loud level (default -12)
  --silence-db X         absolute silence threshold in dBFS (default -50)
  --no-click             do not produce the click-overlay video
  --no-video             metrics and diagnostics only
  --truth FILE           JSON with ground-truth "beats_s" (synthetic tracks) for an
                         objective F-measure and offset report (raw, corrected, refined)

Outputs per track (in --out)
----------------------------
  <name>_beats.json          settings, bpm, raw/corrected/refined beat lists, metrics,
                             octave and energy diagnostics, per-window summary, warnings
  <name>_diagnostics.csv     one beat per row: raw, corrected, refined, used, intervals,
                             local energy, low-energy / silence flags, bar heuristic
  <name>_diagnostics.txt     human-readable summary and 20-second window table
  <name>_flash.mp4           flash video with the original music (uses the "used" beats)
  <name>_flash_click.mp4     same video with a click on every used beat mixed in
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import librosa
import imageio_ffmpeg

SR = 22050
FINE_HOP = 64          # ~2.9 ms envelope used only when --refine is given
TOL_S = 0.070          # standard beat-tracking tolerance window (70 ms)
ENERGY_FRAME = 2048    # RMS analysis frame (93 ms)
ENERGY_HOP = 512       # RMS hop (23 ms)
WINDOW_S = 20.0        # per-window summary length

# Calibrated tracker lag, in ms, by analysis hop (samples at 22050 Hz).
#
# Measured on synthetic 4/4 drum tracks with known beat times (make_synthetic.py,
# 128 BPM and 92 BPM), raw librosa.beat.beat_track output, no refinement:
#   hop 512: raw beats a median +23.2 ms late (exactly one hop = 23.2 ms)
#   hop 256: raw beats a median +18.8 ms late
# Two real tracks (Chor, BYE) analysed at hop 256 showed the same lag relative
# to percussive onset peaks: median +17.4 ms and +20.3 ms late.
#
# Cause: librosa.onset.onset_strength is a spectral-flux difference between
# consecutive centered STFT frames (n_fft 2048), so its peak lands about one
# frame after the physical transient; beat_track inherits that lag and
# quantizes it to the hop. The correction below subtracts the measured lag.
# Hops without a measurement get no correction and a warning.
#   hop 128: +16.0 ms late (single measurement, synth_128 only; treat as provisional)
CALIBRATED_LAG_MS = {512: 23.2, 256: 18.8, 128: 16.0}

# Half/double-time alternatives outside this BPM range are still reported in the
# diagnostics but do not raise a warning (a 240 BPM "double time" of a 120 BPM
# song is a tempogram harmonic, not a plausible felt tempo).
OCTAVE_PLAUSIBLE_BPM = (50.0, 200.0)


# ----------------------------------------------------------------------------- audio io
def ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode_to_wav(src: Path, dst: Path, sr: int = SR) -> None:
    """Decode any container to mono WAV at the analysis sample rate."""
    cmd = [ffmpeg(), "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-ac", "1", "-ar", str(sr), str(dst)]
    subprocess.run(cmd, check=True)


# ----------------------------------------------------------------------------- detection
def detect_beats(y: np.ndarray, sr: int, start_bpm: float, tightness: float,
                 percussive: bool, hop: int):
    """Raw librosa beat tracking. Returns tempo, raw beat times, onset times, onset envelope."""
    y_track = librosa.effects.percussive(y) if percussive else y
    onset_env = librosa.onset.onset_strength(y=y_track, sr=sr, hop_length=hop,
                                             aggregate=np.median)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr,
                                           hop_length=hop, start_bpm=start_bpm,
                                           tightness=tightness, units="time",
                                           trim=False)
    tempo = float(np.atleast_1d(tempo)[0])
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr,
                                        hop_length=hop, units="time")
    return tempo, np.asarray(beats, dtype=float), np.asarray(onsets, dtype=float), onset_env


def refine_to_percussive(beats: np.ndarray, y: np.ndarray, sr: int, window_s: float) -> np.ndarray:
    """
    Optional (--refine). Snap each beat to the strongest PERCUSSIVE transient
    within +/- window_s, using a fine onset envelope of the HPSS percussive
    component. Narrow window and percussive-only source are deliberate: the
    previous +/-45 ms full-mix version snapped to vocal and guitar transients
    and increased jitter on real music.
    """
    yp = librosa.effects.percussive(y, margin=3.0)
    env = librosa.onset.onset_strength(y=yp, sr=sr, hop_length=FINE_HOP)
    times = librosa.frames_to_time(np.arange(len(env)), sr=sr, hop_length=FINE_HOP)
    out = beats.copy()
    for i, b in enumerate(beats):
        lo, hi = np.searchsorted(times, [b - window_s, b + window_s])
        if hi - lo >= 3:
            out[i] = times[lo + int(np.argmax(env[lo:hi]))]
    return out


def bar_phase(beats: np.ndarray, onset_env: np.ndarray, sr: int, hop: int) -> int:
    """
    Unchanged crude 4/4 heuristic (strongest every-fourth beat). Informational
    only; it was observed to pick the wrong phase. Not downbeat detection.
    """
    if len(beats) < 8:
        return 0
    frames = librosa.time_to_frames(beats, sr=sr, hop_length=hop)
    frames = np.clip(frames, 0, len(onset_env) - 1)
    strengths = onset_env[frames]
    scores = [strengths[p::4].mean() for p in range(4)]
    return int(np.argmax(scores))


# ----------------------------------------------------------------------------- diagnostics
def nearest_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """For each value in a, distance to the nearest value in b."""
    if len(b) == 0:
        return np.full(len(a), np.inf)
    idx = np.searchsorted(b, a)
    left = np.abs(a - b[np.clip(idx - 1, 0, len(b) - 1)])
    right = np.abs(b[np.clip(idx, 0, len(b) - 1)] - a)
    return np.minimum(left, right)


def compute_metrics(beats: np.ndarray, onsets: np.ndarray, tempo: float, duration: float) -> dict:
    m: dict = {"bpm_est": round(tempo, 2), "n_beats": int(len(beats)),
               "duration_s": round(duration, 2), "warnings": []}
    if len(beats) < 4:
        m["warnings"].append("fewer than 4 beats detected")
        return m
    iv = np.diff(beats)
    med = float(np.median(iv))
    m.update({
        "bpm_from_intervals": round(60.0 / med, 2),
        "interval_median_ms": round(1000 * med, 1),
        "interval_std_ms": round(1000 * float(np.std(iv)), 1),
        "interval_cv": round(float(np.std(iv) / med), 4),
        "max_gap_ms": round(1000 * float(iv.max()), 1),
        "min_gap_ms": round(1000 * float(iv.min()), 1),
        "first_beat_s": round(float(beats[0]), 3),
        "last_beat_s": round(float(beats[-1]), 3),
    })
    d = nearest_distance(beats, onsets)
    finite = d[np.isfinite(d)]
    if len(finite):
        m["beats_within_50ms_of_an_onset_pct"] = round(100 * float(np.mean(finite < 0.05)), 1)
        m["median_beat_to_onset_ms"] = round(1000 * float(np.median(finite)), 1)
    if m["interval_cv"] > 0.05:
        m["warnings"].append("interval CV > 5%: unstable grid (tempo drift or lost beats)")
    if m["max_gap_ms"] > 1.8 * m["interval_median_ms"]:
        m["warnings"].append("a gap > 1.8x median interval: dropped beat(s)")
    return m


def octave_diagnostics(onset_env: np.ndarray, sr: int, hop: int, tempo: float,
                       warn_ratio: float) -> dict:
    """
    Compare the selected tempo's tempogram strength against half-time and
    double-time alternatives. Warns when they are close. Never changes the tempo.
    """
    tg = librosa.feature.tempogram(onset_envelope=onset_env, sr=sr, hop_length=hop)
    strength = tg.mean(axis=1)
    bpms = librosa.tempo_frequencies(tg.shape[0], sr=sr, hop_length=hop)
    finite = np.isfinite(bpms)

    def strength_near(b: float):
        if not (30.0 <= b <= 300.0):
            return None
        m = finite & (np.abs(bpms - b) / b <= 0.04)
        return float(strength[m].max()) if m.any() else None

    sel = strength_near(tempo)
    out = {"selected_bpm": round(tempo, 2), "selected_strength": None if sel is None else round(sel, 4),
           "alternatives": {}, "warnings": []}
    for label, alt in (("half_time", tempo / 2), ("double_time", tempo * 2)):
        s = strength_near(alt)
        if s is None or not sel:
            continue
        ratio = s / sel
        plausible = OCTAVE_PLAUSIBLE_BPM[0] <= alt <= OCTAVE_PLAUSIBLE_BPM[1]
        out["alternatives"][label] = {"bpm": round(alt, 2), "strength_ratio_vs_selected": round(ratio, 3),
                                      "plausible_range": plausible}
        if ratio >= warn_ratio and plausible:
            out["warnings"].append(
                f"{label.replace('_', '-')} alternative {alt:.1f} BPM has {ratio:.0%} of the selected "
                f"{tempo:.1f} BPM strength: octave ambiguity, confirm the felt tempo by tapping")
    rng = finite & (bpms >= 40) & (bpms <= 240)
    order = np.argsort(strength[rng])[::-1]
    peaks, seen = [], []
    for i in order:
        b = float(bpms[rng][i])
        if all(abs(b - s) / s > 0.03 for s in seen):
            peaks.append(round(b, 1)); seen.append(b)
        if len(peaks) == 5:
            break
    out["top_tempogram_peaks_bpm"] = peaks
    return out


def energy_diagnostics(y: np.ndarray, sr: int, beats: np.ndarray, low_rel_db: float,
                       silence_db: float):
    """
    Frame RMS in dBFS. Reference = median of frames above the silence floor.
    Low-energy threshold = reference + low_rel_db (low_rel_db is negative).
    Returns summary dict plus per-beat local energy and flags. Diagnostic only.
    """
    rms = librosa.feature.rms(y=y, frame_length=ENERGY_FRAME, hop_length=ENERGY_HOP)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=ENERGY_HOP)
    db = 20 * np.log10(rms + 1e-9)
    audible = db[db > silence_db]
    ref = float(np.median(audible)) if len(audible) else float(np.median(db))
    thr = max(ref + low_rel_db, silence_db)

    low = db < thr
    regions = []
    start = None
    for i, flag in enumerate(np.append(low, False)):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if t[i - 1] - t[start] >= 1.0:
                regions.append({"start_s": round(float(t[start]), 2), "end_s": round(float(t[i - 1]), 2),
                                "mean_db": round(float(db[start:i].mean()), 1)})
            start = None

    beat_db = np.empty(len(beats))
    for k, b in enumerate(beats):
        lo, hi = np.searchsorted(t, [b - 0.1, b + 0.1])
        beat_db[k] = db[lo:hi].mean() if hi > lo else db[min(lo, len(db) - 1)]
    beat_low = beat_db < thr
    beat_silent = beat_db < silence_db
    summary = {"reference_db": round(ref, 1), "low_threshold_db": round(thr, 1),
               "silence_threshold_db": silence_db,
               "low_energy_regions": regions,
               "beats_in_low_energy": int(beat_low.sum()), "beats_in_silence": int(beat_silent.sum()),
               "pct_beats_low_energy": round(100 * float(beat_low.mean()), 1) if len(beats) else 0.0,
               "warnings": []}
    if summary["pct_beats_low_energy"] > 10:
        summary["warnings"].append(
            f"{summary['pct_beats_low_energy']}% of beats fall in low-energy regions: "
            f"the tracker emits a grid there but a listener may hear no beat")
    return summary, beat_db, beat_low, beat_silent


def window_summary(beats: np.ndarray, beat_db: np.ndarray, beat_low: np.ndarray, duration: float) -> list:
    rows = []
    for s0 in np.arange(0.0, duration, WINDOW_S):
        m = (beats >= s0) & (beats < s0 + WINDOW_S)
        if m.sum() == 0:
            rows.append({"start_s": int(s0), "n_beats": 0})
            continue
        iv = np.diff(beats[m]) if m.sum() > 1 else np.array([])
        rows.append({"start_s": int(s0), "n_beats": int(m.sum()),
                     "median_interval_ms": round(1000 * float(np.median(iv)), 1) if len(iv) else None,
                     "interval_cv": round(float(np.std(iv) / np.median(iv)), 3) if len(iv) > 1 else None,
                     "mean_energy_db": round(float(beat_db[m].mean()), 1),
                     "low_energy_beats": int(beat_low[m].sum())})
    return rows


def compare_to_truth(beats: np.ndarray, truth: np.ndarray) -> dict:
    """F-measure at 70 ms and signed offset, restricted to the span where truth beats exist."""
    if len(truth) == 0 or len(beats) == 0:
        return {}
    period = float(np.median(np.diff(truth))) if len(truth) > 1 else 0.5
    lo, hi = truth[0] - period / 2, truth[-1] + period / 2
    det = beats[(beats >= lo) & (beats <= hi)]
    d_det = nearest_distance(det, truth)
    d_tru = nearest_distance(truth, det)
    tp = int(np.sum(d_det <= TOL_S))
    precision = tp / len(det) if len(det) else 0.0
    recall = float(np.mean(d_tru <= TOL_S))
    f = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    idx = np.searchsorted(truth, det)
    cand = np.stack([truth[np.clip(idx - 1, 0, len(truth) - 1)],
                     truth[np.clip(idx, 0, len(truth) - 1)]], axis=1)
    nearest = cand[np.arange(len(det)), np.argmin(np.abs(cand - det[:, None]), axis=1)]
    signed = (det - nearest)[np.abs(det - nearest) <= TOL_S]
    out = {"f_measure_70ms": round(f, 3), "precision": round(precision, 3), "recall": round(recall, 3),
           "n_truth": int(len(truth)), "n_detected_in_span": int(len(det))}
    if len(signed):
        out["median_signed_offset_ms"] = round(1000 * float(np.median(signed)), 1)
        out["offset_p90_abs_ms"] = round(1000 * float(np.percentile(np.abs(signed), 90)), 1)
    out["recall_vs_half_time"] = round(float(np.mean(nearest_distance(truth[::2], det) <= TOL_S)), 3)
    return out


# ----------------------------------------------------------------------------- click overlay
def click_track(beats: np.ndarray, is_bar: np.ndarray, n_samples: int, sr: int) -> np.ndarray:
    out = np.zeros(n_samples, dtype=np.float32)
    n = int(0.030 * sr)
    tt = np.arange(n) / sr
    env = np.exp(-tt * 150.0)
    tone_beat = (np.sin(2 * np.pi * 1000 * tt) * env * 0.7).astype(np.float32)
    tone_bar = (np.sin(2 * np.pi * 1600 * tt) * env * 1.0).astype(np.float32)
    for t, bar in zip(beats, is_bar):
        s = int(round(t * sr))
        if s < 0 or s + n > n_samples:
            continue
        out[s:s + n] += tone_bar if bar else tone_beat
    return out


def write_click_mix(y: np.ndarray, sr: int, beats: np.ndarray, is_bar: np.ndarray, dst: Path) -> None:
    music = y / (np.max(np.abs(y)) + 1e-9) * 0.55
    clicks = click_track(beats, is_bar, len(y), sr) * 0.9
    sf.write(str(dst), np.clip(music + clicks, -1.0, 1.0), sr)


# ----------------------------------------------------------------------------- video
def render_flash_video(beats: np.ndarray, is_bar: np.ndarray, phase: int, audio_wav: Path,
                       out_mp4: Path, duration: float, fps: int, w: int, h: int) -> None:
    n_frames = int(np.ceil(duration * fps))
    beat_frames = np.round(beats * fps).astype(int)

    decay = max(2, int(round(0.12 * fps)))
    kernel = np.exp(-np.arange(decay) / (0.035 * fps))
    flash = np.zeros(n_frames)
    barflash = np.zeros(n_frames)
    for bf, bar in zip(beat_frames, is_bar):
        if bf < 0 or bf >= n_frames:
            continue
        end = min(n_frames, bf + decay)
        tgt = barflash if bar else flash
        tgt[bf:end] = np.maximum(tgt[bf:end], kernel[:end - bf])

    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w // 2, h // 2
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    base_r = min(w, h) * 0.12
    bg = np.full((h, w, 3), 16, dtype=np.uint8)
    box_w, box_h, gap = w // 10, h // 12, w // 40
    box_x0 = (w - (4 * box_w + 3 * gap)) // 2
    boxes = [(box_x0 + i * (box_w + gap), h // 16) for i in range(4)]

    cmd = [ffmpeg(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
           "-i", str(audio_wav),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out_mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None

    white = np.array([255, 255, 255], dtype=np.float32)
    orange = np.array([255, 140, 0], dtype=np.float32)
    template = bg.copy()
    template[dist <= base_r] = 40
    template[dist <= base_r * 0.25] = 200
    bar_h = h // 30
    try:
        for i in range(n_frames):
            t = i / fps
            a_beat, a_bar = flash[i], barflash[i]
            a = max(a_beat, a_bar)
            if a > 0.02:
                col = orange if a_bar >= a_beat else white
                frame = (bg.astype(np.float32) * (1 - a * 0.85) + col * (a * 0.85))
                r = base_r * (1.0 + 0.6 * a)
                frame[dist <= r] = np.minimum(frame[dist <= r] + 40, 255)
                frame = frame.astype(np.uint8)
            else:
                frame = template.copy()
            k = int(np.searchsorted(beats, t, side="right")) - 1
            pos = (k - phase) % 4 if k >= 0 else -1
            for bi, (bx, by) in enumerate(boxes):
                frame[by:by + box_h, bx:bx + box_w] = (230, 160, 40) if bi == pos else (50, 50, 50)
            px = int(w * min(1.0, t / duration))
            frame[h - bar_h:h, :px] = (90, 160, 255)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed rendering {out_mp4}")


# ----------------------------------------------------------------------------- main
def resolve_offset(args) -> tuple[float, str, list]:
    warnings = []
    if args.offset_ms is not None:
        return float(args.offset_ms), "user", warnings
    lag = CALIBRATED_LAG_MS.get(args.hop)
    if lag is None:
        warnings.append(f"no calibrated lag for hop {args.hop}; no offset applied (pass --offset-ms)")
        return 0.0, "none", warnings
    return -lag, f"calibrated (hop {args.hop}: raw beats {lag} ms late on synthetic ground truth)", warnings


def process(track: Path, out_dir: Path, args) -> dict:
    name = track.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        decode_to_wav(track, wav)
        y, sr = sf.read(str(wav), dtype="float32", always_2d=False)
        duration = len(y) / sr

        tempo, raw, onsets, onset_env = detect_beats(y, sr, args.start_bpm, args.tightness,
                                                     args.percussive, args.hop)
        offset_ms, offset_source, warnings = resolve_offset(args)
        corrected = raw + offset_ms / 1000.0
        refined = refine_to_percussive(corrected, y, sr, args.refine_window_ms / 1000.0) if args.refine else None
        used = refined if refined is not None else corrected
        used_label = "refined" if refined is not None else "corrected"

        phase = bar_phase(used, onset_env, sr, args.hop)
        is_bar = np.zeros(len(used), dtype=bool)
        is_bar[phase::4] = True

        metrics = compute_metrics(used, onsets, tempo, duration)
        octave = octave_diagnostics(onset_env, sr, args.hop, tempo, args.octave_warn_ratio)
        energy, beat_db, beat_low, beat_silent = energy_diagnostics(y, sr, used, args.low_energy_db, args.silence_db)
        windows = window_summary(used, beat_db, beat_low, duration)
        warnings += metrics["warnings"] + octave["warnings"] + energy["warnings"]

        report = {
            "track": str(track),
            "settings": {"start_bpm": args.start_bpm, "tightness": args.tightness, "percussive": args.percussive,
                         "sr": sr, "hop": args.hop, "offset_ms": round(offset_ms, 2), "offset_source": offset_source,
                         "refine": bool(args.refine), "refine_window_ms": args.refine_window_ms if args.refine else None},
            "bpm": round(tempo, 2),
            "beats_used": used_label,
            "beats_raw_s": [round(float(b), 4) for b in raw],
            "beats_corrected_s": [round(float(b), 4) for b in corrected],
            "beats_refined_s": None if refined is None else [round(float(b), 4) for b in refined],
            "bar_phase_heuristic": phase,
            "metrics": metrics,
            "octave": octave,
            "energy": energy,
            "windows": windows,
            "warnings": warnings,
        }
        if args.truth:
            truth = np.asarray(json.loads(Path(args.truth).read_text())["beats_s"], dtype=float)
            report["vs_truth"] = {"raw": compare_to_truth(raw, truth), "corrected": compare_to_truth(corrected, truth)}
            if refined is not None:
                report["vs_truth"]["refined"] = compare_to_truth(refined, truth)

        (out_dir / f"{name}_beats.json").write_text(json.dumps(report, indent=2))

        with (out_dir / f"{name}_diagnostics.csv").open("w", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["idx", "raw_s", "corrected_s", "refined_s", "used_s", "interval_raw_ms",
                         "interval_used_ms", "local_energy_db", "low_energy", "silent", "beat_in_bar_heuristic"])
            for k in range(len(raw)):
                wr.writerow([k, f"{raw[k]:.4f}", f"{corrected[k]:.4f}",
                             "" if refined is None else f"{refined[k]:.4f}", f"{used[k]:.4f}",
                             "" if k == 0 else f"{1000 * (raw[k] - raw[k - 1]):.1f}",
                             "" if k == 0 else f"{1000 * (used[k] - used[k - 1]):.1f}",
                             f"{beat_db[k]:.1f}", int(beat_low[k]), int(beat_silent[k]), ((k - phase) % 4) + 1])

        lines = [f"Track: {track.name}", f"Duration: {duration:.1f} s   Tempo: {tempo:.2f} BPM   Beats: {len(raw)}",
                 f"Offset applied: {offset_ms:+.1f} ms ({offset_source})",
                 f"Beats used for video/clicks: {used_label}",
                 f"Interval median {metrics.get('interval_median_ms')} ms, CV {metrics.get('interval_cv')}, "
                 f"max gap {metrics.get('max_gap_ms')} ms",
                 f"Octave: selected {tempo:.1f}; alternatives " + json.dumps(octave["alternatives"]) +
                 f"; top tempogram peaks {octave['top_tempogram_peaks_bpm']}",
                 f"Energy: reference {energy['reference_db']} dB, low threshold {energy['low_threshold_db']} dB, "
                 f"{energy['beats_in_low_energy']} beats low ({energy['pct_beats_low_energy']}%), "
                 f"{energy['beats_in_silence']} in silence",
                 "Low-energy regions: " + (", ".join(f"{r['start_s']}-{r['end_s']}s ({r['mean_db']} dB)"
                                                      for r in energy["low_energy_regions"]) or "none"),
                 "", "Warnings:"] + [f"  - {w_}" for w_ in warnings] + ["  (none)"] * (not warnings) + [
                 "", f"{'start':>6} {'beats':>5} {'med_iv_ms':>9} {'cv':>6} {'energy_dB':>9} {'low':>4}"]
        for r in windows:
            lines.append(f"{r['start_s']:>6} {r['n_beats']:>5} {str(r.get('median_interval_ms', '')):>9} "
                         f"{str(r.get('interval_cv', '')):>6} {str(r.get('mean_energy_db', '')):>9} "
                         f"{str(r.get('low_energy_beats', '')):>4}")
        if "vs_truth" in report:
            lines += ["", "vs truth: " + json.dumps(report["vs_truth"])]
        (out_dir / f"{name}_diagnostics.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        if args.no_video:
            return report
        w, h = (int(v) for v in args.size.lower().split("x"))
        render_flash_video(used, is_bar, phase, wav, out_dir / f"{name}_flash.mp4", duration, args.fps, w, h)
        if not args.no_click:
            mix = Path(td) / "mix.wav"
            write_click_mix(y, sr, used, is_bar, mix)
            render_flash_video(used, is_bar, phase, mix, out_dir / f"{name}_flash_click.mp4", duration, args.fps, w, h)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tracks", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=Path("results"))
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--size", default="640x360")
    p.add_argument("--start-bpm", type=float, default=120.0)
    p.add_argument("--tightness", type=float, default=100.0)
    p.add_argument("--percussive", action="store_true")
    p.add_argument("--hop", type=int, default=256)
    p.add_argument("--offset-ms", type=float, default=None)
    p.add_argument("--refine", action="store_true")
    p.add_argument("--refine-window-ms", type=float, default=20.0)
    p.add_argument("--octave-warn-ratio", type=float, default=0.75)
    p.add_argument("--low-energy-db", type=float, default=-12.0)
    p.add_argument("--silence-db", type=float, default=-50.0)
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--no-click", action="store_true")
    p.add_argument("--truth", type=Path, default=None)
    args = p.parse_args(argv)

    for track in args.tracks:
        if not track.exists():
            print(f"missing: {track}", file=sys.stderr)
            continue
        print(f"== {track.name}")
        rep = process(track, args.out, args)
        m, s = rep["metrics"], rep["settings"]
        print(f"   bpm {rep['bpm']:.1f} | beats {m['n_beats']} | interval {m.get('interval_median_ms')} ms "
              f"(cv {m.get('interval_cv')}) | offset {s['offset_ms']:+.1f} ms | using {rep['beats_used']} beats")
        print(f"   octave alternatives: {rep['octave']['alternatives']}")
        print(f"   energy: {rep['energy']['beats_in_low_energy']} beats low-energy "
              f"({rep['energy']['pct_beats_low_energy']}%), {rep['energy']['beats_in_silence']} in silence")
        for wmsg in rep["warnings"]:
            print(f"   WARNING: {wmsg}")
        if "vs_truth" in rep:
            for label, v in rep["vs_truth"].items():
                print(f"   vs truth [{label:9s}] F={v['f_measure_70ms']} offset {v.get('median_signed_offset_ms')} ms "
                      f"(p90 {v.get('offset_p90_abs_ms')} ms)")
        print(f"   -> {args.out / (track.stem + '_diagnostics.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
