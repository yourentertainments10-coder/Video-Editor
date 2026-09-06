"""poc/decide/style.py: mood -> deterministic CutterParams.

The entire "creative instruction" layer in MVP v0 (docs/MVP.md Section 8):
an enum with two values, mapped to fixed parameters by a lookup table, not
a model. This is deliberately the whole implementation -- no LLM, no
natural-language parsing, no additional moods. docs/MVP.md's own reasoning
for this (Section 8): if the deterministic core cannot make a good edit
from a perfect, hand-written plan, no Director will save it; if it can,
swapping this table for an LLM later is a well-understood, low-risk change
because the table's output type (CutterParams) is already what a future
Director would need to emit.

Style table, v0 (docs/MVP.md, "Style table, v0"):

| Param                        | energetic                    | calm                          |
|-------------------------------|-------------------------------|--------------------------------|
| beats per cut                 | 1 when e >= 0.6, else 2       | 4 when e <= 0.5, else 2        |
| motion_base, motion_scale     | 0.5, 0.5                      | 0.1, 0.3                       |

("music window: highest/lowest-energy window starting on a downbeat" is
the table's third row in docs/MVP.md and is NOT implemented here -- see
poc/decide/grid.py's module docstring for why: it requires downbeat
detection, which Step 5 deliberately excludes per
experiments/beat_flash/CONCLUSION.md decision 8, and it is not part of
this step's requested responsibilities.)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = ["Mood", "CutterParams", "style_for_mood"]

Mood = Literal["energetic", "calm"]


class CutterParams(BaseModel):
    """Deterministic scoring weights/preferences for one mood.

    ``beats_per_cut`` is a method, not a field, because the style table
    defines it as a function of the current beat's (normalized) energy,
    not a single constant -- see docs/MVP.md's style table exactly above.
    """

    model_config = ConfigDict(extra="forbid")

    mood: Mood
    motion_base: float
    motion_scale: float

    def beats_per_cut(self, energy: float) -> int:
        """Number of beats until the next cut, evaluated at one beat's
        normalized energy (0..1). Exactly the style table's rule."""
        if self.mood == "energetic":
            return 1 if energy >= 0.6 else 2
        # calm
        return 4 if energy <= 0.5 else 2


_MOOD_PARAMS: dict[Mood, CutterParams] = {
    "energetic": CutterParams(mood="energetic", motion_base=0.5, motion_scale=0.5),
    "calm": CutterParams(mood="calm", motion_base=0.1, motion_scale=0.3),
}


def style_for_mood(mood: Mood) -> CutterParams:
    """Return the fixed CutterParams for ``mood``. Only "energetic" and
    "calm" exist in MVP v0; anything else is a clear, immediate error
    rather than a silent default."""
    try:
        return _MOOD_PARAMS[mood].model_copy()
    except KeyError:
        raise ValueError(
            f"unknown mood {mood!r}: MVP v0 supports only {sorted(_MOOD_PARAMS)}"
        ) from None
