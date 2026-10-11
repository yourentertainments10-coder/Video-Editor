# AI Video Editing — MVP v0

Deterministic, CPU-only command-line prototype: raw video footage, a music
track, and a mood produce a short, beat-synced edit. It is not a user-facing
editor or an AI application. There is no UI, LLM, cloud service, or GPU.

**Fully offline by design.** The project never calls an external AI API or
cloud service and keeps working without internet. This is a binding rule
([docs/OFFLINE_POLICY.md](docs/OFFLINE_POLICY.md)) checked by
`tools/check_offline.py` and `tests/test_offline_policy.py`.

The current product assumption is a standalone, local-first editor. The
implemented scope and constraints are in [docs/MVP.md](docs/MVP.md); the
aspirational system design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Other key documents:

- [docs/PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md) — gap analysis and completion estimate
- [docs/RENDER_SPEC.md](docs/RENDER_SPEC.md) — mixed-media renderer specification (P2.6–P2.8)
- [docs/LOCAL_DIRECTOR.md](docs/LOCAL_DIRECTOR.md) — offline brief/revision engine (P4)
- [docs/VIDEO_CREATION_GUIDE.md](docs/VIDEO_CREATION_GUIDE.md) — inputs and pre-flight checks before an edit

## Setup

Requires Python 3.11+ (developed on 3.13).

From the repository folder in Windows PowerShell, create and install the
development environment:

```bash
py -3.11 -m venv .venv
```

```bash
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

FFmpeg is **not** required on PATH: the project depends on `imageio-ffmpeg`,
which provides its own static FFmpeg binary, so rendering works the same on
any machine without a system install.

The FFmpeg binary used is: `D:\Python\video editor\.venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe`
(FFmpeg version 7.1-essentials_build-www.gyan.dev, GPL licensed).

Verify the environment:

```bash
.\.venv\Scripts\python.exe -c "import cv2, librosa, pydantic, numpy, soundfile, imageio_ffmpeg; print('deps OK')"
```

```bash
.\.venv\Scripts\python.exe -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

Run all automated tests (they use test fixtures and do not need your videos):

```bash
.\.venv\Scripts\python.exe -m pytest
```

If `py -3.11` is not installed, use `python -m venv .venv` with another
Python 3.11+ interpreter.

## Add Your Media

Put your source files in the `project/` folders. The normal video pipeline
does not copy or import the media; it reads files from the paths you pass.
Use this layout (the names can differ, but keep the footage files directly
inside the folder):

```
project/
  footage/
    clip_01.mp4
    clip_02.mov
    clip_03.mp4
    clip_04.mp4
    clip_05.mp4
  music/
    track.mp3
  output/
```

For a normal run, provide 5–20 video clips in `.mp4` or `.mov` format. Each
clip must be 5–180 seconds, and all clips together must total at least four
times the requested edit duration. The target edit must be 15–30 seconds.
Provide one music file in `.mp3`, `.wav`, `.m4a`, or `.flac` format that is at
least 10 seconds longer than the target edit. The music file must contain an
audio stream; video clips must contain video. The CLI only searches the
footage folder itself, so nested subfolders are not scanned.

Still images, voice-over, and additional audio tracks are not used by the
automatic video pipeline. The separate project asset library can record image
metadata, but image rendering and mixed-media editing are not implemented.

## Run The Editor Pipeline

The output option names a video file; its parent directory receives the
timeline, music and footage analysis JSON, and a contact sheet. `--out` is an
alias for `--output`. Run this from the repository root after adding your
media above:

```bash
.\.venv\Scripts\python.exe -m poc run --footage .\project\footage --music .\project\music\track.mp3 --mood energetic --duration 20 --output .\project\output\final.mp4
```

Choose `energetic` or `calm` for `--mood`, and change `--duration` to the
desired number of seconds. For a quick smoke test with short or deliberately
undersized media, add `--allow-development-inputs`; this skips the MVP input
size checks and does not make the result valid evaluation evidence. It still
needs decodable video and music, and the pipeline may fail if there is not
enough usable footage or music to build the requested timeline.

The command prints the paths of the final MP4 and generated artifacts. The
render is `project/output/final.mp4`; the same folder contains
`timeline.json`, `music_map.json`, `footage/` analysis files, and
`contact_sheet.png`. To see available options:

