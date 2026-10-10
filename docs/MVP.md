# MVP v0: Proof of Concept Definition

Version 0.1, 2026-09-05 (consistency fixes 2026-10-10, see `PROJECT_AUDIT.md` §4). Status: v0 product and evaluation specification. This is not a claim that every listed behavior is implemented; current implementation status is in `README.md`. Companion to `ARCHITECTURE.md` (the aspirational system design) and `ARCHITECTURE_REVIEW.md` (verdicts on it). This document defines the *smallest* thing that tests the product hypothesis. Everything here is deliberately smaller than the MVP scope in ARCHITECTURE.md Section 19; that scope is v0.3, this is v0.

## The hypothesis under test

> A system can automatically select appropriate moments from raw footage and arrange them into a musically synchronized, aesthetically reasonable short video based on a creative instruction.

That sentence contains four separable claims. The PoC must be able to fail on each one independently, otherwise a failure teaches nothing.

| Claim | What would falsify it | How v0 tests it |
|-------|----------------------|-----------------|
| A. Moment selection is *appropriate* | Viewers cannot distinguish the system's selection from random selection | Blind pairwise test against a random baseline on the *same* beat grid |
| B. Output is *musically synchronized* | Cuts drift off beats, or beat sync is present but viewers do not perceive it | Mechanical check (every cut on a beat) plus rater question |
| C. Output is *aesthetically reasonable* | Raters would not post it even with small tweaks | Absolute usability question |
| D. Output *follows a creative instruction* | Raters cannot tell which output was made from which instruction | Instruction-identification test |

Claim A is the one nobody has evidence for yet and the one the whole product depends on. Claims B and D are engineering. Claim C is the honest gate.

---

## 1. Exact MVP goal

**Build a command-line tool that takes a folder of raw phone clips, one music track, and a two-word instruction, and produces a 15 to 30 second MP4 in under two minutes on a CPU, with every cut on a beat and with clip choice that blind raters prefer over random choice on the same beat grid.**

Concretely, this command must work end to end:

```bash
python -m poc run --footage ./project/footage --music ./project/music/track.mp3 --mood energetic --duration 20 --output ./project/output/final.mp4
```

`--output` (also accepted as `--out`) names the output video file, not a
directory. Artifacts are written beside it. `--allow-development-inputs`
explicitly bypasses input limits for synthetic/development work only.

and produce `project/output/final.mp4` with `timeline.json`, `music_map.json`, `footage/` analysis files, and `contact_sheet.png` beside it.

The deliverable is not the tool. The deliverable is the **answer to the hypothesis**, backed by a rater sheet from at least five people over at least 30 outputs. The tool is the instrument.

---

## 2. What must be included

| # | Component | Why it is in |
|---|-----------|-------------|
| 1 | **Timeline Document v0** (JSON, Pydantic, validator) | The one contract shared by cutter, renderer, baseline, and evaluator. Without it every part couples to every other part. |
| 2 | **Renderer** (FFmpeg: trim, normalize, concat, mux music) | Nothing can be judged until it can be seen. |
| 3 | **Music analysis** (beats, per-beat energy, downbeat phase, window choice) | Beat grid defines the slots. Energy drives pacing and motion targets. |
| 4 | **Video analysis** (per-clip motion, sharpness, exposure curves at 4 Hz) | The minimum signal for selection to be better than random. |
| 5 | **Deterministic Cutter** (slots from grid, greedy scoring with penalties) | This is the editing intelligence under test. |
| 6 | **Style table** (two moods mapped to cutter parameters) | This is the entire "creative instruction" layer in v0. |
| 7 | **Random baseline generator** (same grid, random moments) | The control. Without it a good-looking output proves nothing. |
| 8 | **Evaluation harness** (batch generation, shuffled pairs, rater CSV) | Turns opinions into a number. |
| 9 | **Analysis cache** (JSON per file, keyed by content hash) | Re-running the cutter must take seconds, not minutes, or iteration on scoring stalls. |

Nine pieces. If a tenth is proposed, it must displace one of these.

---

## 3. What must explicitly NOT be included

Each exclusion below has a reason and a re-entry condition. These are decisions, not oversights.

