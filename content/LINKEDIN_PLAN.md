# LinkedIn Build-in-Public Plan

Written 2026-09-14 against the actual repository state. Nothing below claims
anything the repo cannot back up.

## What actually exists today (post only from this)

| Area | State | Evidence in repo |
|------|-------|------------------|
| Architecture | 22-section design, 10 core decisions, 3 layers (Perceive / Decide / Execute), versioned Timeline Document, operation grammar for revisions | `docs/ARCHITECTURE.md` (11.6k words) |
| Self-review | Keep / Change / Defer on 34 decisions; 3 licensing problems found (madmom models CC BY-NC-SA, InsightFace models non-commercial, Remotion company license); 4 wrong assumptions corrected | `docs/ARCHITECTURE_REVIEW.md` |
| MVP definition | Hypothesis split into 4 falsifiable claims; random baseline as control; 70% blind-preference threshold; LLM explicitly excluded from v0 with reasons | `docs/MVP.md` |
| Beat Flash PoC | 2 iterations, closed; raw librosa beats measured 18.8 to 23.2 ms late; refinement was a negative result; octave ambiguity found (166.7 vs 83.4 BPM) | `experiments/beat_flash/CONCLUSION.md` |
| Implementation | The Python CLI prototype, deterministic media pipeline, and P0 M1-M5 evaluation tooling are implemented. Evaluation reports check completeness, expected denominators, source artifacts, and version metadata. | Automated tests cover the code paths; check the current test run before quoting a count. |
| Integration bug | 250 ms candidate steps are not whole frames at common fps; fixed by snapping to source frame boundaries; found only when Steps 4 and 7 ran together | commit `f986573` |
| Evaluation protocol | Frozen before any footage was collected. The 2026-09-30 pre-collection tooling amendment preserves Sections 0-9; official generation requires a clean source tree and records its exact version. | `docs/EVALUATION_PROTOCOL.md` |
| GPU strategy | CPU-first; GPU is a future scaling layer behind a perception interface | `docs/GPU_INFRASTRUCTURE_STRATEGY.md` |
| First real output | 2026-09-09: 8 phone clips, 13 cuts, 11.3 s, every cut on a beat, no AI model involved | `project/output/cutter_final.mp4`, `timeline.json` with per-clip rationale |
| Not done | Preliminary diagnostic and official blind evaluations, five-rater collection, and any product UI/LLM/cloud services | |

Honest framing: the deterministic CLI and its P0 evaluation tooling exist;
the editing-quality hypothesis is **not yet validated** because no blind
evaluation data has been collected. Do not describe diagnostic data as an
official MVP result.

## Why a series, and the shape of it

One post compresses eight weeks of decisions into a paragraph nobody can act
on. A numbered series does three things a single post cannot: each post
carries one idea a reader can reuse, the numbering creates a reason to follow,
and the unresolved hypothesis gives the series an ending readers wait for.

- Name it. Use one recurring first line, for example `Build log #3: AI video editor`.
- Two posts per week, Tuesday and Thursday, 9 to 11 AM IST. Do not exceed two.
- Ten posts from existing material, about five weeks. Then the series
  continues at the pace of real progress, one post per real milestone.
- Post 1 gets pinned to the profile. Every later post links the previous one
  in the first comment, never in the body.

## Rules that protect the project

1. **No copyrighted audio in any video you upload.** The Chor and BYE flash
   videos must never be posted with sound. The synthetic 128 BPM flash video
   is fully yours and is the one to use. For the first real output, confirm
   the music track's license; if unsure, post it muted with a caption
   ("music removed for rights, cuts land on the beat grid shown below") or
   re-render with a royalty-free track.
2. **Never call v0 "AI".** There is no model in it yet, and that is the
   point. "Deterministic first, LLM later" is a stronger story than a vague
   AI claim, and it is true.
3. **No results before results.** Do not post the preference rate, a
   teaser of it, or "early signs" until the frozen protocol's collection is
   declared final. The protocol forbids interim reads; the posts must too.
4. **Failures are posts.** The refinement negative result and the frame
   alignment bug are two of the best posts in the series. If the evaluation
   fails, that is post 11 and it will be read more than a success.
5. **Faces in footage**: the WhatsApp clips may show people. Get consent or
   use frames without identifiable faces in any image you post.
6. **Decide once whether the repo goes public.** Footage, results, and
   `project/` are git-ignored; docs and code are clean. Public repo means
   every post can link to a file. If it stays private, the posts still
   work; put screenshots instead of links.

## The ten posts

Each post: hook in the first 200 characters (that is all LinkedIn shows
before "see more"), three to five short paragraphs, one concrete artifact,
one question at the end, three to five hashtags, no links in the body.

### 1. The framing post (pin this)

