GPU Infrastructure Strategy

> **Reconciliation note (2026-10-10).** The product is now local-first and
> fully offline (`OFFLINE_POLICY.md`, binding). Where this document prefers
> cloud processing or lists an "External API Implementation", it describes
> a possible future hosted variant only, which would require a dated
> amendment to the offline policy. For the current product: everything runs
> on the user's machine, CPU is the default, and a local GPU (if present)
> is an optional accelerator behind the same analyzer interfaces. The CPU-
> first and "benchmark before buying GPU" principles below still apply.

Purpose

This document records the infrastructure decision for GPU usage in the AI-powered autonomous video editing system.

The system should not depend on the end user's computer hardware for production processing. Heavy processing should run on backend infrastructure selected according to the workload.

This document is intentionally forward-looking. GPU infrastructure is not required for MVP v0, but the architecture should allow GPU workers to be introduced later without redesigning the core system.

1. Core Principle

There are two fundamentally different deployment models.

Local Processing

User's Computer
      |
      +-- Video Analysis
      +-- AI Models
      +-- Rendering

In this model, performance depends on the user's:

CPU

RAM

GPU

This is not the preferred architecture for the main SaaS product.

Cloud Processing

User
  |
  | Upload video / submit instruction
  v
Web Application / API
  |
  v
Job Queue
  |
  +-------------------+
  |                   |
  v                   v
CPU Workers       GPU Workers
  |                   |
  v                   v
Processing        Heavy AI Inference
  |                   |
  +---------+---------+
            |
            v
      Cloud Storage
            |
            v
       Final Video

In this architecture, the user's laptop or phone is primarily responsible for:

accessing the application

uploading media

giving editing instructions

viewing previews

downloading results

The actual processing happens on our infrastructure.

2. Current MVP Decision

GPU is NOT required for MVP v0

The initial MVP intentionally focuses on CPU-friendly components:

FFmpeg rendering

librosa music analysis

beat detection

OpenCV/basic video analysis

motion measurement

sharpness measurement

exposure measurement

deterministic/greedy cutter

timeline generation

evaluation harness

Therefore, the MVP can run:

locally on a reasonably capable development machine

on a CPU cloud worker

potentially on infrastructure such as Render for deployment/testing

The goal is to validate the core editing hypothesis before spending money on GPU infrastructure.

3. What Cloud Deployment Solves

Deploying the application means the processing no longer depends on the developer's or user's local Intel UHD graphics card.

However, deployment does not automatically mean GPU acceleration.

A normal cloud deployment can provide:

more CPU

more RAM

background workers

scalable instances

persistent services

job queues

This is sufficient for many CPU workloads, including the initial MVP.

4. CPU vs GPU Workloads

CPU Workers

Good candidates:

FFmpeg video rendering

audio preprocessing

librosa beat analysis

OpenCV analysis

timeline validation

deterministic cutting algorithms

metadata processing

API/backend work

job orchestration

GPU Workers

Likely future candidates:

deep video understanding

large vision models

video embeddings

advanced scene understanding

large-scale face embedding

GPU inference

generative AI effects

AI video generation

computationally intensive visual models

high-volume parallel inference

The system should treat GPU processing as an optional implementation detail of the Perception layer.

5. Recommended Long-Term Hybrid Architecture

                         USER
                           |
                           v
                    Web Application
                           |
                           v
                       API Backend
                           |
                           v
                        Job Queue
                           |
             +-------------+-------------+
             |                           |
             v                           v
        CPU Worker                   GPU Worker
             |                           |
       +-----+-----+               +-----+------+
       |           |               |            |
       v           v               v            v
    FFmpeg      Audio           Vision       AI Models
    Render      Analysis        Models       Embeddings
    Cutter      OpenCV          Detection    Generation
       |           |               |            |
       +-----------+---------------+------------+
                           |
                           v
                     Footage Index
                           |
                           v
                    Timeline / Decisions
                           |
                           v
                      Final Render
                           |
                           v
                       Cloud Storage

