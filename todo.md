
  # AI Video Editor — Developer TODO

This is the implementation backlog for the project described by the pasted product-video-editor document, cross-checked against the repository as it stood on 2026-09-30. It is written for a developer who will implement the work and report completion against the acceptance criteria below.

**Current status (2026-09-30):** P0 evaluation infrastructure is implemented. P1 official evaluation is blocked by missing real inputs. P2.1 schema expansion, P2.2 migration dispatch, P2.3 local asset management, P2.4 effects, and P2.5 transition catalogue are complete; the legacy cutter/renderer format remains unchanged. P2.6 and later have not started.

**Update (2026-10-10):** documentation audit complete (`docs/PROJECT_AUDIT.md`). The project is now bound by `docs/OFFLINE_POLICY.md`: no external AI API or cloud service, ever. Specifications added for the renderer (`docs/RENDER_SPEC.md`) and the offline Director (`docs/LOCAL_DIRECTOR.md`). Estimated completion: MVP v0 engineering ~95%, MVP v0 evaluation 0%, first usable offline editor ~20%.

## 1. Product target and scope

The repository currently contains a **Python command-line proof of concept** for cutting video clips to music. It is not yet a user-facing editor. The pasted document describes a much larger local-first product: import real screenshots/recordings, make an editable video plan, preview and revise it, and export a finished MP4.

Build in gates. Validate the current editing hypothesis before investing in the full editor. Keep the product's first useful promise narrow:

> Given real video footage, a music track, duration, and a simple creative instruction, create a deterministic, editable timeline, preview it, allow controlled revisions, and export MP4.

The first product release does **not** need generative video, AI music, avatars, lip-sync, multi-user collaboration, cloud rendering, or a permanently running GPU service.

### Recommended architecture for this repository

- **Python**: media analysis, timeline schema/validation, deterministic cutter, AI Director adapter, job orchestration, local API/CLI.
- **FFmpeg**: probing/decoding, trims, transforms, transitions, audio mixing, encoding, and final MP4. Keep it as the authoritative video renderer.
- **React + TypeScript + Vite**: the later editor UI, preview, asset browser, timeline, and prompt/revision panel. The repository has no frontend today. Prefer this lightweight SPA direction from `docs/ARCHITECTURE_REVIEW.md`; do not add Django or Next.js without a separately documented reason.
- **Director (AI) interface**: rule-based Local Director by default (`docs/LOCAL_DIRECTOR.md`); an optional local model may run in-process (llama.cpp) behind the same interface. No cloud or external AI provider is allowed (`docs/OFFLINE_POLICY.md`).
- **Embeddings, vision models, and GPU**: add only when evaluation or a concrete product requirement shows that motion/sharpness/exposure are insufficient. Start CPU-first. Do not build generative video as part of this editor MVP.
- **Remotion / TypeScript graphics**: defer. Use FFmpeg for the core footage edit. Consider Remotion only for a clearly scoped graphics/title subsystem after checking its current license and cost for the intended commercial use. The architecture review already flags this as a licensing decision.

The proposed Python / FFmpeg / React split is useful. The proposed "AI APIs / local models" and GPU branches are options, not prerequisites. The pasted document's React + Django + Remotion arrangement is not implemented here and should not be copied wholesale: use the repository's narrower architecture review as the implementation guide.

## 2. Current baseline (do not mistake this for product completion)

Already present in the repository:

- [x] Python package, dependencies, CLI, Pydantic timeline schema and cross-field validator.
- [x] FFmpeg renderer for a single video track, hard cuts, music mux, and MP4 output.
- [x] CPU music analysis and per-video motion/sharpness/exposure analysis.
- [x] Mood parameter table, beat-grid builder, deterministic cutter, and seeded random baseline.
- [x] End-to-end pipeline and partial evaluation tooling.
- [x] A local rendered sample: 13 cuts, 337 frames, about 11.23 seconds.
- [x] The full suite currently contains 372 tests; 372 pass in the latest verified workspace run. Keep this count tied to the latest test run, not as a release claim.
- [x] A frozen evaluation protocol exists. Its pinned code commit is `90749aa6ee86a570e70a8f2ccb4d34160e641f9c`; current HEAD adds the protocol document. Preserve the distinction between the frozen measured algorithm and subsequent changes.

