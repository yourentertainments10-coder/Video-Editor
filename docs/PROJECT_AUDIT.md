# Project Audit: Architecture, Gaps, and Completion

Date: 2026-10-10. Scope: `README.md`, `todo.md`, and every file in `docs/`.
The Python source and tests were **not** part of this review (only the
documents were supplied), so status figures below come from what the
documents claim (`todo.md` dated 2026-09-30, 372 passing tests). Re-run
`pytest` and `python tools/check_offline.py` to confirm them.

## 1. Verdict in one paragraph

The core design is sound and matches how a reliable automatic editor should
be built: deterministic perception → structured plan → validated timeline →
FFmpeg render, with an honest evaluation gate. The engineering for MVP v0 is
essentially finished. Three things were wrong or missing in the documents:
(1) the target architecture depended on cloud AI models (Claude, Haiku,
hosted VLMs, LiteLLM, Clerk, S3), which contradicts the requirement that the
system is fully independent; (2) several documents disagreed with each other
or with the implementation; (3) the parts a real video editor needs beyond
hard cuts (media normalization, aspect handling, audio mixing and loudness,
text, export presets, progress/cancel, output verification) had no
specification. All three are fixed in the documents now; the code for (3) is
still to be written.

## 2. Completion

### 2.1 By milestone

| Milestone | Definition (todo.md §13) | Status | Estimate |
|---|---|---|---|
| MVP v0 engineering | 9 components of MVP.md §2 built and tested | All 9 present; P0 hardening done | **~95%** |
| MVP v0 validation | P1 blind evaluation run and decided | Not started; blocked on 5 footage sets, 3 tracks, 5 raters | **0%** |
| First usable offline editor | Import images/video/music, edit timeline, preview, constrained AI revision, undo, export MP4 | Schema, migration, assets, effect + transition catalogues exist as data only | **~20%** |
| Release ready | P8 hardening, licences, packaging | Not started | **0%** |

### 2.2 How the ~20% is calculated

Weights reflect effort to reach a first usable editor.

| Phase | Weight | Done | Contribution |
|---|---|---|---|
| P0 correctness and docs | 10 | 100% | 10.0 |
| P1 evaluation | 10 | 0% | 0 |
| P2 engine (timeline, assets, renderer, preview, jobs) | 30 | ~35% (P2.1–P2.5 of 8 steps; no mixed-media render yet) | 10.5 |
| P3 UI | 20 | 0% | 0 |
| P4 Local Director | 15 | 0% (design now in `LOCAL_DIRECTOR.md`) | 0 |
| P6 audio/captions (minimum: music + text) | 5 | 0% | 0 |
| P8 hardening | 10 | 0% | 0 |
| **Total** | 100 | | **~20%** |

## 3. Independence check (no external AI)

| Location | Problem | Fix applied |
|---|---|---|
| ARCHITECTURE.md §0, §5.1, §8, §11, §14, §17, §21, App. A | Director = Claude Opus 5, Reviser = Haiku 4.5, hosted VLM, cloud STT, LiteLLM, token cost model, `anthropic` routing config | Superseded banners added pointing to `OFFLINE_POLICY.md` and `LOCAL_DIRECTOR.md`; offline decision added as Decision 0; routing config and stack table rewritten for local components |
| ARCHITECTURE.md §17 | Clerk auth, S3/MinIO, Next.js, Remotion, madmom, InsightFace, SigLIP+LAION | Replaced with local equivalents and review verdicts |
| todo.md P4 | "optional Claude/GPT/other providers" | Removed; local-only with optional in-process llama.cpp |
| GPU_INFRASTRUCTURE_STRATEGY.md | Says local processing "is not the preferred architecture" while README says local-first; lists "External API Implementation" | Reconciliation note added: local-first wins; cloud GPU only if policy is amended |
| workflow.md | Lists Fable/Sonnet/Opus | Clarified: development assistants only, never runtime dependencies |
| Code | Unknown | New `tools/check_offline.py` + `tests/test_offline_policy.py` fail the build if a cloud AI SDK or remote URL appears |

The current v0 code, as described, already uses no external AI: librosa,
OpenCV, NumPy, Pydantic, FFmpeg only.

## 4. Contradictions found between documents

