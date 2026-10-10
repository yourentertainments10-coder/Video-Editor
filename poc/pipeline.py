"""poc/pipeline.py: Step 8 -- end-to-end orchestration only.

Wires together every previously-built component into one call:

  analyze_footage (Step 6) ─┐
  analyze_music   (Step 5) ─┼─► cut / cut_baseline (Step 7, which already
                            │   calls poc.timeline.validate() internally
                            │   and never returns an invalid Timeline --
                            │   Step 3) ─► render (Step 4) ─► final MP4

No new algorithmic logic lives here. Every step below calls an existing
public function from poc.perceive / poc.decide / poc.timeline / poc.execute
unchanged; this module's only job is sequencing, path bookkeeping,
artifact I/O, and turning each component's specific exception into one
clear, uniform ``PipelineError`` so the CLI has exactly one thing to catch.

Path-resolution decision (from the Step 8 pre-implementation review): the
Timeline schema resolves every relative path against a single base_dir,
but the CLI's footage folder and music file may live in unrelated
locations, while ``--output`` points somewhere else again. This module
computes ``base_dir`` as the common parent directory of the footage
folder and the music file, and expresses every ``Source.path``/
``Music.path`` relative to it via ``os.path.relpath`` -- so ``render()``
is called directly (never ``render_from_file()``), decoupling "where the
Timeline JSON is saved" from "where media is resolved from". If the
footage folder and music file share no common parent (e.g. different
Windows drives), this fails clearly rather than crashing on a raw
``ValueError`` from ``os.path.commonpath``.

Artifacts are saved next to the final video, in ``--output``'s parent
directory: ``music_map.json``, ``footage/<original filename>.json`` per
input clip, ``timeline.json``, ``contact_sheet.png``, and the final video.
Music and footage analysis use the content-addressed JSON cache in
``poc.perceive.cache``; source content, analyzer version, or analysis
parameter changes produce a new cache entry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from poc.decide import CutterError, GridError, cut, cut_baseline
from poc.decide.style import Mood
from poc.execute.contact_sheet import ContactSheetError, generate_contact_sheet
from poc.execute.render import FFmpegError, RenderResult, TimelineInvalidError, render
from poc.perceive.footage import FootageMap, analyze_footage
from poc.perceive.music import analyze_music
from poc.timeline.schema import Timeline

__all__ = ["PipelineError", "PipelineResult", "run_pipeline", "FOOTAGE_EXTENSIONS"]

FOOTAGE_EXTENSIONS = {".mp4", ".mov"}


class PipelineError(RuntimeError):
    """The one exception type Step 8 raises for every orchestration
    failure. The original exception, if any, is always chained via
    ``raise ... from e`` so nothing about the real cause is lost."""


@dataclass(frozen=True)
class PipelineResult:
    timeline: Timeline
    render_result: RenderResult
    music_map_path: Path
    footage_map_paths: dict[str, Path]
    timeline_path: Path
    output_path: Path
    contact_sheet_path: Path


def _discover_footage(footage_dir: Path) -> list[Path]:
    if not footage_dir.is_dir():
        raise PipelineError(f"footage folder does not exist or is not a directory: {footage_dir}")
    files = sorted(
        p for p in footage_dir.iterdir() if p.is_file() and p.suffix.lower() in FOOTAGE_EXTENSIONS
    )
    if not files:
        raise PipelineError(
            f"no footage found in {footage_dir} (looked for {sorted(FOOTAGE_EXTENSIONS)})"
        )
    return files


def _common_base_dir(footage_dir: Path, music_path: Path) -> Path:
    try:
        return Path(os.path.commonpath([str(footage_dir), str(music_path.parent)]))
    except ValueError as e:
        # os.path.commonpath raises ValueError when the paths share no
        # common root (e.g. different Windows drives) -- a clear,
        # actionable failure rather than a crash.
        raise PipelineError(
            f"footage folder ({footage_dir}) and music file ({music_path}) share no "
            "common parent directory; the Timeline path-resolution contract needs one"
        ) from e


def _rel_path(path: Path, base_dir: Path) -> str:
    return Path(os.path.relpath(path, base_dir)).as_posix()


def run_pipeline(
    footage_dir: Path | str,
    music_path: Path | str,
    mood: Mood,
    duration_s: float,
    output_path: Path | str,
    *,
    baseline: bool = False,
    seed: int | None = None,
    bpm_hint: float | None = None,
    music_offset_s: float | None = None,
    ffmpeg_exe: str | None = None,
) -> PipelineResult:
    """Run the full pipeline and return where every artifact landed.

    Raises ``PipelineError`` for every failure mode: missing/empty
    footage, an unanalyzable music file, a duration the beat grid cannot
    satisfy, a footage set with no valid candidate, or a Timeline that
    fails validation. Nothing here is new scoring/cutting/rendering logic
    -- see the module docstring for exactly which existing function each
    step calls.
    """
    footage_dir = Path(footage_dir).resolve()
    music_path = Path(music_path).resolve()
    output_path = Path(output_path).resolve()

    if not music_path.is_file():
        raise PipelineError(f"music file does not exist: {music_path}")

    footage_files = _discover_footage(footage_dir)
    base_dir = _common_base_dir(footage_dir, music_path)

    artifacts_dir = output_path.parent
    footage_artifacts_dir = artifacts_dir / "footage"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    footage_artifacts_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 5: music analysis ---
    music_kwargs = {} if bpm_hint is None else {"start_bpm": bpm_hint}
    try:
        music_map = analyze_music(music_path, ffmpeg_exe=ffmpeg_exe, **music_kwargs)
    except Exception as e:
        raise PipelineError(f"music analysis failed for {music_path}: {e}") from e

    music_map_path = artifacts_dir / "music_map.json"
    music_map.save(music_map_path)

    # --- Step 6: footage analysis (one call per file, unmodified) ---
    footage_maps: dict[str, FootageMap] = {}
    footage_map_paths: dict[str, Path] = {}
    for footage_file in footage_files:
        try:
            footage_map = analyze_footage(footage_file, ffmpeg_exe=ffmpeg_exe)
        except Exception as e:
            raise PipelineError(f"footage analysis failed for {footage_file}: {e}") from e
        rel_path = _rel_path(footage_file, base_dir)
        footage_maps[rel_path] = footage_map
        fm_path = footage_artifacts_dir / f"{footage_file.name}.json"
        footage_map.save(fm_path)
        footage_map_paths[rel_path] = fm_path

    rel_music_path = _rel_path(music_path, base_dir)

    # --music-offset-s reuses Step 7's existing start_beat_index hook; no
    # new music-window-selection logic is added here (see Step 7's own
    # disclosed scope note on why downbeat-based window scanning is not
    # implemented at all).
    start_beat_index = 0
    if music_offset_s is not None:
        start_beat_index = next(
            (i for i, t in enumerate(music_map.beats_used_s) if t >= music_offset_s), None
        )
        if start_beat_index is None:
            raise PipelineError(
                f"--music-offset-s {music_offset_s} is past the last detected beat "
                f"({music_map.beats_used_s[-1]:.2f}s) in {music_path}"
            )

    # --- Step 7: deterministic cutter or baseline (validates internally, Step 3) ---
    try:
        if baseline:
            timeline = cut_baseline(
                music_map, rel_music_path, footage_maps, mood, duration_s,
                seed=seed if seed is not None else 0,
                start_beat_index=start_beat_index,
            )
        else:
            timeline = cut(
                music_map, rel_music_path, footage_maps, mood, duration_s,
                start_beat_index=start_beat_index,
            )
    except GridError as e:
        raise PipelineError(f"requested duration ({duration_s}s) could not be satisfied: {e}") from e
    except CutterError as e:
        raise PipelineError(f"no valid edit could be produced: {e}") from e
    except ValueError as e:
        raise PipelineError(f"invalid request: {e}") from e

    timeline_path = artifacts_dir / "timeline.json"
    timeline.save(timeline_path)

    # --- Step 4: render (re-validates independently against fresh probes) ---
    try:
        render_result = render(timeline, base_dir, output_path, ffmpeg_exe=ffmpeg_exe)
    except TimelineInvalidError as e:
        raise PipelineError(f"timeline failed validation before rendering: {e}") from e
    except FFmpegError as e:
        raise PipelineError(f"rendering failed: {e}") from e

    contact_sheet_path = artifacts_dir / "contact_sheet.png"
    try:
        generate_contact_sheet(timeline, base_dir, contact_sheet_path)
    except ContactSheetError as e:
        raise PipelineError(f"contact sheet generation failed: {e}") from e

    return PipelineResult(
        timeline=timeline,
        render_result=render_result,
        music_map_path=music_map_path,
        footage_map_paths=footage_map_paths,
        timeline_path=timeline_path,
        output_path=output_path,
        contact_sheet_path=contact_sheet_path,
    )
