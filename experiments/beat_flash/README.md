# Beat Flash Test

Purpose: decide whether librosa's beat tracker is accurate enough to drive the
music cut grid described in the architecture document, before any MVP code is
written. This folder is a self-contained experiment. It shares nothing with the
main project and can be deleted afterwards.

Status: **CLOSED (2026-09-06).** Two iterations completed. The formal
conclusion, known limitations, MVP decisions, out-of-scope list, and the
Phase 02 handoff are in `CONCLUSION.md`. No further algorithm changes are
planned here. The listening verdicts belong on `evaluation_sheet.md`.

---

## What changed in iteration 2 (and why)

Driven strictly by the findings of the first real-track run.

| Change | Before | After |
|--------|--------|-------|
| Refinement | ON by default: snap each beat to the nearest transient in the full mix within plus or minus 45 ms. Raised jitter on real music (snapped to vocals and guitar). | **OFF by default.** `--refine` enables a narrow (20 ms) snap to the **percussive** component only. Raw timestamps are always kept in the output. |
| Timing offset | None. Raw beats were a measured 17 to 23 ms late. | **Calibrated constant correction**, `--offset-ms`, default minus the measured lag for the chosen hop (18.8 ms at hop 256). See "Offset calibration". |
| Octave diagnostics | A 70 to 180 BPM range check that missed BYE at 166.7 BPM. | Tempogram strength of half-time and double-time alternatives is compared with the selected tempo. **Warning** when an alternative in the plausible 50 to 200 BPM range has 75% or more of the selected strength. The tempo is never changed automatically. |
| Energy diagnostics | None. Beats were emitted through silence unnoticed. | Frame RMS in dBFS; low-energy regions (12 dB below the track's median loud level, or under minus 50 dBFS) are listed; each beat is flagged `low_energy` or `silent`. **Beats are not removed.** |
| Diagnostic output | Beat list and a few metrics. | Per-beat CSV with raw, corrected, refined, used timestamps, both interval series, local energy, flags; a text summary with a 20-second window table; JSON with everything. |

Not changed, deliberately: no new models, no allin1, no downbeat detection, no
music structure analysis. The crude 4/4 "bar" heuristic from iteration 1 is
still present, unchanged and still unverified; it only drives the orange
flashes and the 1-2-3-4 boxes, and it is not scored.

---

## 1. Test procedure

1. **Pipeline self-check (done).** `make_synthetic.py` generates a drum track
   at a known BPM plus a ground-truth beat list. `beat_flash.py --truth` scores
   raw, corrected, and (if enabled) refined beats objectively. Results in
   `results/synth/`.
2. **Pick three real tracks** (Section 2), copy them to `fixtures/real/`.
   Two are already there and processed (Chor, BYE). The two short clips in that
   folder (5 s and 12 s) are too short to evaluate and do not count.
3. **Run the detector and renderer:**

   ```bash
   .venv/Scripts/python.exe beat_flash.py fixtures/real/*.mp3 --out results/real
   ```

4. **Read the diagnostics first**: `results/real/<name>_diagnostics.txt`. Note
   the octave warning (if any), the low-energy regions, and the window table.
   These tell you where to listen.
5. **Listen with the click version** (`_flash_click.mp4`) through **wired**
   headphones or speakers. Bluetooth adds 100 to 250 ms of audio latency and
   will make every flash look early. Play the whole track once. Then replay
   the intro, one quiet section, the drop or chorus, and the ending.
6. **Watch the no-click version** (`_flash.mp4`) once and judge whether the
   flashes alone feel on the beat.
7. **Tap the tempo** of each track with a metronome app or by counting for
   30 seconds. Write it on the sheet. This settles octave warnings.
8. **Fill in `evaluation_sheet.md`** and apply Section 7.
9. If a track fails, try the tuning flags in Section 8 and record what you tried.

---

## 2. Required input files

Three full-length tracks (2 to 4 minutes), MP3, WAV, M4A, or FLAC:

| Track | What it tests | Suggested character |
|-------|---------------|---------------------|
| A. Steady | The easy case | Electronic or pop, constant tempo 118 to 130 BPM, clear kick |
| B. Build and drop | Section changes, quiet passages, the drop | A breakdown where drums stop for 8 to 16 bars, then a clear drop |
| C. Hard | Sparse or ambiguous pulse | Hip-hop or R&B at 70 to 95 BPM (half-time feel), live drummer, or swing |

For each track, record the **reference BPM** (metadata, Tunebat or Beatport,
or your own tapping). `fixtures/real/` is git-ignored.

---

## 3. Environment

Already created in `.venv` (Python 3.13). Dependencies: numpy, librosa 1.0,
soundfile, imageio-ffmpeg (bundles a static FFmpeg; nothing installed
system-wide). To recreate:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

---

## 4. Detection and diagnostics (what `beat_flash.py` does)

1. Decode to mono 22.05 kHz WAV.
2. Onset-strength envelope (median aggregation) at `--hop 256` (11.6 ms).
3. `librosa.beat.beat_track` (tempo prior `--start-bpm 120`, `--tightness 100`).
   These are the **raw** beats, kept as `beats_raw_s`.
4. **Offset correction**: `corrected = raw + offset_ms / 1000`. Default offset
   is minus the calibrated lag for the hop (Section "Offset calibration").
   `--offset-ms 0` disables it.
5. **Optional refinement** (`--refine`): snap each corrected beat to the
   strongest percussive transient within `--refine-window-ms` (default 20).
6. The beats **used** for video and clicks are the refined ones if `--refine`
   was given, otherwise the corrected ones. `beats_used` in the JSON says which.
7. **Octave diagnostics**: tempogram strength at the selected tempo versus
   half and double; warning at ratio 0.75 or higher within 50 to 200 BPM.
8. **Energy diagnostics**: per-beat local RMS (plus or minus 100 ms), low-energy
   regions of 1 s or longer, per-beat flags, and a warning when more than 10%
   of beats are in low-energy regions.
9. Metrics (interval median, CV, max gap) and a 20-second window table.
10. With `--truth`, F-measure at 70 ms and signed offset for raw, corrected,
    and refined lists separately.

Outputs: `<name>_beats.json`, `<name>_diagnostics.csv`, `<name>_diagnostics.txt`,
`<name>_flash.mp4`, `<name>_flash_click.mp4`.

### Offset calibration (`--offset-ms` default)

The default is the negative of a **measured** lag, not a tuned constant:

| Hop (samples at 22.05 kHz) | Hop duration | Raw librosa beats late by (synthetic ground truth) | Default offset |
|----------------------------|--------------|-----------------------------------------------------|----------------|
| 512 (librosa default) | 23.2 ms | +23.2 ms (synth 128) | minus 23.2 ms |
| **256 (this test's default)** | 11.6 ms | +18.8 ms (synth 128), +21.5 ms (synth 92) | **minus 18.8 ms** |
| 128 | 5.8 ms | +16.0 ms (synth 128 only, provisional) | minus 16.0 ms |

Cross-check on real music: at hop 256 the raw beats on Chor and BYE were a
median 17.4 and 20.3 ms late relative to percussive onset peaks, consistent
with the synthetic measurement.

Why the lag exists: `onset_strength` is a spectral-flux difference between
consecutive centered STFT frames (window 2048), so its peak lands about one
frame after the physical transient, and the beat tracker inherits that lag
and quantizes it to the hop. Any other hop gets no correction and a warning
until it is measured.

---

## 5. Visual beat-flash output

640x360 at **60 fps** (flash can start within 8.3 ms of a beat). Whole frame
flashes white on each used beat, orange on the heuristic bar beat, decaying
over about 120 ms. Four boxes across the top count 1-2-3-4 (heuristic). A
pulsing disc grows with each flash. A blue progress bar shows position. The
`_click` variant mixes a 30 ms click (1 kHz, 1.6 kHz on heuristic bar beats)
at the used beat times.

---

## 6. How to evaluate manually

| Symptom | Meaning | Where to look |
|---------|---------|---------------|
| Clicks consistently just before or after the drum hit | Constant offset | Steady sections. Compare against `--offset-ms 0` if in doubt |
| Clicks twice as fast or half as fast as you tap | Octave error | The diagnostics octave warning names the alternative BPM |
| Clicks on the off-beat ("and") | Phase error | Half-time and syncopated tracks |
| Clicks slowly separate from the drums then snap back | Drift or lost beat | Windows with high `cv` in the table |
| Clicks in a passage with no drums | Expected tracker behaviour, listed under low-energy regions | Regions in the diagnostics; not a fail on its own |
| Clicks wander in a breakdown and re-lock late after the drop | Pulse lost | The two bars after each drop |
| Orange flashes or boxes on the wrong beat | Bar heuristic wrong | Ignore, not scored |

Then watch the no-click video once: does the flashing feel on the music?
Answer "yes", "mostly", or "no".

---

## 7. Pass / fail

A track **passes** when all hold:

1. Detected BPM within 2% of your tapped reference, or exactly half or double
   of it with the octave warning present (that is a labelling issue the
   product can expose, not a tracking failure).
2. No section of 4 bars or more where clicks are audibly off the beat
   (early, late, drifting, or off-phase), excluding listed low-energy regions.
3. Fewer than 3 isolated wrong beats in the whole track outside low-energy
   regions.
4. After every breakdown or drop, the click is back in time within 2 beats.
5. The no-click flash video "feels on the beat" (yes or mostly).
6. No `interval CV > 5%` or `dropped beat` warning that a genuine tempo change
   in the music does not explain.

Overall:

- **PASS**: A and B pass, and C passes or fails only on criterion 1. Proceed
  with librosa plus the offset correction for the MVP grid, with the tempo
  prior and the octave choice exposed as user settings.
- **CONDITIONAL**: A passes, B or C fails on breakdowns or phase. Keep librosa
  for the MVP, plan a model-based tracker for v1, add a manual tap override.
- **FAIL**: A fails, or two of three tracks fail. Evaluate alternatives before
  Phase 2.

---

## 8. If librosa fails

Tuning first (all supported here): `--start-bpm` near the tapped BPM,
`--percussive`, `--tightness 50` for live drummers or `300` for rigid
electronic music, `--refine` for tracks whose drums are clean. Then, if still
failing, model-based trackers as listed in the architecture review (allin1
MIT; Beat This!, verify license; madmom models and Essentia have licensing
problems). Not part of this PoC. A manual "tap tempo and nudge" control is
needed in the product regardless.

---

## Results so far

### Synthetic tracks (ground truth known), iteration 2 defaults

| Track | List | F-measure (70 ms) | Median signed offset | p90 abs offset |
|-------|------|-------------------|----------------------|----------------|
| synth 128 BPM | raw | 1.00 | +18.8 ms | 23.2 ms |
| synth 128 BPM | **corrected** | 1.00 | **0.0 ms** | 5.9 ms |
| synth 128 BPM | refined (explicit `--refine`) | 1.00 | +7.9 ms | 8.7 ms |
| synth 92 BPM | raw | 1.00 | +21.5 ms | 26.1 ms |
| synth 92 BPM | **corrected** | 1.00 | **+2.7 ms** | 7.3 ms |
| synth 128, hop 512 | corrected | 1.00 | 0.0 ms | 10.2 ms |

No regression: every beat is still found on both tempos. The constant
correction beats the old refinement on ground truth (0.0 versus 7.9 ms).
The octave diagnostic warns on both synthetic tracks (half-time at 88%,
double-time at 91%) because straight eighth-note hats create genuine
sub-harmonics; this is the diagnostic doing its job, not a tracking error.

### Real tracks: before (iteration 1, refinement on) versus after (iteration 2)

| Track | Metric | Before | After |
|-------|--------|--------|-------|
| Chor (120.2 BPM, 404 beats) | interval CV | 6.5% (warning) | **4.6%** (no warning) |
| Chor | min / max gap | 357 / 644 ms | 395 / 627 ms |
| Chor | octave note | none | double-time 240 BPM at 80% strength, outside plausible range, reported but not warned |
| Chor | energy | not measured | 48 of 404 beats (11.9%) in low-energy regions, 3 in silence; regions at 5.4 to 7.9 s and 173 to 212 s (track ends near 201 s) |
| BYE (166.7 BPM, 475 beats) | interval CV | 6.6% (warning) | **2.9%** |
| BYE | min / max gap | 279 / 456 ms | 325 / 406 ms |
| BYE | octave note | none (166.7 was inside the old 70 to 180 check) | **warning: half-time 83.4 BPM has 94% of the selected strength** |
| BYE | energy | not measured | 13 beats (2.7%) low-energy; silence at 0 to 3.2 s and 174 to 180 s |
| BYE with `--start-bpm 85` (hypothesis run) | tempo | | 84.7 BPM, 239 beats, CV 4.5%; double-time 169.4 at 106% (the two hypotheses are equally strong by tempogram) |
| Chor with explicit `--refine` | interval CV | | 5.6%, worse than corrected: confirms the default-off decision |

Tempo is stable across both songs (every 20-second window at the same median
interval). Alignment to percussive peaks was measured in the investigation at
83 to 92% of beats within 35 ms; the offset correction moves all of them
about 19 ms earlier, which the synthetic data says centres them.

### What still requires human listening

Machine diagnostics cannot answer these. They are the test.

1. **Is the corrected offset right on real music?** Ground truth exists only
   for the synthetic tracks. Listen to the click versions and judge whether
   clicks now sit on the drum hits rather than slightly after.
2. **BYE: 166.7 or 83.4 BPM?** The tempogram cannot decide (94 to 106% either
   way). Tap along and record what you feel. Both grids are time-aligned;
   the question is which one the edit should count in.
3. **Do the low-energy regions match what you hear as "no beat"?** Chor 0 to
   20 s has 18 low-energy beats; BYE has a quiet 3 s intro and a silent
   outro. Confirm the flags land where the music is actually quiet.
4. **Chor 160 to 200 s** shows the highest jitter (CV 5.8 to 7%) and falling
   energy. Listen there for drift or a lost beat.
5. **Does the no-click video feel on the beat?** No metric measures this.
6. **The bar heuristic** (orange flashes, boxes) is unverified and expected to
   be wrong on some tracks. Do not score it; note it if it distracts.
