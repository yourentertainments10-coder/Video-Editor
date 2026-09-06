"""poc.perceive: video and music analysis only -- no editing decisions."""

from poc.perceive.footage import FootageMap, FootageSample, analyze_footage
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
    "FootageMap",
    "FootageSample",
    "LowEnergyRegion",
    "MusicMap",
    "OctaveAlternative",
    "OctaveDiagnostics",
    "WindowSummary",
    "analyze_footage",
    "analyze_music",
]
