# Beat Flash PoC: Conclusion and Closure

Status: **CLOSED** on 2026-09-06 after two iterations.
Scope: an isolated proof of concept under `experiments/beat_flash/`. It shares no
code with the main project. The folder is kept as a reference and as a test
fixture generator; it is not a dependency of anything.

Final code state: `beat_flash.py` (iteration 2), `make_synthetic.py`,
`requirements.txt`, `README.md`, `evaluation_sheet.md`, and this document.
Results referenced below are in `results/synth/`, `results/real/`,
`results/real_prior85/`, `results/real_refine/`, and `results/real_v1/`
(iteration 1 outputs kept for the before/after comparison).

---

## 1. Original hypothesis

> librosa's beat tracker, run on a full music track, produces beat timestamps
> that are aligned closely enough with the perceived beats (visually and
> audibly) to serve as the timing grid for music-synchronised video cuts.

Implied sub-claims that were tested:

- Every perceived beat is found, with no false beats, on steady 4/4 music.
- Timing error per beat is small relative to one video frame (33 ms at 30 fps,
  17 ms at 60 fps).
- The result holds on real, mixed music, not only on synthetic drums.

## 2. What was validated

Machine-verified, on two synthetic tracks with ground truth (128 and 92 BPM, with
drumless pad intros) and two real tracks (Chor, 120 BPM, 3:32; BYE, 166.7 or
83.4 BPM, 3:00):

| Claim | Evidence | Verdict |
|-------|----------|---------|
| All beats found, no false beats, on steady 4/4 | F-measure 1.00 at 70 ms tolerance on both synthetic tracks (80 and 43 beats). Interval CV 1.2% and 2.6%. | Validated |
| Tempo estimate is stable across a whole real track | Chor: every 20 s window at 499.2 ms median interval. BYE: every window at 359.9 ms. | Validated |
| Timing error can be brought under one frame | Raw librosa beats are a constant 17 to 23 ms late (one analysis frame). After the calibrated offset, median error is 0.0 ms and +2.7 ms on ground truth, p90 under 8 ms. | Validated, with correction |
| Real tracks align to the drums | 83 to 92% of raw beats within 35 ms of a percussive onset peak; 94 to 96% within 70 ms, before offset correction. Interval CV 4.6% (Chor) and 2.9% (BYE) after iteration 2. | Validated for the loud body of both songs |
| The renderer shows what the detector decided | Frames sampled at detected beats measure bright (142 gray), mid-beat frames dark (21). Click energy at 1 kHz is 34x higher on beats than between them. | Validated |
| A tempo prior is enough to flip the octave when needed | BYE with `--start-bpm 85` gives 84.7 BPM, 239 beats, CV 4.5%, every 20 s window on the same interval. | Validated as a control, not as automation |

## 3. What failed or showed limitations

| Finding | Detail | Consequence |
|---------|--------|-------------|
| Raw output is late by one analysis frame | +23.2 ms at hop 512 (librosa default), +18.8 ms at hop 256, +16.0 ms at hop 128, measured against ground truth and confirmed on real tracks (+17.4 and +20.3 ms vs percussive peaks). | Raw librosa timestamps must never be used directly for cutting. A calibrated constant offset is required. |
| Nearest-transient refinement hurt real music | The iteration 1 refinement (snap to the nearest transient in the full mix within 45 ms) improved synthetic drums (offset 18.8 to 7.9 ms) but raised jitter on real music (Chor CV 4.6% to 6.5%, BYE 2.9% to 6.6%) by snapping to vocal and guitar transients. A narrow percussive-only version still measured worse than the constant offset (Chor CV 5.6%). | Refinement is off by default and is not carried into the MVP. |
| Octave ambiguity is real and undetected by range checks | BYE was tracked at 166.7 BPM with a half-time alternative at 94% of its tempogram strength; with a prior of 85 the alternative reaches 106%. A 70 to 180 BPM sanity check did not flag it. The tempogram cannot decide; only a listener can. | The MVP must expose octave choice to the user and must never assume the tracker's BPM is the felt tempo. |
| The tracker emits beats through silence and quiet passages | Chor: 48 of 404 beats (11.9%) in low-energy regions, 3 in digital silence after the song ends at about 201 s. BYE: beats in a 3 s silent intro and a 6 s silent outro. | Beat existence is not evidence of a perceived beat. Energy must gate where cuts are allowed. |
| Jitter rises in quiet, sparse sections | Chor 160 to 200 s: window CV 5.8 to 7% with falling energy, versus 1.9 to 3.2% in the loud middle. | Grid confidence varies along the track; the MVP should carry a per-region confidence, not a single number. |
| The 4/4 bar heuristic is unreliable | It picked the wrong phase on the synthetic track (preferred the snare on beat 2) and different phases on the two real tracks. | Not validated, not scored, and not carried forward. Downbeats need a proper model, which is out of scope for MVP v0. |
| Short clips are useless as test material | 5 s and 12 s clips produced 8 and 23 beats; too few for any conclusion. | Test material must be full tracks. |

