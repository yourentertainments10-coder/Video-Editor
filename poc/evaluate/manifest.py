"""poc/evaluate/manifest.py: the reproducibility manifest for one evaluation pair.

Step 9 is an isolated measurement layer around the frozen Steps 1-8. This
module holds no editing logic at all -- it only records, for one
cutter-v0/baseline-v0 pair, everything needed to reproduce it exactly:
which footage set and music track went in, the mood and duration
requested, the baseline's random seed, which algorithm version produced
it, and the git commit the whole pipeline was frozen at. None of this
identifies which artifact is which to a rater -- see poc/evaluate/blind.py
for that separation.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from poc.decide.style import Mood

__all__ = ["PairManifest", "get_git_commit", "CUTTER_VERSION", "BASELINE_VERSION"]

# These match Timeline.provenance.generator exactly (poc/timeline/schema.py)
# -- kept as separate constants here rather than imported for display
# purposes, since a manifest must remain readable even if that schema
# literal is ever extended.
CUTTER_VERSION = "cutter-v0"
BASELINE_VERSION = "baseline-v0"


def get_git_commit(repo_dir: Path | None = None) -> str | None:
    """Best-effort git commit hash of the frozen pipeline. None if this
    is not a git checkout or git is unavailable -- never raises."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir) if repo_dir else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None


class PairManifest(BaseModel):
    """Everything needed to reproduce one evaluation pair exactly."""

    model_config = ConfigDict(extra="forbid")

    pair_id: str
    footage_set_id: str
    footage_files: list[str]  # relative (POSIX) identifiers, not absolute paths
    music_id: str
    music_file: str
    mood: Mood
    duration_s: float
    baseline_seed: int
    cutter_version: str = CUTTER_VERSION
    baseline_version: str = BASELINE_VERSION
    git_commit: str | None = None

    # internal artifact paths -- NOT for rater eyes (a rater only ever
    # sees poc.evaluate.blind's opaque dataset/, never this manifest)
    cutter_timeline_path: str
    baseline_timeline_path: str
    cutter_video_path: str
    baseline_video_path: str

    # M1 (mechanical) inputs, recorded at generation time
    cutter_render_seconds: float
    baseline_render_seconds: float
    cutter_duration_frames: int
    baseline_duration_frames: int

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @classmethod
    def load(cls, path: str | Path) -> "PairManifest":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
