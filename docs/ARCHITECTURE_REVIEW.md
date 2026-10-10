# Architecture Decision Review (v0.1 of ARCHITECTURE.md)

Date: 2026-09-05. Reviewer stance: Senior AI Systems Architect, pre-implementation. Verdicts: Keep / Change / Defer.

> **Note (2026-10-10):** item 8's "thin adapter over two official SDKs",
> item 21's frontier-model effort settings, and the "Vendor lock-in risks"
> section assumed hosted LLMs. Under `OFFLINE_POLICY.md` there are no hosted
> providers; the Director is the rule-based `LOCAL_DIRECTOR.md`. All other
> verdicts stand and override `ARCHITECTURE.md`.

## Summary verdict

The three-layer split, the symbolic reasoning boundary, the versioned timeline, and the deterministic Cutter are sound and should be kept. The document over-specifies infrastructure for an MVP (workflow engine, four worker types, MinIO, Remotion, Next.js with HLS, two-model cascade), carries three licensing problems (madmom models, InsightFace models, Remotion), and rests on four technically wrong assumptions (raw footage has editorial cuts to detect; the LAION aesthetic head works on SigLIP embeddings; face-box size classifies shot scale for all footage; vertical output can defer reframing). Fix those and the design is ready to build.

## Core decisions

| # | Decision | Verdict | Reasoning and action |
|---|----------|---------|----------------------|
| 1 | LLMs never see pixels; reason over symbolic index | **Keep** | Strongest decision in the doc. Keep the Critic (images) optional and out of MVP. |
| 2 | Versioned Timeline Document as source of truth | **Keep, change time base** | Milliseconds plus rounding at compile time causes off-by-one drift and ambiguous equality checks on grid snapping. Store frame indices at a fixed timeline fps (or rational time as OpenTimelineIO does). Display in ms. Makes OTIO export trivial later. |
| 3 | Perceive / Decide / Execute layers | **Keep** | |
| 4 | Director plans, deterministic Cutter picks clips | **Keep** | Start with greedy selection plus one-step lookahead. Beam search only if greedy output shows repetition or dead ends. |
| 5 | Perception cached per asset by content hash | **Keep** | |
| 6 | Two-tier rendering with per-segment cache | **Keep, change assembly** | A 60-input `xfade` filter graph is slow and memory-heavy. Pre-render transitions as tiny two-clip segments; join hard cuts with the concat demuxer (stream copy, no re-encode); mux audio last. Assembly drops to about a second. |
| 7 | Music defines the cut grid | **Keep, change analyzer** | madmom is effectively unmaintained (breaks on current Python and NumPy) and its pretrained models are CC BY-NC-SA, so commercial use is not permitted. Use allin1 (MIT) for beats, downbeats, and sections in one pass, librosa novelty as fallback. Verify allin1 installs on your machine first; its `natten` dependency is fragile. |
| 8 | Model roles behind interfaces, routing by config | **Keep interfaces, change model plan** | Start with one model at two effort levels, not a Director plus Reviser cascade. A cascade splits prompt-cache namespaces and doubles prompt maintenance before there is data to justify it. Add the cheap tier when Reviser call volume shows the saving. Drop LiteLLM; a thin adapter over two official SDKs is less surface area. |
| 9 | Revisions as validated operations | **Keep, shrink** | MVP operation set: `replace_clip`, `trim_clip`, `set_speed` (constant), `retime_section`, `set_transition` / `remove_transition`, `set_grade`, `regenerate_section`, plus `lock` and revert. Defer `set_scale_preference`, `set_transition_policy`, `reorder_clips`, `set_music_window`, `set_duration`, and caption ops. |
| 10 | Python + FFmpeg backend, TypeScript UI, Remotion graphics | **Keep Python/FFmpeg, defer Remotion** | Remotion needs a paid company license once the company exceeds three people or the revenue threshold. libass captions cover MVP. Revisit Remotion for v1 with the license budgeted. |

## Perception decisions

| # | Decision | Verdict | Reasoning and action |
|---|----------|---------|----------------------|
| 11 | TransNetV2 shot detection as the primary segmentation step | **Change** | Wrong assumption. Raw footage is mostly continuous takes with almost no editorial cuts. The real problem is splitting long takes into meaningful sub-shots. Make motion-curve valleys, embedding drift, and fixed 3 to 6 s windows the primary segmenter; PySceneDetect's adaptive detector handles the rare true cuts. TransNetV2 deferred. |
| 12 | Shot scale from face or person box height | **Change** | Fails on products, food, landscapes, animals. Add CLIP zero-shot classification of scale (prompted with "extreme close-up of", "wide shot of") for shots without a detected person. |
| 13 | SigLIP embeddings plus LAION aesthetic predictor | **Change** | Incompatible. The LAION aesthetic head is trained on OpenCLIP ViT-L/14 embeddings, not SigLIP. Use OpenCLIP ViT-L/14 for embeddings, zero-shot tags, scale, and aesthetics together, or drop aesthetic scoring in MVP and rely on sharpness and exposure. |
| 14 | VLM descriptions per shot in MVP | **Defer** | CLIP zero-shot tagging against a fixed vocabulary (subjects, settings, moods) gives most of the first-cut value at a fraction of GPU time. Add the VLM when content references ("the shot of the dog") and richer editor's notes need it. |
| 15 | RT-DETR object detection in MVP | **Defer** | With CLIP tags, only a person detector is needed for scale, and the face detector covers that. |
| 16 | InsightFace SCRFD for faces | **Change** | InsightFace pretrained models are licensed for non-commercial research only. Use OpenCV YuNet or MediaPipe Face Detector (both Apache 2.0). |
| 17 | 540p proxy for analysis and preview | **Change to 720p** | Small faces vanish at 540p. One 720p proxy serves both analysis and preview. |
| 18 | Optical flow (Farneback) at 4 to 8 fps | **Keep** | Cheap on CPU. Frame differencing is an acceptable first pass if flow is slow. |
| 19 | Footage audio analysis, transcription | **Defer** | Already deferred; correct. |