## 4. Final algorithm (as shipped in `beat_flash.py`, iteration 2)

1. Decode the track to mono 22.05 kHz with FFmpeg (bundled binary via `imageio-ffmpeg`).
2. Onset-strength envelope: `librosa.onset.onset_strength`, median aggregation, hop 256 samples (11.6 ms).
3. Beat tracking: `librosa.beat.beat_track` on that envelope, tempo prior 120 BPM, tightness 100, `trim=False`. Output is the **raw** beat list and the tempo estimate. Raw beats are always retained.
4. **Offset correction**: `corrected = raw + offset`, with `offset = -18.8 ms` at hop 256 (`-23.2` at hop 512, `-16.0` at hop 128, provisional). The values are measured tracker lag against synthetic ground truth, cross-checked on real music. Hops without a measurement get no correction and a warning. This is the list used for video, clicks, and any downstream consumer.
5. **Optional refinement** (`--refine`, off by default): snap each corrected beat to the strongest percussive transient within 20 ms. Kept for experiments only.
6. **Octave diagnostics**: tempogram strength at the selected tempo versus half and double; warning when an alternative inside 50 to 200 BPM has 75% or more of the selected strength. Never changes the tempo.
7. **Energy diagnostics**: frame RMS in dBFS (frame 2048, hop 512); reference = median of frames above minus 50 dBFS; low-energy threshold = reference minus 12 dB; regions of 1 s or longer listed; each beat flagged `low_energy` and `silent`. Beats are never removed.
8. Metrics and a 20-second window table (beats, median interval, CV, energy, low-energy count); per-beat CSV; JSON; text summary.
9. Rendering (verification only): 60 fps flash video, plus a variant with a 30 ms click mixed at each used beat.

Dependencies: numpy, librosa 1.0, soundfile, imageio-ffmpeg. Python 3.13. No GPU.

## 5. Known limitations

**Octave ambiguity.** For a class of tracks (half-time grooves, straight
eighth-note hats, 160 to 180 BPM feels) the tracker's BPM and its half or double
are equally supported by the signal. The PoC detects the ambiguity and warns;
it does not resolve it. Resolution requires a human choice or a tempo prior
set by the user. Any product built on this must expose that choice.

**Low-energy and silence regions.** The dynamic-programming tracker always
returns a complete grid from the first frame to the last, including through
silent intros, breakdowns, and the silence after a song ends. Beats in those
regions are real grid points and false perceptual beats at the same time. The
PoC flags them and leaves them in place. Downstream consumers must treat a beat
inside a low-energy region as "grid position exists" and not as "cut here".

