"""poc.perceive: video and music analysis only -- no editing decisions.

Step 5 adds ``music``. Step 6 will add ``footage`` alongside it.
"""

from poc.perceive.music import (
    BeatEnergy,
    EnergyDiagnostics,
    LowEnergyRegion,
    MusicMap,
    OctaveAlternative,
    OctaveDiagnostics,
    WindowSummary,
    analyze_music,
)

__all__ = [
    "BeatEnergy",
    "EnergyDiagnostics",
    "LowEnergyRegion",
    "MusicMap",
    "OctaveAlternative",
    "OctaveDiagnostics",
    "WindowSummary",
    "analyze_music",
]