| Excluded | Reason | Comes back when |
|----------|--------|-----------------|
| **LLM of any kind** | See Section 8. The hypothesis is about editing intelligence, not language understanding. | v0.2, after Claim A passes, as the offline rule-based Local Director (`LOCAL_DIRECTOR.md`); no external AI API ever (`OFFLINE_POLICY.md`) |
| Natural-language briefs, revisions, chat | Same reason. Instruction is an enum. | v0.2 |
| Timeline versioning, operation grammar, undo, locks | Nothing is being revised in v0. | v0.3 |
| Transitions (crossfade, dip, whip, flash) | Hard cuts on beats are the strongest form of sync and the cleanest experiment. Transitions blur the timing signal being tested. | After Claim B passes |
| Speed changes, ramps | Speed is always 1.0. Removes a whole class of source-range arithmetic. | v1 |
| Color grading, LUTs, captions, graphics | Aesthetic decoration would confound Claim C. Raters must judge selection and rhythm. | v0.3 |
| Aspect-ratio reframing, vertical output | Output matches the footage. Mixed-aspect footage is scaled and center-cropped to the majority aspect. Landscape phone clips in, landscape out. | v0.3, per review item 31 |
| Sections, drop detection, music structure | Per-beat energy is sufficient to vary pacing. Structure adds a fragile dependency (allin1) for a second-order effect. | v0.2 |
| Shot detection (TransNetV2, PySceneDetect) | Raw footage is continuous takes; candidates are windows over the motion curve, not detected shots. Review item 11. | Only if footage sets contain pre-edited material |
| Faces, people, objects, embeddings, VLM, aesthetic models | No GPU, no downloads, no licensing questions. Motion plus sharpness plus exposure first. | Faces (YuNet) are the **first** thing added if Claim A fails or barely passes. |
| Footage audio | Ignored entirely; music replaces it. | v1 |
| Database, job queue, workers, storage abstraction | Files on disk. One process. | v0.3 |
| Web UI, API, preview player | CLI plus a media player. | Phase 8 of the roadmap |
| Auth, multi-tenancy, cloud, Docker | Single developer, single machine. | Never in PoC |
| GPU, torch, allin1, madmom | Pure CPU: numpy, OpenCV, librosa. Installs natively on Windows in one command. | v0.2 for allin1 if librosa's beat tracker proves unreliable on the test tracks |
| Partial re-render, segment cache | Full re-render of a 20 s video is under 30 s. Not worth the complexity. | v0.3 |

---

## 4. The smallest end-to-end workflow

```
project/footage/*.mp4 ─┐
project/music.mp3     ─┼─► [1] Probe & analyze (cached) ─► footage_index.json, music_map.json
--mood --duration     ─┘              │
                                      ▼
                       [2] Style table: mood ─► CutterParams
                                      │
                                      ▼
                       [3] Grid ─► slots (beat-aligned, length from energy + pacing)
                                      │
                                      ▼
                       [4] Cutter: for each slot, score candidate windows, pick best
                           (or Baseline: pick random)      ─► timeline.json
                                      │
                                      ▼
                       [5] Validate timeline (contiguous, on-beat, in-range, no reuse)
                                      │
                                      ▼
                       [6] Render: trim segments ─► concat ─► mux music ─► out.mp4
```

Steps 1 and 6 are I/O heavy and cached or bounded. Steps 2 to 5 are pure functions over JSON and run in well under a second. That property is what makes scoring iteration fast: change a weight, re-run steps 2 to 6, watch the result in 30 seconds.

Runtime budget on a laptop CPU for 10 clips totaling 5 minutes of 1080p footage: analysis about 60 to 90 s on first run (then cached), music about 5 s, cutter under 1 s, render about 20 to 40 s.

---

## 5. Minimum input format

```
project/
    footage/            5 to 20 files, .mp4 or .mov, 5 s to 3 min each, any fps or resolution
    music/track.mp3     .mp3, .wav, .m4a, or .flac; at least duration + 10 s long
```

Plus CLI flags: `--mood {energetic|calm}`, `--duration <seconds, 15 to 30>`, and `--output <file.mp4>` (`--out` is an alias).

Optional flags that exist only to unblock testing: `--seed` (baseline randomness), `--bpm-hint` (when the beat tracker locks to half or double tempo), `--music-offset-s` (force the music window start), `--baseline` (produce the random control instead of the scored cut).

Constraints are enforced with actionable errors: 5 to 20 supported video files, each 5 seconds to 3 minutes, with total footage at least 4 times the target duration; music must have an audio stream and be at least 10 seconds longer than the requested output. Variable-frame-rate phone video is handled by the renderer normalizing every segment to constant 30 fps; the analyzer samples by timestamp, not frame index. The prototype is video-only: still images/screenshots are not supported as timeline assets.

