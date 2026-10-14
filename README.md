# Offline Video Editor

A local-first, offline video editing project built around a hybrid architecture:

- Python media pipeline and CLI in [poc](poc)
- local FastAPI backend in [poc/server](poc/server)
- React + TypeScript + Vite frontend in [ui](ui)

This project is designed to run fully offline and does not depend on external cloud services or AI APIs. The repository policy is documented in [docs/OFFLINE_POLICY.md](docs/OFFLINE_POLICY.md).

## Overview

This repo is not a SaaS application and not a purely terminal-only script. It combines:

- a deterministic Python editing/render pipeline
- a local editor backend for project and render orchestration
- a browser-based frontend for local preview and interaction

The project is still in MVP/experimental stages, but the underlying architecture is real and intentionally local-first.

## Architecture

### 1) Python engine and CLI
The main automation pipeline lives under [poc](poc). It handles:

- media ingestion and scanning
- music/beat analysis
- timeline generation and validation
- rendering and export
- evaluation and reporting
- local project asset management

The CLI entrypoint is [poc/__main__.py](poc/__main__.py), and it supports commands like:

```bash
.\.venv\Scripts\python.exe -m poc run --help
.\.venv\Scripts\python.exe -m poc run --footage .\project\footage --music .\project\music\track.mp3 --mood energetic --duration 20 --output .\project\output\final.mp4
```

### 2) Local editor backend
The local backend is implemented in [poc/server/app.py](poc/server/app.py) and started via [poc/server/__main__.py](poc/server/__main__.py).

It provides local APIs for:

- project creation and management
- asset ingestion
- timeline access and editing
- render jobs
- health checks and local serving

### 3) Frontend UI
The browser app is in [ui](ui), configured as a React + TypeScript + Vite project in [ui/package.json](ui/package.json).

The frontend is intended for local browser workflows, previews, and editor interactions. It is not meant for remote deployment.

## Project status

The repo is best described as a hybrid local editor project with:

- implemented Python render pipeline
- working local backend structure
- frontend code wired for local editing workflows
- ongoing validation and product completion work

This is not a finished end-user commercial editor, but it is significantly more than a bare CLI prototype.

## Quick start

### Requirements

- Python 3.11+
- Node.js and npm
- FFmpeg is bundled via `imageio-ffmpeg`, so it does not need to be on your PATH

### 1) Create and activate a Python environment

From the repository root:

```bash
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 2) Install frontend dependencies

```bash
cd ui
npm install
```

## How to run the UI locally

The app is designed to be run locally in development mode. Use two terminals.

### Terminal 1: start the backend

```bash
cd D:\Python\video editor
.\.venv\Scripts\python.exe -m poc.server
```

This starts the local FastAPI backend and opens the browser automatically. The server binds to `127.0.0.1` and chooses a random loopback port, then prints the URL in the terminal.

If the browser does not open automatically, use the URL printed in the terminal, such as:

```text
http://127.0.0.1:PORT/
```

### Terminal 2: start the frontend

```bash
cd D:\Python\video editor\ui
npm run dev
```

Then open the Vite local URL shown in the terminal, usually:

```text
http://localhost:5173/
```

### Recommended local workflow

1. Start the backend with `python -m poc.server`
2. Start the frontend with `cd ui && npm run dev`
3. Open the frontend URL in the browser
4. Create/load a project, import assets, and preview/edit locally

> The frontend is intended for local browser development and preview workflows, not remote hosting.

## Windows installer

`powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` builds a self-contained
app (Python, FFmpeg and the UI bundled, no internet needed) and an installer
`build\installer\OfflineVideoEditor-Setup-<version>.exe`. See [packaging/README.md](packaging/README.md).

`python -m poc.server --check` runs the same self-test as the installed app.

## Run the CLI pipeline

After placing footage and music under `project/`, run:

```bash
.\.venv\Scripts\python.exe -m poc run --footage .\project\footage --music .\project\music\track.mp3 --mood energetic --duration 20 --output .\project\output\final.mp4
```

This produces the rendered MP4 and related analysis artifacts in the output directory.

## Output and generated files

When rendering succeeds, the output folder may contain files such as:

- `final.mp4` — rendered result
- `timeline.json` — timeline metadata
- `music_map.json` — music analysis output
- `footage/` — footage analysis files
- `contact_sheet.png` — visual summary sheet

## Evaluation

The repository includes a structured evaluation workflow for blind testing and metric generation. See:

- [docs/EVALUATION_PROTOCOL.md](docs/EVALUATION_PROTOCOL.md)
- [poc/evaluate](poc/evaluate)

## Repository layout

```text
.
├── docs/                 project specs, architecture, audits, and policies
├── examples/             example fixtures and timelines
├── experiments/          prototype experiments
├── models/               supporting model assets
├── poc/                  core Python engine and CLI
│   ├── decide/           selection and decision logic
│   ├── evaluate/         scoring and reporting logic
│   ├── execute/          execution and render orchestration
│   ├── ingest/           asset import and normalization
│   ├── perceive/         media analysis
│   ├── render/           render/export logic
│   ├── server/           local FastAPI backend
│   ├── timeline/         timeline schema and validation
│   ├── __main__.py       CLI entry point
│   └── ...
├── project/              local input footage/music (gitignored)
├── tests/                automated tests
├── tools/                repo checks
├── ui/                   React + TypeScript + Vite frontend
├── pyproject.toml        Python package configuration
├── README.md             project overview
├── todo.md               backlog and notes
└── ...
```

## Key documentation

- [docs/MVP.md](docs/MVP.md) — implemented MVP constraints
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — wider system design
- [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md) — architecture review and decisions
- [docs/OFFLINE_POLICY.md](docs/OFFLINE_POLICY.md) — non-negotiable offline rule
- [docs/PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md) — gap analysis and completion estimate
- [docs/RENDER_SPEC.md](docs/RENDER_SPEC.md) — renderer specification
- [docs/LOCAL_DIRECTOR.md](docs/LOCAL_DIRECTOR.md) — local director logic
- [docs/VIDEO_CREATION_GUIDE.md](docs/VIDEO_CREATION_GUIDE.md) — input and pre-flight guidance

## Final note

This repository contains a real local-first offline editing stack: a Python media engine, a local backend, and a browser frontend. It is still evolving toward a complete end-user product, but it is already more than a simple command-line utility.