Known status: the sample output proves the pipeline can produce a file. No blind evaluation result exists, so editing quality and product usefulness are **not validated**.

## 3. P0 — Make the current MVP honest, reproducible, and safe to evaluate

Do these before treating an evaluation report as evidence.

### 3.1 Fix evaluation correctness and completeness

- [x] **Do not report unmeasured metrics as passing.** Missing metrics return incomplete and cannot issue an official verdict.
- [x] **Wire all five metrics end to end.** M1–M5 generation, rater templates, typed CSV/JSONL ingestion, validation, CLI, and reporting are connected.
- [x] **Make incomplete collection visible.** Reports list missing responses by metric, require five declared raters, and distinguish incomplete, diagnostic-only, and official reports.
- [x] **Deduplicate against the existing rating log.** Raw imports remain append-only; first valid `(subject_id, rater_id)` is used by reports and duplicates are reported.
- [x] **Fix M4's denominator.** The denominator is the complete expected output set; missing responses remain visible.
- [x] **Verify M1 against actual artifacts.** M1 checks expected manifests, timelines, source hashes/bounds, render streams/duration, beat alignment, and render-time budget.
- [x] **Keep the protocol auditable.** Frozen Sections 0-9 are unchanged; the pre-collection P0 tooling amendment is recorded in Section 11. Official generation requires a clean source/protocol tree and manifests retain version fingerprints.

**Acceptance:** a deliberately incomplete set of ratings cannot produce a full-pass verdict; duplicate CSV ingestion cannot inflate scores; a complete generated sample dataset can collect M2–M5 ratings and produce a report that lists all missing data, denominators, thresholds, and the final rule applied.

### 3.2 Finish the explicitly promised MVP mechanics

- [x] **Analysis cache.** Content SHA-256, analyzer version, and effective parameters key the per-user JSON cache; location and invalidation are documented.
- [x] **Contact sheet.** Pipeline and evaluation renders produce one sampled frame per clip with timing, source, score, and rationale.
- [x] **Input constraints.** CLI validates duration, file counts/formats, per-clip duration, total footage, and music headroom; development bypass is explicit.
- [x] **CLI/documentation alignment.** `--output` and `--out` name a video file; README and MVP examples use that contract. Evaluation phases and rating instruments are documented.
- [x] **Update README status.** The README describes the current CLI prototype and explicitly states that quality remains unvalidated.
- [x] **Clarify image support.** The v0 prototype is explicitly video-only; still-image scenes remain future editor scope.
- [x] **Make path and media errors actionable.** Input probes, stream checks, supported formats, and cross-drive path errors are handled before analysis/rendering.
- [x] **Pin and record reproducibility inputs.** Manifests capture software/analyzer versions, parameters, seeds, hashes, commit, fingerprint, phase, and worktree state; matched M2 pairs share analyzed objects and renderer.

**Acceptance:** requirements and commands in README/MVP match real CLI behavior; all declared constraints have executable checks; repeated runs reuse valid analysis; each successful run produces timeline, MP4, analysis references, and contact sheet, or clearly documents the deferred artifact.

### 3.3a Offline enforcement (added 2026-10-10)

- [x] Write the binding offline policy (`docs/OFFLINE_POLICY.md`).
- [x] Add a static checker for forbidden AI/cloud SDKs, dependencies, and remote URLs (`tools/check_offline.py`, `tests/test_offline_policy.py`). Run it against the real repository and fix any findings.
- [ ] Add a runtime socket guard fixture around the end-to-end pipeline test (refuse non-loopback connects).
- [ ] Add `models/registry.json` (id, file, sha256, licence, size) and verify before any model load (needed before YuNet faces).

### 3.3 Reconcile project documentation before expanding scope