Output:

```
project/output/                (parent folder of --output)
    music_map.json                 music analysis result
    footage/<filename>.json        per-input footage analysis result
    timeline.json                  the Timeline Document
    final.mp4                      1080p H.264, AAC, faststart
    contact_sheet.png              one thumbnail per clip with slot time, source, score, rationale (debug)
```

Content-addressed analysis cache is separate from this project output:
`%LOCALAPPDATA%/poc/analysis` on Windows, `$XDG_CACHE_HOME/poc/analysis` on
Linux/macOS.

Cache keys include source SHA-256, analyzer version, and effective analysis
parameters. Changing any of these creates a new cache entry; deleting the
per-user `poc/analysis` directory clears cached maps. Evaluation manifests
record source hashes, analysis parameters/version, software version, source
fingerprint, Git commit, and whether tracked files were dirty at generation.

---

## 6. Minimum video analysis

Per file, sampled at 4 Hz by timestamp on a 320-pixel-wide grayscale downscale:

| Signal | Method | Cost |
|--------|--------|------|
| `motion[t]` | Mean absolute difference between consecutive sampled frames, divided by 255 | Trivial |
| `sharpness[t]` | Variance of the Laplacian | Trivial |
| `luma[t]`, `clip_frac[t]` | Mean gray value; fraction of pixels above 250 or below 5 | Trivial |
| `duration_ms`, `fps`, `width`, `height` | ffprobe | Trivial |

That is the whole per-file index: four curves and four scalars. Stored as JSON under the file's content hash so re-analysis never repeats.

Normalization happens at cutter time, not analysis time: `motion` and `sharpness` are converted to percentile ranks across the *whole footage set*, so "high motion" means high relative to this shoot, not an absolute threshold. This one decision removes most footage-dependent tuning.

Candidates are not precomputed. For a slot of length `L`, the candidate set is every `(file, in_ms)` at 250 ms steps where `in_ms + L` fits inside the file with a 200 ms margin at each end. The cutter evaluates each candidate directly from the curves. A 5-minute footage set yields about 1,200 candidates per slot, and a 20-slot edit scores about 24,000 windows, which takes well under a second in numpy.

What is deliberately absent: optical flow, camera-motion classification, faces, scale, keyframes, embeddings. The first of these to add is faces, and only if Claim A demands it.

---

## 7. Minimum music analysis

All via librosa on 22.05 kHz mono:

| Output | Method |
|--------|--------|
| `bpm`, `beats_ms[]` | `librosa.beat.beat_track` with `--bpm-hint` as `start_bpm` when given |
| `energy_per_beat[]` | RMS energy averaged between consecutive beats, then normalized to 0..1 by percentile across the track |
| `window_in_ms` | The current v0 implementation starts at the first detected beat, or uses the explicit `--music-offset-s` override. It does not rank candidate music windows by mood. |

The 4/4 downbeat phase heuristic and mood-ranked music-window selection described in earlier drafts are not implemented. `experiments/beat_flash/CONCLUSION.md` found the phase heuristic unreliable; v0 uses no assumed downbeats. The beat-flash experiment validates beat timing, not downbeat labels. Any later downbeat method requires a separate evaluation and a protocol amendment before use in an official result.

What is deliberately absent: onsets for sub-beat sync, sections, drops, loudness normalization (music gain is a fixed -1 dB with a 1 s fade out).

---

## 8. Is an LLM necessary in MVP v0?

**No.** Three reasons.

1. **It is not what is being tested.** In the full architecture the LLM Director translates a natural-language brief into an `EditPlan`: a structured set of pacing, scale, and motion parameters. In v0 the style table produces that same structured object from an enum. If the deterministic core cannot make a good edit from a perfect, hand-written plan, no Director will save it. If it can, adding a Director is a well-understood structured-output task with near-zero technical risk.

2. **It would contaminate the experiment.** A model in the loop makes every output non-reproducible and every failure ambiguous: was the plan wrong or the cut wrong? The PoC needs each failure to point at exactly one component.

3. **It would cost time that belongs to scoring.** Prompt design, schema validation, retries, and API plumbing are a week of work that produces nothing the raters can see.