```bash
.\.venv\Scripts\python.exe -m poc run --help
```

To check the project without rendering your own media, run the automated
tests from the repository root:

```bash
.\.venv\Scripts\python.exe -m pytest
```

## Evaluation

`eval generate` defaults to the official 5 footage-set × 3 track × 2 mood
grid and requires a clean tracked worktree. For the diagnostic-only
preliminary grid, use three sets and two tracks:

```bash
.\.venv\Scripts\python.exe -m poc eval generate --phase preliminary --footage-set set_a=./project/footage/set_a --footage-set set_b=./project/footage/set_b --footage-set set_c=./project/footage/set_c --music track_a=./project/music/track_a.mp3 --music track_b=./project/music/track_b.mp3 --duration 20 --out ./project/prelim_eval
```

Both moods are generated unless `--mood` is supplied. The generated rater
folder contains separate templates for M2, M3, M4, and M5. Import a sheet
with `eval ingest --metric M3 --out <run-dir> --ratings <csv>`; choose one of
`M2`, `M3`, `M4`, or `M5` (`M2` is the default). Pass each expected rater with
`--expected-rater` when running `eval report`. Report exit codes are `0` for
a complete official all-metric pass, `1` for a complete measured failure,
and `2` when collection is incomplete or the dataset is diagnostic-only.
Before collecting official ratings, record the clean source commit and
fingerprint printed by the report in Section 11 of the frozen protocol.

Analysis JSON is cached by source SHA-256, analyzer version, and parameters
under `%LOCALAPPDATA%/poc/analysis` on Windows (or `$XDG_CACHE_HOME/poc/analysis`
on Linux/macOS). Editing or replacing media, changing analyzer parameters, or
bumping the analyzer version invalidates the relevant entry. Delete that
cache directory to clear it. Each successful render also creates
`contact_sheet.png`; evaluation renders create private diagnostic sheets in
`contact_sheets/`.

## Project layout

```
poc/
  timeline/   Timeline Document: schema + validator (single source of truth)
  perceive/   video and music analysis only, no editing decisions
  decide/     pure functions over analysis JSON: style, grid, cutter, baseline
  execute/    renders a validated Timeline to MP4 via FFmpeg
tests/        one test file per module
examples/     hand-written fixtures (e.g. a sample Timeline Document)
docs/         architecture, MVP, offline policy, render spec, audit
tools/        repository checks (check_offline.py)
experiments/  self-contained, throwaway PoCs (e.g. beat_flash) that inform
              but are not imported by the main package
project/      local input footage/music for manual runs (gitignored)
```

## Status

Estimated completion (2026-10-10, see [docs/PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md)):
MVP v0 engineering ~95%, MVP v0 blind evaluation 0% (needs real footage,
music, and five raters), first usable offline editor ~20%.

The deterministic analysis, cutter, renderer, pipeline, and evaluation CLI
exist. P0 evaluation correctness and completeness work is implemented and
covered by automated tests. No preliminary or official blind result exists;
the editing-quality hypothesis and product usefulness remain unvalidated.
The official evaluation must be generated from a clean committed source
version and completed with five independent raters before drawing a final
MVP conclusion. See [docs/EVALUATION_PROTOCOL.md](docs/EVALUATION_PROTOCOL.md)
for the frozen thresholds and decision rules.

P2.1/P2.2 add an experimental, data-only editor timeline schema v1.0 and
explicit migration dispatch from legacy v0.1. The CLI, Step 7 cutter, and
FFmpeg renderer continue to use v0.1; the editor schema is not yet used for
rendering. P2.3 adds a Python project asset library with `project.json`
metadata and external source paths; it does not copy source media into the
project. Back up `project.json` and every referenced source file together.
On restore to a different location, relink sources; stable asset IDs are
preserved after their content hashes are verified. Image import is supported
as metadata only; image rendering and the mixed-media renderer remain future
work. P2.4 declares a typed effect catalogue for editor timelines; all
catalogue entries are currently non-renderable. P2.5 adds a separate typed
transition catalogue attached to incoming clip boundaries; its entries are
also non-renderable. Neither catalogue changes the legacy renderer.