- [x] **Resolve beat/downbeat conflict.** MVP docs now state that v0 has no assumed downbeats; experimental evidence remains authoritative. Frozen decision rules are preserved and any future use requires an amendment.
- [x] **Separate implemented architecture from aspirational architecture.** Architecture docs are labeled target design; README lists what exists.
- [x] **Clarify the target application.** Current assumption is a standalone local-first tool; no CarTrends integration is claimed.
- [x] **Keep `content/LINKEDIN_PLAN.md` accurate.** Update planned posts to distinguish completed P0 tooling from the still-unrun blind evaluation.

**Acceptance:** there is one current source of truth for v0 scope, evaluation, and the next product phase. Historical experiment results remain intact.

## 4. P1 — Complete the MVP blind evaluation

Do not proceed to expensive vision/GPU or full-app work until this gate is resolved, unless a deliberate product decision records why.

- [ ] Prepare **five varied footage sets**, each with 8–15 eligible video clips and 3–10 minutes total footage. Cover people, landscape/travel, product/object, event, and mixed content. The existing local sample is one small set, not the five-set evaluation dataset.
- [ ] Prepare **three full music tracks** with different tempos/energy profiles. Run the beat-flash check and record tempo ambiguity, quiet regions, and licensing/usage rights. The sample timeline references a CapCut Beat Sync track; do not publish its audio until rights are confirmed.
- [ ] Generate the planned grid: 5 footage sets × 3 tracks × 2 moods = 30 matched pairs, 60 MP4 outputs, plus the M3 mood-identification materials. Confirm M2 pairs share the same grid and differ only in selection.
- [ ] Check all 60 outputs for render success, beat alignment, duration, and playback before giving blind assets to raters. Keep generator identity and timelines out of rater materials.
- [ ] Recruit five independent raters who did not tune the cutter. Collect all required questions without exposing the answer key or generator identity.
- [ ] Run the fixed M1–M5 analysis and preserve the final report, manifests, answer key, rater inputs, code commit, and decision. Follow the protocol's iteration limit and its rule for versioning/regenerating after changes.
- [ ] Make the documented go/no-go decision. If the cheap signals fail, add face detection or semantic vision only as the protocol permits; if the MVP gate passes, move to the editor product phase.

**Acceptance:** one reproducible, complete result exists for the frozen/versioned algorithm and the documented decision rule has been applied. A visually appealing sample alone does not pass this gate.

## 5. P2 — Define and build the editable local video engine

P2 is intentionally proceeding before the P1 gate by explicit product decision. Keep editing-intelligence changes separate from evaluation work.

### 5.1 Timeline contract