What v0 does to make the LLM trivial to add later: the style table's output type, `CutterParams`, is written as a Pydantic model with field descriptions. In v0.2 the Director's job is defined as "emit a valid `CutterParams` from a brief", and the v0 Cutter consumes it unchanged. The interface is fixed now; the implementation behind it swaps.

---

## 9. The exact Timeline schema needed

Timeline positions are **integer frames at a fixed timeline fps of 30** (review item 2: no millisecond drift, exact equality on grid snapping). Source positions are **integer milliseconds** because source files have their own clocks and FFmpeg seeks in seconds. Speed is always 1.0, so a clip's source out-point is implied by its length and is not stored.

```json
{
  "schema_version": "0.1",
  "settings": {"fps": 30, "width": 1920, "height": 1080},
  "duration_frames": 600,
  "music": {"path": "music.mp3", "in_ms": 15230, "gain_db": -1.0, "fade_out_ms": 1000},
  "beats": [
    {"frame": 0,  "downbeat": true,  "energy": 0.62},
    {"frame": 15, "downbeat": false, "energy": 0.60}
  ],
  "clips": [
    {
      "id": "c001",
      "start_frame": 0,
      "end_frame": 30,
      "source": {"path": "footage/IMG_0412.mp4", "in_ms": 3250},
      "score": 0.81,
      "rationale": {"quality": 0.90, "motion_match": 0.75, "reuse": 0.0, "coverage": 0.1}
    }
  ],
  "provenance": {
    "generator": "cutter-v0",
    "params": {"mood": "energetic", "duration_s": 20},
    "seed": null,
    "created_at": "2026-09-05T12:00:00Z"
  }
}
```

Present and load-bearing: one implicit video track, beats as informational markers, `rationale` as the debugging signal for why a clip won, `provenance` so an evaluator can tell system from baseline without trusting the filename.

Absent on purpose from the legacy v0.1 timeline: tracks, sections, transitions, speed, transform, effects, graphics, `version` and `parent_version` (they arrive with the operation grammar in v0.3), `shot_id` (there are no shots), and `locked`. P2.1 adds a separate editor-timeline v1.0 model; P2.2 provides explicit v0.1-to-v1.0 dispatch. Neither changes this MVP contract or its cutter/renderer consumers.

Validator invariants for v0, each with a machine-readable code:

| Code | Invariant |
|------|-----------|
| `CONTIGUOUS` | Clips sorted by `start_frame` tile `[0, duration_frames)` exactly, no gaps, no overlaps |
| `MIN_LENGTH` | Every clip is at least 6 frames (200 ms) |
| `ON_BEAT` | Every `start_frame` equals a beat frame within 1 frame |
| `SOURCE_RANGE` | `in_ms >= 0` and `in_ms + frames * 1000 / fps <= source duration` |
| `NO_SOURCE_REUSE` | No two clips from the same file have overlapping source ranges |
| `UNIQUE_IDS` | Clip ids are unique |
| `MUSIC_RANGE` | `music.in_ms + duration <= music duration` |

The schema maps to the full v1 schema in ARCHITECTURE.md Section 12.2 by wrapping `clips` into `tracks[0].clips`, adding `speed: {constant, 1.0}`, and converting frames to the chosen time base. No information is lost in either direction.

---

## 10. How success will be measured

### Test set

- **5 footage sets**, real phone footage, 8 to 15 clips each, 3 to 10 minutes total. Vary the content: one with people, one landscape or travel, one product or object, one event, one mixed. Do not use stock footage; it is already curated and would inflate results.
- **3 music tracks** with different tempo and energy profiles, all passing the beat-flash test.
- **2 moods**: energetic, calm.
- 5 x 3 x 2 = **30 system outputs**, plus **30 baseline outputs** on identical grids and music windows.

### Metrics and pass thresholds

| # | Claim | Measurement | Pass | Interpretation of failure |
|---|-------|-------------|------|---------------------------|
| M1 | B (mechanical) | Validator passes; every cut within 1 frame of a beat; render succeeds; wall time under 2 min on the first run and under 45 s cached | 100% of 60 outputs | Engineering bug, fix it |
| M2 | A | Blind pairwise: system vs baseline, same footage, music, grid. 5 raters, order randomized. "Which is the better edit?" | System preferred in **at least 70%** of 150 judgments | 50% means selection adds nothing. 55 to 65% means the signals are too weak; add faces and re-run. |
| M3 | D | Show the energetic and calm outputs of the same footage and music side by side. "Which one was asked to be energetic?" | **At least 80%** correct | Style table parameters are not different enough, or the music window choice dominates |
| M4 | C | Per system output: "Would you post this after small manual tweaks?" | Yes from at least 3 of 5 raters on **at least 50%** of outputs | The honest gate. Can fail while M2 passes; that means the whole approach needs semantics before it is useful. |
| M5 | B (perceived) | Per output: "Do the cuts feel in time with the music?" 1 to 5 | Mean **at least 4.0** | If M1 passes and M5 fails, hard cuts alone are not enough and transitions or downbeat weighting come forward |