The important architectural rule is:

The rest of the system should consume standardized metadata and should not care whether that metadata was produced by a CPU worker, GPU worker, local machine, or future cloud provider.

6. GPU Provider Strategy

Free GPU Options

Free GPU services are useful primarily for:

experimentation

model testing

prototypes

notebooks

benchmarking

Examples may include temporary notebook environments such as Google Colab when GPU access is available.

Free resources should NOT be treated as production infrastructure because:

availability is not guaranteed

sessions can terminate

hardware can change

runtime limits may apply

persistent APIs are generally unsuitable

Paid GPU Providers

For production or serious testing, specialized GPU providers are generally paid.

Examples to evaluate when the project reaches that phase include:

RunPod

other on-demand GPU cloud providers

major cloud GPU offerings

The provider should not be selected yet. Pricing, availability, serverless capabilities, cold starts, GPU types, and model requirements should be compared at the time a real GPU workload exists.

7. On-Demand GPU Principle

The preferred model is:

Do not keep an expensive GPU running permanently.
                    X

Start GPU compute when a qualifying job requires it.
                    |
                    v

Process the workload.
                    |
                    v

Scale down when idle.
                    V

Conceptually:

User submits job
       |
       v
Does this job require GPU?
       |
   +---+---+
   |       |
  NO      YES
   |       |
   v       v
CPU      GPU Worker
Worker   starts/processes
   |       |
   +---+---+
       |
       v
   Store Result

This approach can reduce idle infrastructure cost, particularly during early product stages when job volume is unpredictable.

8. Development Hardware vs Production Hardware

These must be considered separately.

Development Machine

The current development machine only needs enough resources to:

write and test code

run the CPU-based MVP

perform small local renders

validate algorithms

A dedicated high-end NVIDIA GPU is not necessary for the current MVP.

Production Infrastructure

Production hardware is chosen based on actual workloads.

For example:

MVP
 |
 +-- CPU only
 |
 v
Advanced perception added
 |
 +-- Benchmark workload
 |
 v
Determine GPU requirement
 |
 +-- Select provider and GPU
 |
 v
Deploy GPU worker on demand

Do not purchase or permanently provision GPU infrastructure before benchmarking a real workload.

9. Architectural Requirement

Future GPU components must conform to replaceable interfaces.

Conceptually:

Perception Interface
        |
        +-- CPU Analyzer Implementation
        |
        +-- GPU Analyzer Implementation
        |
        +-- External API Implementation

All implementations should produce compatible output contracts.

Example:

Input Video
     |
     v
[ Analyzer ]
     |
     v
Standardized Footage Metadata
     |
     v
Footage Index
     |
     v
Decision Layer

This ensures that infrastructure can evolve independently of the rest of the editing system.

10. Current Decision

For the current roadmap:

Phase 0 / Architecture

No GPU infrastructure decision required.

Phase 1 / MVP Planning

No GPU required.

Phase 2 / MVP Implementation

CPU-first implementation.

Advanced Perception Phase

Benchmark candidate models locally or using temporary/on-demand GPU infrastructure.

Production GPU Phase

Compare current providers based on:

required GPU memory

model inference speed

cold-start time

serverless support

pricing

geographic availability

concurrency requirements

data transfer costs

scaling behavior

11. Final Position

The project should follow this progression:

NOW
 |
 v
CPU-only MVP
 |
 v
Validate automatic editing quality
 |
 v
Add advanced perception requirements
 |
 v
Benchmark actual AI models
 |
 v
Use temporary/free/low-cost GPU resources for experiments
 |
 v
Select production GPU provider only when justified
 |
 v
Deploy on-demand GPU workers

The key decision is:

GPU infrastructure is a future scaling layer, not a prerequisite for validating the core product.

The current MVP should remain CPU-first.