- **When**: this week.
- **Hook**: "I am building an AI video editor. Not templates. Not text-to-video. An editor that takes your raw footage, a song, and a sentence, and keeps revising the cut in conversation. Here is the first decision that shaped everything."
- **Idea**: automatic editing is a selection and arrangement problem with a subjective objective, followed by deterministic compilation. Not a generation problem. That framing decides where each technology belongs.
- **Body points**: the three hard sub-problems (perception, taste, revision); the Perceive / Decide / Execute split; "the deterministic CLI exists, but editing quality is not validated until the blind test is complete."
- **Asset**: a clean three-layer diagram. Render the mermaid block from `docs/ARCHITECTURE.md` Section 5.1 at mermaid.live and export PNG, or redraw it simpler with just the three layers and the Timeline in the middle.
- **Question**: "If you edit video, what is the one thing an automatic editor always gets wrong?"

### 2. Ten decisions before one line of code

- **When**: week 1, Thursday.
- **Hook**: "Ten architectural decisions I made before writing a single line of an AI video editor. Number 1: the language model never sees a pixel."
- **Idea**: Section 0 of the architecture, as a carousel. One decision per slide with its one-line "why".
- **Asset**: 11-slide PDF carousel (cover + 10). Text only, big type, consistent colours. LinkedIn carousels get the longest dwell time of any format.
- **Question**: "Which of these would you have decided differently?"

### 3. I reviewed my own architecture as a hostile critic

- **When**: week 2, Tuesday.
- **Hook**: "I asked for a hostile review of my own architecture. It found three licensing problems I would have shipped."
- **Idea**: the Keep / Change / Defer review. Lead with licensing because it is concrete and rarely discussed: madmom's models are non-commercial, InsightFace's models are non-commercial, Remotion needs a company license, Ultralytics YOLO is AGPL. Then the four wrong assumptions, especially "raw phone footage has no editorial cuts to detect", which inverted the whole perception design.
- **Asset**: screenshot of the licensing table from `docs/ARCHITECTURE_REVIEW.md`.
- **Question**: "What is the worst license surprise you have hit in an ML project?"

### 4. Writing down how the MVP could fail, before building it

- **When**: week 2, Thursday.
- **Hook**: "Before building the MVP I wrote down four ways it could fail. If it cannot fail on each one separately, a failure teaches nothing."
- **Idea**: the four claims (appropriate selection, musical sync, aesthetic usability, follows instruction), the random baseline on the same beat grid as the control, the 70% threshold, and why the LLM is excluded from v0 ("if the deterministic core cannot make a good edit from a perfect hand-written plan, no Director will save it").
- **Asset**: the claims table from `docs/MVP.md` as an image.
- **Question**: "Do you run a random baseline against your own systems? Honestly?"

### 5. The cheapest experiment first: the beat flash test

- **When**: week 3, Tuesday.
- **Hook**: "The riskiest assumption in a music-synced video editor is that the beat detector is right. I spent one evening testing it before anything else. It was 23 milliseconds wrong."
- **Idea**: synthetic drum track with known beat times, flash video, objective F-measure, then real music. Raw librosa beats land one analysis frame late (23.2 ms at the default hop, 18.8 ms at hop 256). At 30 fps that is most of a video frame. Fixed with a measured constant, not a magic number.
- **Asset**: the synthetic flash video `experiments/beat_flash/results/synth/synth_128_flash_click.mp4` (no copyright issue), plus the calibration table as an image.
- **Question**: "What is the smallest experiment that saved you the most time?"

### 6. Three things that failed in the beat test

- **When**: week 3, Thursday.
- **Hook**: "Negative results from my beat detection experiment. The 'improvement' that made real music worse, the tempo the algorithm cannot decide, and the beats it invents in silence."
- **Idea**: refinement improved synthetic drums but raised jitter on real songs by snapping to vocals and guitar (Chor CV 4.6% to 6.5%); a real track sits at 166.7 or 83.4 BPM and the tempogram cannot tell (94% vs 100%); the tracker emits a grid through silent intros and outros. Each became a rule for the MVP: never snap to transients, expose octave choice to the user, gate cuts by energy.
- **Asset**: the contact sheet `Chor_flash_contact.png` (no audio, no faces) or the 20-second window table from the diagnostics text.
- **Question**: "Do you publish your negative results internally? Where do they live?"

### 7. Closing an experiment properly

- **When**: week 4, Tuesday.
- **Hook**: "Most experiments do not end. They just stop. Here is the one-page closure I now write for every PoC: hypothesis, validated, failed, final algorithm, limitations, decisions, out of scope, handoff."
- **Idea**: the structure of `CONCLUSION.md`, the explicit out-of-scope list (lyrics, sections, drops, emotion, downbeats), and the "re-implement from the document, never import the PoC" rule. The music module in the MVP imports nothing from the experiment folder and says so in its docstring.
- **Asset**: screenshot of the Phase 02 Handoff section headings.
- **Question**: "What does 'done' mean for an experiment on your team?"

