# Development Workflow

> The models named below (Fable, Sonnet, Opus) are **development assistants**
> used by the developer to design and write code. They are not part of the
> product and are never called at runtime. The shipped editor uses no
> external AI (`OFFLINE_POLICY.md`). Runtime intelligence for stages 03-04
> is the offline Local Director (`LOCAL_DIRECTOR.md`).

```
00 - Master Architecture
Model: Fable
        │
        │ Architecture review + freeze
        ▼
01 - MVP Planning & Proof of Concept
Model: Fable
        │
        │ Define smallest testable product
        ▼
02 - MVP Implementation
Model: Claude Sonnet / Opus for coding
        │
        ├── Timeline Core
        ├── Audio Analysis
        ├── Scene Detection
        ├── Deterministic Cutter
        └── FFmpeg Renderer
        ▼
03 - AI Editing Agent Design
Model: Fable
        │
        │ Agent reasoning + orchestration
        ▼
04 - Natural Language Revision System
Model: Fable + Sonnet
        │
        ▼
05 - Full Product Development
Model: Sonnet primarily
        │
        ├── Backend
        ├── AI Pipeline
        ├── Rendering Engine
        └── Frontend
        ▼
06 - Testing & Debugging
Model: Sonnet / Opus
        │
        ▼
07 - Deployment & DevOps
Model: Sonnet + Fable for architecture review
```