**A beat grid is not automatically a cut grid.** The PoC produces beat times
only. It does not know bars, phrases, sections, drops, or which beats are
strong. A cut on every beat at 120 BPM is a cut every 500 ms, which is an
editing decision, not a detection result. Turning beats into allowed cut points
(bar-level, beat-level, or half-beat by section, with density chosen by the
edit's pacing) is the job of the editing layer, and the "bar" structure it
needs is not provided here.

**Human perceived tempo may differ from the selected BPM.** Beyond the strict
octave case, listeners feel some tracks at 3:2 or 2:3 of the tracker's rate
(shuffle, triplet feels, 6/8), and some tracks have sections that feel
half-time against a constant grid. The PoC's "tempo" is the periodicity that
best explains the onset envelope, which is not the same quantity as felt
tempo. The evaluation sheet asks the listener to tap for this reason.

Also noted: the constant offset was calibrated on synthetic drums and
cross-checked on two real tracks. It is not guaranteed for every genre, mix, or
transient shape; the real-music check is by ear.

## 6. Decisions that carry into the MVP architecture

1. **librosa is sufficient for the MVP beat grid**, at hop 256 with the
   calibrated offset applied. No model-based tracker is needed for v0.
2. **Never consume raw tracker timestamps.** The offset correction is part of
   the beat analyser's contract, and its calibration constant lives with the
   analyser version.
3. **Do not snap beats to nearby transients.** Full-mix nearest-transient
   refinement is harmful on real music. If timing ever needs tightening, fit a
   constant-tempo grid over stable sections instead; do not chase transients.
4. **Store raw, corrected, and diagnostics together.** The MusicMap must carry
   raw beat times, the offset applied, the analyser version, the octave
   alternatives with their strength ratios, and per-beat energy flags.
5. **Octave choice is a user-visible setting.** The MusicMap carries the
   selected BPM and the strongest alternative; the UI can offer "count this at
   half speed / double speed" and the analyser re-emits the grid with the prior.
6. **Energy gating belongs to the grid, not to the tracker.** The cut grid
   consumes beat flags and excludes or down-weights beats in low-energy regions.
7. **Per-region confidence, not a single number.** Interval jitter and energy per
   window are cheap and predictive of where the grid is trustworthy.
8. **The 4/4 bar heuristic does not ship.** Bar and downbeat information is
   absent from the MVP v0 grid by design; section granularity ("bar" cuts) is
   therefore not available in v0 and pacing must be expressed in beats.
9. **Full tracks only** as analysis input and as test fixtures; the synthetic
   generator stays as a regression fixture for the analyser.
10. **60 fps verification renders and a click overlay** are the standard way to
    check any future change to the beat analyser by ear.

## 7. Out of scope for MVP v0 (belongs to later phases)

The following were not built, not tested, and are explicitly excluded from the
first MVP. They appear in the architecture document as v1 or later and must not
be pulled forward by the Phase 02 implementation.

- **Lyrics understanding**: no transcription of sung lyrics, no language
  understanding of what a song is about.
- **Lyric timestamps and semantics**: no word or line timing, no caption sync
  to lyrics, no "cut on this word".
- **Song section understanding**: no intro, verse, chorus, bridge, or outro
  labelling; no section boundaries. The MVP v0 grid is beats plus energy only.
- **Drop detection**: no automatic identification of build-ups or drops. The
  PoC's energy diagnostics are per-beat loudness flags, not structural events.
- **Emotion-aware editing**: no mood, valence, or energy-arc interpretation of
  the music feeding editing decisions.
- **Downbeat intelligence**: no downbeat or bar detection, no time-signature
  estimation. The unverified 4/4 heuristic in the PoC is not carried forward.

Also not part of v0 (from the PoC's own limits): automatic octave resolution,
tempo-change tracking within a track, swing or triplet feel detection, and
any additional AI model for rhythm analysis.

## 8. Phase 02 Handoff

What the MVP implementation may take from this PoC, and how.

**Take as specification (re-implement inside the project, do not import the PoC):**

- The analysis chain in Section 4, steps 1 to 4, 6 and 7, as the first
  implementation of the `BeatAnalyzer` interface from the architecture
  document. Parameters: sample rate 22050, hop 256, median-aggregated onset
  strength, `beat_track` with `trim=False`, tightness 100, tempo prior as an
  input parameter defaulting to 120.
- The offset table `CALIBRATED_LAG_MS = {512: 23.2, 256: 18.8, 128: 16.0}` and
  the rule that unmeasured hops apply no correction and raise a warning. The
  table is versioned with the analyser.
- The MusicMap fields established here: `bpm`, `beats_raw_s`, `beats_corrected_s`,
  `offset_ms`, `offset_source`, `octave.alternatives[{bpm, strength_ratio,
  plausible_range}]`, `energy.low_energy_regions`, per-beat `local_energy_db`,
  `low_energy`, `silent`, and the 20-second `windows` summary.
- The warning rules: interval CV above 5%, a gap above 1.8x the median
  interval, an octave alternative at 75% or more strength inside 50 to 200
  BPM, more than 10% of beats in low-energy regions.

**Take as test assets:**

- `make_synthetic.py` and the two generated fixtures with ground-truth JSON, as
  regression tests for the analyser: expected F-measure 1.00 at 70 ms and a
  median corrected offset within plus or minus 5 ms.
- The two real tracks' diagnostics in `results/real/` as reference numbers for
  detecting behaviour changes (Chor: 120.2 BPM, 404 beats, CV 4.6%; BYE:
  166.7 BPM, 475 beats, CV 2.9%, half-time alternative at 94%). The audio
  files themselves are copyrighted and stay git-ignored.

**Take as verification tooling (may be copied into a dev-tools folder):**

- The flash-video renderer and click-overlay generator, to audit any future
  change to the beat analyser by ear.

**Do not take:**

- The `--refine` path. It is a documented negative result.
- The `bar_phase` heuristic and anything that renders orange "bar" flashes or
  1-2-3-4 boxes.
- The assumption that a beat equals a cut point. The cut grid is a separate
  component that consumes this analyser's output together with the pacing
  plan and the energy flags.

**Open item that Phase 02 inherits:** the listening verdicts on
`evaluation_sheet.md` for Chor and BYE (offset by ear, tapped BPM, low-energy
regions matching what is heard). The machine diagnostics are complete; the
human confirmation is recorded on the sheet, not in this document.
