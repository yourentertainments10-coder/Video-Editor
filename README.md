# AI Video Editing — MVP v0

Deterministic, CPU-only proof of concept: raw footage + a music track + a
one-word mood produce a short, beat-synced automatic edit. No LLM, no cloud
infrastructure, no GPU. Full scope and rationale: [docs/MVP.md](docs/MVP.md).
Long-term design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Setup

Requires Python 3.11+ (developed on 3.13).

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -e ".[dev]"
```

FFmpeg is **not** required on PATH: the project depends on `imageio-ffmpeg`,
which provides its own static FFmpeg binary, so rendering works the same on
any machine without a system install.

Verify the environment:

```bash
.venv/Scripts/python -c "import cv2, librosa, pydantic, numpy, soundfile, imageio_ffmpeg; print('deps OK')"
```

```bash
.venv/Scripts/python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

Run the test suite:

```bash
.venv/Scripts/pytest
```

## Project layout

```
poc/
  timeline/   Timeline Document: schema + validator (single source of truth)
  perceive/   video and music analysis only, no editing decisions
  decide/     pure functions over analysis JSON: style, grid, cutter, baseline
  execute/    renders a validated Timeline to MP4 via FFmpeg
tests/        one test file per module
examples/     hand-written fixtures (e.g. a sample Timeline Document)
docs/         architecture, MVP definition, and review documents
experiments/  self-contained, throwaway PoCs (e.g. beat_flash) that inform
              but are not imported by the main package
project/      local input footage/music for manual runs (gitignored)
```

## Status

Implementation proceeds one step at a time per `docs/MVP.md` Section 11.
Currently: Step 1 (repository setup) complete. See that document for the
full sequence and acceptance checks for each step.