### 8. Steps 1 to 9 in one day, and the bug integration found

- **When**: week 4, Thursday.
- **Hook**: "The deterministic pipeline worked. P0 cleanup made its evaluation honest."
- **Idea**: build order by dependency (schema first, because it is the contract everyone shares); each step ends with something observable; the frame-alignment bug: 250 ms candidate steps are not a whole number of frames at 30 or 29.97 fps, so a frame-accurate seek could land mid-frame and lose a frame, visible only when the cutter and renderer ran together. Fix: snap candidates to the source file's own frame grid. The evaluation CLI now measures M1-M5, checks incomplete data and duplicates, and keeps preliminary results diagnostic.
- **Asset**: current git log screenshot and the `poc/` tree from the README. Refresh both before posting.
- **Question**: "What is the last bug you only found at integration?"

### 9. Pre-registration, for software

- **When**: week 5, Tuesday.
- **Hook**: "Before collecting a single clip of test footage, I froze the evaluation: commit hash, dataset size, raters, blinding, threshold, decision rule. The document is not allowed to change to fit the result."
- **Idea**: borrowed from clinical trials. 5 footage sets x 3 tracks x 2 moods = 30 blind pairs, 5 raters, 150 judgments, 70% threshold, Wilson interval, two iterations allowed then stop. Blinding is enforced by a unit test that scans the rater folder for the words "cutter" and "baseline". Failure handling is written before any failure exists.
- **Asset**: the metrics table (M1 to M5) as an image.
- **Question**: "Would you freeze your success criteria before seeing the first output?"

### 10. The first real output (no AI in it yet)

- **When**: week 5, Thursday.
- **Hook**: "First automatic edit from my own phone footage. 8 clips in, 13 cuts out, 11 seconds, every cut on a beat. There is no AI model in this. That is deliberate."
- **Idea**: what the deterministic cutter does (motion, sharpness, exposure percentiles, music energy, reuse and coverage penalties, greedy argmax) and what it does not know (faces, content, story). Show one clip's `rationale` from `timeline.json`. State plainly: this is the system that will be put against a random baseline in a blind test; the number is coming.
- **Asset**: the output video, muted or with cleared music; a screenshot of one clip's rationale JSON.
- **Question**: "Would you post this after small tweaks? That is literally one of my rater questions."

### 11 and onward (post when they happen, not before)

- **Eval results**: the preference rate and the decision the protocol forces, whatever it is. If it fails, the post is "the cheap-signal hypothesis failed, here is the breakdown by track and mood".
- **First iteration** (faces via YuNet, if the rule triggers).
- **GPU strategy**: "why my AI video editor has no GPU" fits anywhere as a filler in a slow week; it is a complete doc already.
- **First LLM Director** (v0.2): "the model's whole job is to emit one Pydantic object".
- **First natural-language revision** (v0.3): the operation grammar.

## Asset checklist (make these once, reuse)

| Asset | Source | How |
|-------|--------|-----|
| Three-layer diagram PNG | `docs/ARCHITECTURE.md` 5.1 mermaid | mermaid.live, export PNG at 2x, or redraw in Excalidraw with three boxes and the Timeline between |
| Ten decisions carousel PDF | `docs/ARCHITECTURE.md` Section 0 | Canva or Google Slides, 1080x1080, export PDF |
| Licensing table image | `docs/ARCHITECTURE_REVIEW.md` | screenshot at 2x zoom, crop |
| Four claims table image | `docs/MVP.md` top | screenshot |
| Synthetic beat flash video | `experiments/beat_flash/results/synth/synth_128_flash_click.mp4` | already exists; trim to 20 s if you want |
| Calibration table image | `experiments/beat_flash/README.md` | screenshot |
| Contact sheet | `experiments/beat_flash/results/real/Chor_flash_contact.png` | exists |
| Diagnostics window table | `experiments/beat_flash/results/real/*_diagnostics.txt` | screenshot of the table only, file name cropped out |
| Git log image | `git log --oneline` in a terminal | screenshot |
| Metrics table image | `docs/EVALUATION_PROTOCOL.md` Section 6 | screenshot |
| First output video | `project/output/cutter_final.mp4` | check music rights first; mute or re-render |
| Rationale snippet | `project/output/timeline.json`, one clip | screenshot, path field cropped |

## Post template

```
Build log #N: AI video editor

<hook: one or two sentences, a number or a surprise>

<what I did, 2 to 3 short paragraphs, one idea>

<what I learned or decided, 1 paragraph>

<one honest limitation or open question>

<question to the reader>

#BuildInPublic #VideoEditing #MachineLearning #SoftwareArchitecture #Python
```

First comment: link to the previous post in the series and, if the repo is
public, the file the post is about.