## Agent and revision decisions

| # | Decision | Verdict | Reasoning and action |
|---|----------|---------|----------------------|
| 20 | Up to three clarifying questions before the first cut | **Change** | Blocking questions add friction and hide the product. Generate immediately with stated assumptions; the revision loop is the clarification mechanism. Ask only if duration or aspect is missing. |
| 21 | Separate brief-parsing and planning calls | **Change** | Three sequential frontier-model calls with adaptive thinking can consume the two-minute first-cut budget alone. Merge brief parsing and planning into one structured call, stream it, run at effort `medium`, and measure before raising. |
| 22 | Self-critique pass with contact sheets | **Defer** | Low signal, hard to evaluate, adds cost and latency. Revisit after the human-rated eval harness exists. |
| 23 | Hybrid reference resolver (code plus model) | **Keep, simplify** | Code parses times and marker names. Everything else goes to the model with the compact timeline. Full semantic resolution against the index waits for the VLM. |
| 24 | Style lexicon mapping "cinematic" to levers | **Keep** | This is the product's taste and it is cheap. Store it as data, not as prompt prose, so it can be tuned without prompt changes. |
| 25 | Lightweight per-project preference learning | **Defer** | |
| 26 | Determinism guarantee | **Change wording** | Multi-threaded FFmpeg encodes are not byte-identical. Guarantee identical cut points and timing, not identical files. |

## Infrastructure decisions

| # | Decision | Verdict | Reasoning and action |
|---|----------|---------|----------------------|
| 27 | Temporal or Dramatiq, four worker deployables | **Change** | One worker process and a Postgres job table (or Dramatiq on Redis if you want retries for free). Temporal and per-queue deployables when there are paying users. |
| 28 | Postgres + pgvector + Redis + MinIO | **Change** | Postgres with JSONB: yes. pgvector: fine, but brute-force NumPy search is enough under about 50k shots. MinIO: defer; local filesystem behind a `Storage` interface. Redis only if Dramatiq is used. |
| 29 | Next.js, HLS playback, Konva timeline | **Change** | Plain Vite React SPA. Progressive MP4 with `faststart`, not HLS. Thumbnail strip with plain DOM. CLI first, UI at Phase 8 as planned. |
| 30 | Clerk auth, multi-tenancy, workspaces | **Defer** | Single-user MVP. |
| 31 | Aspect ratio handling deferred to v1 | **Change (gap)** | Vertical reels from landscape footage are the primary use case. Without at least a face-centered static crop, 9:16 output from 16:9 footage is broken. Add static subject-centered crop to MVP; tracking reframe stays in v1. |
| 32 | 28-week roadmap to beta | **Change expectations** | Realistic only for three to four engineers. Solo, expect double. Phase 4 (blind reviewers rate the deterministic cut usable) is the real gate; everything after is contingent on passing it. |
| 33 | Cost model | **Keep** | Directionally right. Note the Director cost falls further once brief parsing and planning are merged. |
| 34 | Windows development | **Add** | allin1, TransNetV2, and CUDA-torch are painful on native Windows. Develop the perception stack in WSL2 with Docker and the NVIDIA container toolkit, and make CPU fallbacks the default dev path. |

## Licensing issues found

| Component | Problem | Action |
|-----------|---------|--------|
| madmom pretrained models | CC BY-NC-SA 4.0, non-commercial | Replace with allin1 (MIT) plus librosa |
| InsightFace pretrained models | Non-commercial research only | Replace with YuNet or MediaPipe (Apache 2.0) |
| Remotion | Company license required above three employees or revenue threshold | Defer; budget for v1 |
| BeatNet (listed as alternative) | License is restrictive; verify before use | Do not adopt without checking |
| Ultralytics YOLO | AGPL | Already avoided; correct |
| FFmpeg with libx264 | GPL build | Fine for a server-side service; matters only if a desktop binary is shipped |
| Caption fonts | Varies | Use OFL-licensed fonts only |
| Qwen2.5-VL | 7B is Apache 2.0; 3B and 72B carry different licenses | Stay on 7B if adopted |
| LUT packs | Usually not redistributable | Author or license your own |

## Vendor lock-in risks

- Provider-specific API features (effort levels, structured output config, prompt caching) must live only inside the adapter. Prompts should be plain instructions plus a JSON schema, portable to OpenAI and Gemini. Keep a per-provider prompt test in the eval harness.
- Do not design any flow whose cost is acceptable only with one vendor's caching discount.
- S3 API, container-based GPU providers, and self-hostable observability are portable; no change.
- Auth provider lock-in is moot while auth is deferred.

## The resulting MVP shape

One Python service and one worker process. Postgres with JSONB. Local filesystem storage behind an interface. FFmpeg with concat-demuxer assembly and pre-rendered transitions. allin1 plus librosa for music. Motion-based sub-shot splitting plus PySceneDetect. OpenCLIP ViT-L/14 for embeddings, zero-shot tags, scale, and aesthetics. YuNet for faces. One LLM at two effort levels behind a thin adapter. Seven operations plus lock and revert. libass captions. Face-centered static crop for vertical output. CLI first, Vite SPA later.