- [x] **P2.1: Add a versioned editor timeline data model.** `EditorTimeline` v1.0 is separate from the v0.1 `Timeline` consumed by the cutter and renderer.
- [x] **P2.1: Define stable IDs and media structure.** Project/timeline, assets, layers, tracks, clips/scenes, and markers have stable IDs; video/image/audio/music/text assets are represented.
- [x] **P2.1: Define edit properties.** Integer timeline ticks/timebase, rational output fps, source in/out, duration, crop, scale, position, opacity, audio, and typed transition/effect/animation references are modeled.
- [x] **P2.1: Add strict structural validation.** Duplicate IDs, missing asset/track/layer references, invalid source bounds, timeline bounds, media-kind mismatches, and invalid effect/transition identifiers are rejected/reported.
- [x] **P2.1: Add legacy conversion.** Explicit v0.1-to-v1.0 conversion retains clip IDs, timing, source ranges, music, beats, provenance, scores, and rationale. The v0.1 format remains loadable and unchanged.
- [x] **P2.2: Formalize schema migrations.** `migrate_timeline` dispatches only supported v0.1 and v1.0 inputs, canonicalizes v1.0, rejects unknown/older/future schemas without downgrading, and documents the extension policy. No persistence history framework is introduced.
- [x] **P2.3: Asset management.** Implemented `ProjectAssetLibrary` with external-source imports, content-derived stable identity, SHA-256 duplicate detection, media probing/metadata, configurable limits, project save/load, missing-source checks, verified relinking, timeline asset reference checks, and analysis-cache hash reuse. Originals are not copied or modified; backups must include the manifest and all external media.
- [x] **P2.4: Effect catalogue.** Added 12 ordered allowlisted IDs, strict typed per-effect parameter schemas/defaults/ranges, deterministic contracts, and renderability metadata. All remain non-renderable; no FFmpeg strings or rendering implementation are accepted.
- [x] **P2.5: Transition catalogue.** Added nine ordered allowlisted IDs, typed parameter schemas/defaults/ranges, deterministic contracts, and renderability metadata. References attach to incoming clip boundaries and require an adjacent prior clip; duration is bounded against both clips. All remain non-renderable; no FFmpeg strings or renderer implementation are accepted.
- [ ] **P2.6: Mixed-media renderer.** Implement `docs/RENDER_SPEC.md` in this order, flipping catalogue entries to `renderable: true` only when their golden test passes:
  - [ ] Renderer interface (`compile` pure, `render` with progress/cancel) — §1
  - [ ] Ingest normalization: rotation, VFR, HDR→SDR, 10-bit, odd sizes/SAR, interlace, audio resample, Unicode/long paths — §2
  - [ ] 720p proxies and thumbnails/filmstrip — §2
  - [ ] Still images (loop, EXIF rotation) and Ken Burns via crop expressions — §2, §4
  - [ ] Aspect fit modes: cover, contain_blur, subject_crop; vertical safe-area warnings — §3
  - [ ] Transitions (fade, dissolve with fixed seed, dips, wipes) as pre-rendered two-clip segments — §4
  - [ ] Effects (fades, zoom/pan, eq, blur) — §4
  - [ ] Text via libass with bundled OFL fonts — §4
  - [ ] Audio: click-free cut fades, ducking, −14 LUFS loudnorm, exact A/V length — §5
  - [ ] Export profiles with BT.709 tagging and faststart — §6
  - [ ] Post-render verification (frames, A/V, black/freeze detect) and synthetic golden corpus — §8
- [ ] **P2.7/P2.8: Preview, progress, cancellation, end-to-end editor engine.**
- [ ] AI output will eventually use typed timeline operations, never raw FFmpeg. The operation API itself is deferred until its own planned step.
- [ ] Preserve deterministic rendering semantics: same timeline and source versions must produce the same cut points/timing; do not promise byte-identical files unless proven.

**P2.1 acceptance:** editor timeline data serializes/deserializes, validates references and ranges, migrates from v0.1, and leaves the legacy renderer/cutter format operational. Human editing and rendering are later P2 steps.

### 5.2 Rendering and media handling

- [ ] Extend the FFmpeg renderer to still images and mixed image/video timelines, including aspect-ratio handling, crop/scale, pan/zoom, title overlays, fades/cuts, audio mixing, and final MP4 export.
- [ ] Support vertical and landscape output explicitly. Include at least a usable static subject-centered crop for vertical output; defer automatic tracking/reframing until evaluated.
- [ ] Keep FFmpeg responsible for video decode/encode, trims, transitions, audio, and final mux. Add a renderer interface so future backends can be evaluated without coupling timeline semantics to one engine.
- [ ] Add job progress, cancellation, clear render errors, safe temporary-file cleanup, output validation, and a low-resolution preview render path.
- [ ] Decide whether still-image animation and title templates can be done with FFmpeg filters. Add a Remotion graphics worker only if the visual requirement justifies its extra runtime and license.
- [ ] Document FFmpeg build/licensing implications for the intended distribution model before packaging a desktop binary or selling hosted rendering.

**Acceptance:** mixed still/video projects preview and export to valid MP4 at supported aspect ratios with correct audio duration, no missing frames at cuts, and visible render status/errors.

### 5.3 Project and asset management

- [ ] Add create/open/save project flows and an asset library with thumbnails, metadata, duration, dimensions, and source path/hash.
- [ ] Implement import for screenshots, images, video, and audio with type/size validation, duplicate detection, and relinking of moved files.
- [ ] Keep project data local by default, with a documented storage layout and backup/export behavior. Use content hashes for analysis reuse.
- [ ] Ensure analysis and rendering do not modify source media. Handle Unicode and long Windows paths; test the supported Windows environment.

**Acceptance:** a user can import assets, close/reopen the project, relink a moved asset, and render without re-importing or losing timeline edits.