### Decision rule

- M1 through M5 all pass: hypothesis validated. Proceed to v0.2 (LLM Director, natural-language brief) with confidence that the core is worth wrapping.
- M2 passes, M4 fails: selection works but output is not good enough to ship. Add faces and downbeat-aligned motion peaks, re-run once. If M4 still fails, the product needs semantic understanding (VLM) before it is useful, which changes the cost structure and must be decided explicitly.
- M2 fails after one iteration with faces: the cheap-signal hypothesis is false for this footage class. Stop and rethink perception before writing any more cutter code.

**Implementation boundary:** the frozen evaluation protocol is
`docs/EVALUATION_PROTOCOL.md`; its thresholds, dataset size, and decision
rules remain unchanged. References above to downbeat-aligned motion peaks
are historical decision-rule text, not a supported v0 capability. Do not
silently substitute a downbeat heuristic or change the frozen rule; any
future iteration must preserve the protocol's versioned amendment process.

### Iteration budget

Two scoring iterations after the first full evaluation. Each iteration is one change to signals or weights, re-run on all 30 pairs. More than two iterations without crossing 70% is a signal to stop, not to keep tuning.

---

## 11. Step-by-step development sequence

Each step ends with something observable. Estimates assume one developer, full time. FFmpeg is provided by the bundled `imageio-ffmpeg` binary; a system FFmpeg on PATH is not required (see README).

| Step | Build | Check before moving on | Time |
|------|-------|------------------------|------|
| 0 | Repo skeleton: `poc/` package, `pyproject.toml`, deps (numpy, opencv-python-headless, librosa, pydantic, soundfile). Confirm `ffmpeg -version` and `ffprobe` run. | `python -c "import cv2, librosa, pydantic"` | 1 h |
| 1 | `timeline.py`: schema, validator, load and save. `examples/hand_timeline.json` with 4 clips from real files. | `python -m poc.timeline examples/hand_timeline.json` prints `OK` and, with a deliberate gap, prints `CONTIGUOUS` | 0.5 day |
| 2 | `render.py`: timeline to MP4. Per-clip trim and normalize to 1080p 30 fps, concat demuxer, music mux with fade. | Hand timeline renders. `ffprobe` shows exactly `duration_frames` frames. A/V lengths match. Plays in VLC. | 1 day |
| 3 | `music.py`: beats and per-beat energy (as implemented; the downbeat and mood-ranked window parts were dropped, see Section 7). Plus the beat-flash renderer as a subcommand. | Beat-flash video looks right on all 3 test tracks (use `--bpm-hint` if needed). | 1 day |
| 4 | `grid.py` and `baseline.py`: slots from beats and params; random moment per slot; writes a valid timeline. **First end-to-end beat-synced video with zero editing intelligence.** | `python -m poc run --baseline ...` produces a watchable, on-beat MP4 from real footage. Validator passes. This is also the control for M2. | 0.5 day |
| 5 | `footage.py`: per-file curves, ffprobe metadata, content-hash cache. `contact_sheet.py` debug output. | Plot motion curve for one file next to its thumbnails; peaks correspond to visible movement. Second run of analysis is instant. | 1 day |
| 6 | `cutter.py`: candidate generation, scoring, greedy selection with reuse and coverage penalties, rationale. `style.py`: two moods to `CutterParams`. | Same footage and music, `--mood energetic` vs `--mood calm` produce visibly different edits. Rationale in `timeline.json` explains each pick. Personally compare against baseline on 3 sets. | 2 days |
| 7 | `eval.py`: batch-generate the 60 outputs, name them by opaque ids, write `pairs.csv` and a rater instruction sheet. | Dry run with yourself as rater. | 0.5 day |
| 8 | Run the blind evaluation with 5 raters. Compute M1 to M5. Write `docs/EVAL_v0.md` with numbers and rater comments. | Decision rule in Section 10 applied. | 2 days including rater time |
| 9 | At most two scoring iterations (faces via YuNet is the planned first one). Re-run step 8 on the 30 pairs. | Cross 70% on M2 or stop. | 1 to 3 days |