| # | Where | Contradiction | Resolution |
|---|---|---|---|
| 1 | ARCHITECTURE.md | Two sections numbered 5.2 | Deployment view renumbered 5.3 |
| 2 | ARCHITECTURE.md §12.2 notes vs review item 2 and P2.1 | "Everything is milliseconds in the timeline" vs integer ticks + rational fps | Note corrected to ticks |
| 3 | ARCHITECTURE.md §9, §10, §17 vs ARCHITECTURE_REVIEW.md | Still recommends TransNetV2-first, madmom, InsightFace, SigLIP+LAION, 540p proxies, xfade assembly, Next.js+HLS | Superseded markers added; stack table aligned with review |
| 4 | ARCHITECTURE.md FR-23 / §11 step 1 vs review item 20 | Up to three blocking clarifying questions vs ask only for duration/aspect | Marked superseded by review item 20 |
| 5 | MVP.md §1 | Says output is `out/timeline.json` and `out/out.mp4`; everything else says `final.mp4` beside `--output` | Fixed |
| 6 | MVP.md §11 | "working FFmpeg on PATH"; README says bundled `imageio-ffmpeg`, no PATH needed | Fixed |
| 7 | MVP.md §11 step 3 and Appendix style table | Music module does "downbeats, window"; style table picks "highest-energy window starting on a downbeat" — §7 says neither is implemented | Annotated as not implemented in v0 |
| 8 | MVP.md M1 vs EVALUATION_PROTOCOL.md §6 | MVP adds "under 45 s cached"; frozen protocol only says "under 2 minutes" | **Not edited** (protocol is frozen). Owner should decide; the frozen protocol governs the official result |
| 9 | EVALUATION_PROTOCOL §8 | Decision rule mentions "downbeat-aligned motion peaks", which v0 does not support | Already flagged in MVP.md; any use needs an amendment. Left unchanged |
| 10 | README | Mixed `.\.venv\Scripts\python.exe` and `.venv/Scripts/python` | Normalized to the PowerShell form |

## 5. Video editor capability matrix

Legend: **Done** = implemented and tested (per todo.md). **Data** = schema
exists, not rendered. **Spec** = specified in this review, no code.
**Missing** = not yet designed in detail.

| Area | Capability | Status | Where specified |
|---|---|---|---|
| Ingest | Probe, format checks, actionable errors | Done | MVP.md §5 |
| Ingest | Content hash, duplicate detection, relink | Done | P2.3 |
| Ingest | VFR → CFR | Done (render) / Spec (ingest) | RENDER_SPEC §2 |
| Ingest | Rotation, HDR→SDR, 10-bit, interlace, odd audio | Spec | RENDER_SPEC §2 |
| Ingest | Proxies and thumbnails/filmstrip | Spec | RENDER_SPEC §2 |
| Ingest | Still images / screenshots | Data | P2.3, RENDER_SPEC §2 |
| Analysis | Beats, per-beat energy | Done | MVP.md §7 |
| Analysis | Motion, sharpness, exposure | Done | MVP.md §6 |
| Analysis | Analysis cache | Done | P0 |
| Analysis | Faces (YuNet, local) | Missing | P1 decision rule |
| Analysis | Subject/saliency crop | Spec | RENDER_SPEC §3 |
| Analysis | Sections, drops, downbeats | Missing (deferred) | MVP.md §3 |
| Analysis | Speech-to-text (local) | Missing | todo P6 |
| Decide | Deterministic cutter + random baseline | Done | MVP.md §11 |
| Decide | Mood → parameters (style table) | Done (2 moods) | MVP.md App. |
| Decide | Brief parsing and revisions, offline | Spec | LOCAL_DIRECTOR.md |
| Timeline | v0.1 legacy schema + validator | Done | MVP.md §9 |
| Timeline | v1.0 editor schema, migration | Done (data) | P2.1, P2.2 |
| Timeline | Versions, operations, undo/redo | Missing | ARCHITECTURE §12.3/12.5 |
| Effects | 12 effects, 9 transitions catalogued | Data (non-renderable) | P2.4, P2.5 |
| Render | Hard cuts + music mux, 1080p | Done | MVP.md §11 |
| Render | Transitions, effects, Ken Burns, text | Spec | RENDER_SPEC §4 |
| Render | Aspect fit modes, 9:16 output | Spec | RENDER_SPEC §3 |
| Audio | Click-free cuts, ducking, −14 LUFS loudness | Spec | RENDER_SPEC §5 |
| Audio | Footage audio kept / voice-over | Missing | todo P6 |
| Export | Platform presets, BT.709 tagging | Spec | RENDER_SPEC §6 |
| Jobs | Progress, cancellation, temp cleanup, atomic output | Spec | RENDER_SPEC §7 |
| QA | Output verification (frames, A/V, black/freeze) | Partly done (M1) / Spec | RENDER_SPEC §8 |
| QA | Offline enforcement test | Done (new) | OFFLINE_POLICY §4 |
| UI | Asset library, player, timeline, revision panel | Missing | todo P3 |
| Release | Licence inventory (FFmpeg build, fonts, models) | Missing | todo P8 |

## 6. Recommended next steps (in order)

1. Run `pytest` and `python tools/check_offline.py` on the real repository;
   fix anything the checker flags.
2. Collect the P1 dataset (5 footage sets, 3 licensed tracks) and run the
   frozen evaluation. This is still the most important unknown: whether the
   cheap-signal cutter beats random selection.
3. In parallel, start P2.6 from `RENDER_SPEC.md` in this order: ingest
   normalization → aspect fit modes → fades/dissolve/dips → text → audio
   loudness → export presets → verification. Flip catalogue entries to
   `renderable: true` one at a time as golden tests pass.
4. P2.7/P2.8 progress, cancel, preview profile, then the P3 UI.
5. P4 Local Director with the golden sentence test file before any UI chat.