## 6. P3 — Add the editor interface

- [ ] Build a React + TypeScript + Vite UI unless the product owner chooses a different host. Keep frontend code separate from Python media/AI logic behind a small local API.
- [ ] Add the main workspace: asset upload/library, prompt/settings panel, storyboard or scene list, preview player, editable timeline, render/export controls, and clear error/progress states.
- [ ] Support project settings such as target duration, aspect ratio, output resolution, mood/style, voice/music preference, and selected assets.
- [ ] Allow direct manual edits to scene order, duration, crop/scale, text, transition, and audio levels. The UI must remain useful without AI.
- [ ] Provide undo/redo or a revision history before introducing conversational editing; save revisions without destroying earlier versions.
- [ ] Make preview and final export use the same timeline semantics so a previewed result does not materially differ from the render.

**Acceptance:** a user unfamiliar with the CLI can import media, make or adjust a timeline, preview it, export an MP4, and recover the project after restart.

## 7. P4 — Add a constrained AI Director and natural-language revisions

- [ ] Define typed Pydantic request/response schemas for an edit plan and for revision operations. Return structured JSON, validate it, and reject unknown fields/actions.
- [ ] Give the Director compact symbolic asset metadata and user instructions. Do not send source pixels to a text-only model. Add image/vision access as a separate, explicit component only when needed.
- [ ] Implement an allowlist such as `add_scene`, `remove_scene`, `move_scene`, `set_duration`, `set_crop`, `set_zoom`, `set_transition`, `add_text`, `add_voiceover`, `add_music`, and `set_volume`, mapped to validated timeline edits.
- [ ] Apply revisions as diffs/operations against a project version, preserve the original, explain what changed, and support undo/revert. Resolve ambiguous references conservatively; ask only when required information is genuinely missing.
- [ ] Implement the rule-based Local Director (`docs/LOCAL_DIRECTOR.md`): intents file, style lexicon as data, reference resolver, diff explainer, 100+100 golden sentence tests.
- [ ] Optional later: in-process local LLM (llama.cpp, registered GGUF file) behind the same interface, off by default, falling back to rules. No hosted providers.
- [ ] Add prompt/schema regression cases and failure handling for malformed output, unavailable model, timeout, unsupported request, and model hallucinated asset IDs.
- [ ] Keep deterministic rendering separate from AI planning: the model proposes; validated code decides whether an operation is permitted; FFmpeg renders.

**Acceptance:** example requests (“make the dashboard bigger”, “shorten the intro”, “use a softer transition”) produce valid, reviewable timeline changes, preserve undo, and never cause unrestricted command execution.

## 8. P5 — Add automatic website/product capture (optional product module)

Only build this if automatic capture is part of the chosen product release.

- [ ] Implement Playwright capture as a separate opt-in worker: URL, explicit browser session, approved pages/workflow, viewport, waits, screenshot/recording outputs.
- [ ] Prefer a user-signed-in browser session or dedicated demo account. Never store production passwords in project JSON, prompts, logs, or model context.
- [ ] Restrict navigation to user-approved origins and prevent local-network/metadata endpoint access; treat URL capture as an SSRF/security boundary.
- [ ] Let users inspect captured screenshots/recordings and remove/redact sensitive data before using them in a video.
- [ ] Store capture provenance (URL, viewport, timestamp, steps) without storing secrets.

**Acceptance:** capture is user-authorized, limited to approved origins, produces reviewable assets, and does not expose credentials or private data in logs or exports.

## 9. P6 — Voice, captions, and music (optional staged features)

- [ ] Add a local TTS interface for narration only after the core image/video editor works. Let users review/edit the script and choose from available local voices.
- [ ] Add local speech-to-text/Whisper for word or segment timestamps when captions are required. Keep generated captions editable and synchronized after timeline changes.
- [ ] Support uploaded voice/audio and a licensed local music library. Add track selection, volume, fades, and voice/music ducking.
- [ ] Store rights/provenance metadata for music, fonts, and imported assets. Do not ship or publish copyrighted sample tracks without permission.
- [ ] Keep AI music generation and voice cloning out of the initial editor scope; require explicit consent and a separate product decision before adding voice cloning.

