"""poc: MVP v0 proof of concept for deterministic, beat-synced automatic video editing.

Package layout mirrors the PERCEIVE -> DECIDE -> EXECUTE architecture:

  poc.timeline   the Timeline Document: schema + validator (the single source of truth)
  poc.perceive   video and music analysis only; never makes editing decisions
  poc.decide     pure functions over analysis JSON: style, grid, cutter, baseline
  poc.execute    turns a validated Timeline into a rendered MP4 (FFmpeg)

See docs/MVP.md for the full scope and docs/ARCHITECTURE.md for the long-term design.
"""

__version__ = "0.1.0"
