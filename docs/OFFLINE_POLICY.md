# Offline and Independence Policy

Version 1.0, 2026-10-10. Status: **binding product constraint.** Where any
other document disagrees with this one, this one wins. `ARCHITECTURE.md`
sections that name hosted models or cloud services are marked superseded.

## 1. The rule

> The editor runs completely on the user's machine. It never calls an
> external AI service, never sends media, metadata, prompts, or telemetry
> off the machine, and keeps working with the network cable unplugged.

This is a product decision, not a temporary MVP shortcut. There is no
"optional cloud provider" path in the shipped product.

## 2. What is allowed and what is not

| Category | Allowed | Not allowed |
|---|---|---|
| Media processing | FFmpeg (bundled via `imageio-ffmpeg` or a pinned build), OpenCV, NumPy, librosa, soundfile | Cloud render APIs (Shotstack, Creatomate, etc.) |
| "AI" / intelligence | Deterministic code, rule tables, classic DSP and CV, small **local** model files run in-process (ONNX Runtime, OpenCV DNN, llama.cpp, whisper.cpp) | Any hosted LLM, VLM, STT, TTS, or embedding API (Anthropic, OpenAI, Google, Azure, AWS, NVIDIA NIM hosted, Replicate, Hugging Face Inference, etc.) |
| Model weights | Files on disk, listed in a local model registry with SHA-256, licence, and source; installed once by an explicit user action | Downloading weights silently at runtime; `from_pretrained()` calls that hit the network |
| Network | Loopback only (`127.0.0.1` / `::1`) for the local UI talking to the local API | Any other outbound socket, analytics, crash reporting to a server, update pings |
| Auth | None needed for local use (single user, OS account is the boundary) | Clerk, Auth0, or any hosted identity provider |
| Storage | Local filesystem behind a `Storage` interface; SQLite or JSON project files | S3, R2, GCS, MinIO-as-a-service |

Development-time assistants (see `workflow.md`) are tools used by the
developer to write code. They are not runtime dependencies and must never be
imported or called by the product.

## 3. Consequences for the architecture

1. **The Director is local.** Brief parsing and planning are done by the
   rule-based Local Director in `LOCAL_DIRECTOR.md`. An optional local LLM
   (llama.cpp / GGUF file) may be added later behind the same interface, but
   the product must be fully usable with it absent.
2. **Perception is CPU-first and local.** Motion, sharpness, exposure, beats,
   energy (implemented); faces via OpenCV YuNet ONNX (Apache 2.0), saliency
   crop via OpenCV, scene cuts via PySceneDetect (BSD) when needed.
3. **Speech and captions** (later): whisper.cpp or faster-whisper with a
   local model file. **Narration** (later): Piper TTS (MIT) with local voices.
4. **GPU is an accelerator, not a dependency.** Every analyzer has a CPU path.
   `GPU_INFRASTRUCTURE_STRATEGY.md`'s cloud GPU workers apply only if a hosted
   product is ever chosen, which would require amending this policy.
5. **No cost model in tokens.** All marginal cost is local CPU time.

## 4. How it is enforced (not just promised)

| Check | Where | Fails when |
|---|---|---|
| Static import and dependency scan | `tools/check_offline.py`, run by `tests/test_offline_policy.py` | A forbidden SDK is imported or declared in `pyproject.toml` / requirements, or code contains a hard-coded non-loopback `http(s)://` URL outside docs and tests |
| Runtime socket guard | Planned: pytest fixture that patches `socket.socket.connect` to refuse non-loopback addresses during the end-to-end pipeline test | Anything tries to open an outbound connection while rendering |
| Model registry check | Planned: `models/registry.json` (id, file, sha256, licence, size) verified before load | A model file is missing, changed, or has no recorded licence |
| Release check | P8 checklist | A dependency added a telemetry or auto-update call |

Allowlist exceptions (for example a licence URL in a docstring) go in the
checker's `ALLOWED_URL_PREFIXES`, with a one-line reason in the commit.

## 5. Licence rules for local models

A local model is only acceptable if both the code and the **weights** permit
commercial use (Apache 2.0, MIT, BSD, OFL for fonts). Known rejects, from
`ARCHITECTURE_REVIEW.md`: madmom pretrained models (CC BY-NC-SA),
InsightFace models (non-commercial), Ultralytics YOLO (AGPL). Record every
model in the registry before it is used in an official evaluation.

## 6. Changing this policy

Only by a dated entry appended below, stating the reason and what data would
leave the machine. Silent edits are not allowed.

### Amendments

- *(none)*