About 10 working days to a validated or falsified hypothesis. Steps 1 and 3 are independent and can be done in either order; step 3's beat-flash check is the single riskiest dependency and can be spiked on day one in half an hour if there is any doubt about the music.

### Renderer notes for step 2

Three FFmpeg invocations, no filter graphs across clips:

1. Per clip: `-ss <in_s> -i <src> -t <frames/30> -r 30 -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1" -an -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p seg_NNN.mp4`. Placing `-ss` before `-i` is accurate when re-encoding and much faster than after.
2. Concat demuxer over the segment list with `-c copy`.
3. Mux: `-i video.mp4 -ss <music_in_s> -i music.mp3 -t <duration_s> -af "volume=-1dB,afade=t=out:st=<duration_s - 1>:d=1" -c:v copy -c:a aac -movflags +faststart out.mp4`.

Check each segment's frame count with `ffprobe -count_frames`; a segment that is one frame short is the first bug you will meet, and it comes from variable-frame-rate sources.

---

## 12. The first piece of code

`poc/timeline.py`. The Timeline Document is the only thing every other module shares: the cutter and baseline write it, the renderer and contact sheet read it, the evaluator compares two of them. Writing it first fixes the contract, and the hand-written example it validates becomes the renderer's first test input on day two.

It is deliberately small: three helper functions, seven Pydantic models, one validator returning machine-readable codes, and a command-line entry point.