**Acceptance:** a user can add/edit narration, produce timed captions, mix voice and licensed music, and re-render with synchronization preserved.

## 10. P7 — Vision, embeddings, and GPU (only when evidence justifies them)

- [ ] Use the MVP evaluation and real editing feedback to identify which decisions motion/sharpness/exposure cannot make.
- [ ] Add face detection first only if it addresses a measured failure mode. Prefer permissively licensed models such as the alternatives recorded in `docs/ARCHITECTURE_REVIEW.md`; verify model-weight licenses separately from library licenses.
- [ ] Add embeddings/zero-shot tags for content retrieval only when users need semantic selection (for example, “show the dog” or “the CRM dashboard”). Use a model whose embeddings match any chosen scoring head.
- [ ] Add a VLM only when fixed tags/embeddings cannot satisfy a demonstrated requirement. Keep visual understanding optional and behind an interface.
- [ ] Benchmark model quality, latency, memory, licensing, and CPU fallback on actual media. Use GPU workers on demand only after workloads justify them; do not buy/provision a GPU in advance.
- [ ] Keep generative video as a separate future feature and budget. It is not necessary for product-screen editing and is not part of “AI-directed editing”.

**Acceptance:** every added model has a recorded user need, evaluation result, reproducible model/version, licensing review, resource budget, and fallback/error behavior.

## 11. P8 — Product hardening and release readiness

- [ ] Distribution target is local (desktop or loopback-only local web app) per `docs/OFFLINE_POLICY.md`. A hosted service or CarTrends integration would require a dated policy amendment first.
- [ ] If local-only, bind the API to loopback, restrict file access to approved project roots, and document installation/update/uninstall. If hosted, add authentication, tenant isolation, quotas, job queue, storage, backups, monitoring, and deletion policy before accepting user media.
- [ ] Add input limits, safe filenames, path traversal protection, temporary file cleanup, dependency/model license inventory, and secret-free logs.
- [ ] Make render jobs recoverable after errors and crashes; preserve project/timeline state even if export fails.
- [ ] Document supported media formats, maximum resolutions/durations, CPU/RAM needs, model downloads, offline behavior, and known limitations.
- [ ] Run automated tests and manual end-to-end checks on the supported Windows setup and at least one clean install environment. Verify sample output playback and audio/video sync.
- [ ] Update README, architecture diagrams, setup instructions, and release notes to match shipped behavior. Remove obsolete claims and examples.

**Acceptance:** a new developer can install from documented instructions, create a project, import media, generate/edit/preview/export, and diagnose failures without relying on undocumented local files.

## 12. Suggested build order and gates

1. **P0 correctness and docs** — fix evaluation verdict/ingestion errors; align documented CLI and requirements; mark absent functionality accurately.
2. **P1 evaluation** — generate the complete blind dataset, collect ratings, record the result, and follow the go/no-go rule.
3. **P2 timeline/render/assets** — build a reliable mixed-media, editable local engine.
4. **P3 UI** — make manual editing usable before adding the Director.
5. **P4 AI Director** — add validated planning and revision operations over the editor's timeline.
6. **P5/P6 optional capture and audio** — build only the modules included in the product decision.
7. **P7 model/GPU upgrades** — respond to measured quality needs.
8. **P8 release hardening** — secure, document, package, and verify the chosen distribution.

Do not mark a phase complete because code exists. Mark it complete when its acceptance criteria are met and the evidence (report, artifact, or documented manual check) is linked in the project.

## 13. Final definition of “ready”

The project is ready for the **validated editing MVP** when P0 and P1 pass and the recorded evaluation decision supports the next step.

The project is ready as the **first usable AI video editor** when a user can import images/videos/music, create and manually edit a structured timeline, preview, request a constrained AI plan/revision, undo it, and export a correctly synchronized MP4 without any external API or network connection.

The project is ready for **commercial or hosted release** only after the applicable license, privacy, security, storage, deployment, and support items in P8 are complete. Those release tasks are separate from proving the local editing product works.