```python
"""poc/timeline.py: Timeline Document v0.

The single contract of the proof of concept. The cutter and the baseline write it,
the renderer and the contact sheet read it, the evaluator compares two of them.

Time bases:
  timeline positions  -> integer frames at settings.fps (exact grid equality, no drift)
  source positions    -> integer milliseconds (sources have their own clocks; FFmpeg seeks in seconds)
Speed is always 1.0 in v0, so a clip's source out-point is implied by its frame length.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

TIMELINE_FPS = 30
MIN_CLIP_FRAMES = 6          # 200 ms at 30 fps
ON_BEAT_TOLERANCE_FRAMES = 1


def ms_to_frame(ms: float, fps: int = TIMELINE_FPS) -> int:
    return int(round(ms * fps / 1000.0))


def frame_to_ms(frame: int, fps: int = TIMELINE_FPS) -> int:
    return int(round(frame * 1000.0 / fps))


def frames_to_seconds(frames: int, fps: int = TIMELINE_FPS) -> float:
    return frames / fps


class Settings(BaseModel):
    fps: int = TIMELINE_FPS
    width: int = 1920
    height: int = 1080


class Music(BaseModel):
    path: str
    in_ms: int = 0                 # position in the track that sits at timeline frame 0
    gain_db: float = -1.0
    fade_out_ms: int = 1000


class Beat(BaseModel):
    frame: int
    downbeat: bool = False
    energy: float = Field(ge=0.0, le=1.0)


class Source(BaseModel):
    path: str
    in_ms: int = Field(ge=0)


class Clip(BaseModel):
    id: str
    start_frame: int = Field(ge=0)
    end_frame: int                 # exclusive
    source: Source
    score: float | None = None
    rationale: dict[str, float] = Field(default_factory=dict)

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    def source_out_ms(self, fps: int = TIMELINE_FPS) -> int:
        return self.source.in_ms + frame_to_ms(self.frames, fps)


class Provenance(BaseModel):
    generator: Literal["hand", "baseline-v0", "cutter-v0"] = "hand"
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class Timeline(BaseModel):
    schema_version: Literal["0.1"] = "0.1"
    settings: Settings = Field(default_factory=Settings)
    duration_frames: int = Field(gt=0)
    music: Music
    beats: list[Beat] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    # ---- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Timeline":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    # ---- convenience -------------------------------------------------------

    @property
    def duration_ms(self) -> int:
        return frame_to_ms(self.duration_frames, self.settings.fps)

    def sorted_clips(self) -> list[Clip]:
        return sorted(self.clips, key=lambda c: c.start_frame)


class Violation(BaseModel):
    code: Literal[
        "CONTIGUOUS", "MIN_LENGTH", "ON_BEAT", "SOURCE_RANGE",
        "NO_SOURCE_REUSE", "UNIQUE_IDS", "MUSIC_RANGE",
    ]
    clip_id: str | None = None
    detail: str

    def __str__(self) -> str:
        where = f" [{self.clip_id}]" if self.clip_id else ""
        return f"{self.code}{where}: {self.detail}"


def validate(
    tl: Timeline,
    source_durations_ms: dict[str, int] | None = None,
    music_duration_ms: int | None = None,
) -> list[Violation]:
    """Return every invariant violation. Empty list means the timeline is renderable.

    source_durations_ms and music_duration_ms are optional so a timeline can be
    checked structurally without probing media; pass them (from ffprobe) for the
    full check.
    """
    v: list[Violation] = []
    fps = tl.settings.fps
    clips = tl.sorted_clips()

    # UNIQUE_IDS
    ids = [c.id for c in clips]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        v.append(Violation(code="UNIQUE_IDS", clip_id=dup, detail="duplicate clip id"))

    # CONTIGUOUS
    if not clips:
        v.append(Violation(code="CONTIGUOUS", detail="timeline has no clips"))
    else:
        if clips[0].start_frame != 0:
            v.append(Violation(code="CONTIGUOUS", clip_id=clips[0].id,
                               detail=f"first clip starts at {clips[0].start_frame}, expected 0"))
        for prev, cur in zip(clips, clips[1:]):
            if cur.start_frame != prev.end_frame:
                kind = "gap" if cur.start_frame > prev.end_frame else "overlap"
                v.append(Violation(code="CONTIGUOUS", clip_id=cur.id,
                                   detail=f"{kind}: {prev.id} ends {prev.end_frame}, "
                                          f"{cur.id} starts {cur.start_frame}"))
        if clips[-1].end_frame != tl.duration_frames:
            v.append(Violation(code="CONTIGUOUS", clip_id=clips[-1].id,
                               detail=f"last clip ends {clips[-1].end_frame}, "
                                      f"expected duration_frames {tl.duration_frames}"))

    # MIN_LENGTH
    for c in clips:
        if c.frames < MIN_CLIP_FRAMES:
            v.append(Violation(code="MIN_LENGTH", clip_id=c.id,
                               detail=f"{c.frames} frames < {MIN_CLIP_FRAMES}"))

    # ON_BEAT (frame 0 is on the grid by construction)
    beat_frames = sorted(b.frame for b in tl.beats)
    if beat_frames:
        for c in clips:
            if c.start_frame == 0:
                continue
            nearest = min(beat_frames, key=lambda b: abs(b - c.start_frame))
            if abs(nearest - c.start_frame) > ON_BEAT_TOLERANCE_FRAMES:
                v.append(Violation(code="ON_BEAT", clip_id=c.id,
                                   detail=f"start {c.start_frame} is {abs(nearest - c.start_frame)} "
                                          f"frames from nearest beat {nearest}"))

    # SOURCE_RANGE
    if source_durations_ms is not None:
        for c in clips:
            dur = source_durations_ms.get(c.source.path)
            if dur is None:
                v.append(Violation(code="SOURCE_RANGE", clip_id=c.id,
                                   detail=f"unknown source {c.source.path}"))
            elif c.source_out_ms(fps) > dur:
                v.append(Violation(code="SOURCE_RANGE", clip_id=c.id,
                                   detail=f"source out {c.source_out_ms(fps)} ms > duration {dur} ms"))

    # NO_SOURCE_REUSE
    by_path: dict[str, list[Clip]] = {}
    for c in clips:
        by_path.setdefault(c.source.path, []).append(c)
    for path, group in by_path.items():
        group = sorted(group, key=lambda c: c.source.in_ms)
        for a, b in zip(group, group[1:]):
            if b.source.in_ms < a.source_out_ms(fps):
                v.append(Violation(code="NO_SOURCE_REUSE", clip_id=b.id,
                                   detail=f"overlaps {a.id} in {path}"))

    # MUSIC_RANGE
    if music_duration_ms is not None and tl.music.in_ms + tl.duration_ms > music_duration_ms:
        v.append(Violation(code="MUSIC_RANGE",
                           detail=f"window {tl.music.in_ms}+{tl.duration_ms} ms exceeds "
                                  f"track {music_duration_ms} ms"))

    return v


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m poc.timeline <timeline.json>", file=sys.stderr)
        return 2
    tl = Timeline.load(argv[1])
    problems = validate(tl)
    if problems:
        for p in problems:
            print(p)
        return 1
    print(f"OK  {len(tl.clips)} clips, {tl.duration_frames} frames "
          f"({tl.duration_ms / 1000:.2f} s) at {tl.settings.fps} fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

And the hand-written example that step 1 validates and step 2 renders. Replace the paths and in-points with real files; the beat frames here are a 120 BPM grid (one beat every 15 frames):

```json
{
  "schema_version": "0.1",
  "settings": {"fps": 30, "width": 1920, "height": 1080},
  "duration_frames": 240,
  "music": {"path": "music.mp3", "in_ms": 0, "gain_db": -1.0, "fade_out_ms": 1000},
  "beats": [
    {"frame": 0,   "downbeat": true,  "energy": 0.5},
    {"frame": 15,  "downbeat": false, "energy": 0.5},
    {"frame": 30,  "downbeat": false, "energy": 0.5},
    {"frame": 45,  "downbeat": false, "energy": 0.5},
    {"frame": 60,  "downbeat": true,  "energy": 0.6},
    {"frame": 75,  "downbeat": false, "energy": 0.6},
    {"frame": 90,  "downbeat": false, "energy": 0.6},
    {"frame": 105, "downbeat": false, "energy": 0.6},
    {"frame": 120, "downbeat": true,  "energy": 0.7},
    {"frame": 135, "downbeat": false, "energy": 0.7},
    {"frame": 150, "downbeat": false, "energy": 0.7},
    {"frame": 165, "downbeat": false, "energy": 0.7},
    {"frame": 180, "downbeat": true,  "energy": 0.8},
    {"frame": 195, "downbeat": false, "energy": 0.8},
    {"frame": 210, "downbeat": false, "energy": 0.8},
    {"frame": 225, "downbeat": false, "energy": 0.8}
  ],
  "clips": [
    {"id": "c001", "start_frame": 0,   "end_frame": 60,  "source": {"path": "footage/A.mp4", "in_ms": 2000}},
    {"id": "c002", "start_frame": 60,  "end_frame": 120, "source": {"path": "footage/B.mp4", "in_ms": 500}},
    {"id": "c003", "start_frame": 120, "end_frame": 180, "source": {"path": "footage/C.mp4", "in_ms": 4000}},
    {"id": "c004", "start_frame": 180, "end_frame": 210, "source": {"path": "footage/A.mp4", "in_ms": 9000}},
    {"id": "c005", "start_frame": 210, "end_frame": 240, "source": {"path": "footage/D.mp4", "in_ms": 1000}}
  ],
  "provenance": {"generator": "hand", "params": {}, "seed": null}
}
```

The second file, written the same day, is `render.py`, and its only job is to turn this JSON into an MP4 that ffprobe confirms has exactly 240 frames.

---

## Appendix: the cutter's scoring function, for reference during step 6

Written here so it is agreed before it is coded. Weights are the starting point, not the answer.

```
For slot s with length L frames and mean music energy e_s:
  target_motion = params.motion_base + params.motion_scale * e_s

For candidate (file f, in_ms i) with normalized curves over [i, i + L]:
  quality      = 0.6 * mean(sharpness_pct) + 0.4 * (1 - mean(clip_frac))         # 0..1
  motion_match = 1 - |mean(motion_pct) - target_motion|                            # 0..1
  reuse        = 1.0 if f was used in the previous slot else 0.5 if in the one before else 0
  coverage     = uses_so_far[f] / max(1, max(uses_so_far))                          # 0..1

  score = 0.35 * quality + 0.40 * motion_match - 0.15 * reuse - 0.10 * coverage
  (candidates overlapping an already-used source range are excluded, not penalized)

Pick the argmax per slot, in timeline order (greedy). Record the four terms as rationale.
```

Style table, v0:

| Param | energetic | calm |
|-------|-----------|------|
| beats per cut | 1 when `e >= 0.6`, else 2 | 4 when `e <= 0.5`, else 2 |
| motion_base, motion_scale | 0.5, 0.5 | 0.1, 0.3 |
| music window | *Not implemented in v0:* window starts at the first detected beat or `--music-offset-s` (Section 7). Original plan: highest-energy window on a downbeat | *Not implemented in v0* (same as energetic). Original plan: lowest-energy window on a downbeat |

Slot generation walks the beats from the window start, advancing by "beats per cut" evaluated at the current beat, and stops at the last beat at or before the requested duration. The timeline duration therefore snaps to a beat, which is what a human editor would do too.
