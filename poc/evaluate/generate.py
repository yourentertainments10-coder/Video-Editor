"""poc/evaluate/generate.py: generate the paired evaluation dataset.

This is the only module in Step 9 that calls into the frozen pipeline,
and it does so by composing Steps 4-7's own public functions directly
(analyze_music, analyze_footage, cut, cut_baseline, render) rather than
poc.pipeline.run_pipeline: a matched pair needs the cutter and baseline
outputs to share the exact same MusicMap and FootageMaps (the "same
beat grid, same candidate constraints" requirement), which is naturally
guaranteed by analyzing once and calling cut()/cut_baseline() with the
identical objects -- run_pipeline is built for one output per call and
would re-run analysis twice, which is both wasteful and a weaker
guarantee of sameness than sharing the actual objects. No algorithmic
logic lives here: every stage is an unmodified call into Steps 4-7.

Two dataset shapes come out of this module:

  generate_pair / generate_dataset  -- M2's core artifact: one cutter-v0
    output and one matched baseline-v0 output per (footage set, music
    track, mood) combination, plus a full PairManifest.

  generate_mood_identification_pair -- M3's artifact: the energetic and
    calm cutter-v0 outputs of the SAME footage+music, for a rater to
    guess which is which.

build_blind_dataset is the one function that copies rendered video into
the folder a rater will actually see, with opaque A.mp4/B.mp4 names and
zero mention of "cutter" or "baseline" anywhere in it -- see
poc.evaluate.blind's module docstring for where the real mapping is kept
instead.
"""
from __future__ import annotations

import csv
import hashlib
import inspect
import os
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from poc.decide import cut, cut_baseline
from poc.decide.style import Mood
from poc.execute.contact_sheet import generate_contact_sheet
from poc.evaluate.blind import (
    AnswerKey,
    MoodAnswerKey,
    MoodBlindAssignment,
    assign_blind_labels,
)
from poc.evaluate.manifest import (
    PairManifest,
    get_git_commit,
    get_source_code_fingerprint,
    is_git_worktree_dirty,
)
from poc.execute.render import render
from poc.perceive.footage import analyze_footage
from poc.perceive.music import analyze_music
from poc.pipeline import FOOTAGE_EXTENSIONS

__all__ = [
    "EvaluationGenerationError",
    "FootageSetSpec",
    "MusicTrackSpec",
    "MoodPairManifest",
    "generate_pair",
    "generate_dataset",
    "generate_mood_dataset",
    "build_blind_dataset",
    "generate_mood_identification_pair",
]


class EvaluationGenerationError(RuntimeError):
    """A clear, single exception type for every failure in generating the
    evaluation dataset -- missing footage, an unanalyzable music file, or
    anything the underlying (unmodified) Steps 4-7 functions raise."""


@dataclass(frozen=True)
class FootageSetSpec:
    footage_set_id: str
    footage_dir: Path


@dataclass(frozen=True)
class MusicTrackSpec:
    music_id: str
    music_path: Path


def _relpath(path: Path, base_dir: Path) -> str:
    return Path(os.path.relpath(path, base_dir)).as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _analysis_parameters(function) -> dict[str, object]:
    return {
        name: parameter.default
        for name, parameter in inspect.signature(function).parameters.items()
        if parameter.default is not inspect.Parameter.empty
        and isinstance(parameter.default, (str, int, float, bool, type(None)))
    }


def _discover_footage(footage_dir: Path) -> list[Path]:
    if not footage_dir.is_dir():
        raise EvaluationGenerationError(f"footage folder does not exist: {footage_dir}")
    files = sorted(p for p in footage_dir.iterdir() if p.is_file() and p.suffix.lower() in FOOTAGE_EXTENSIONS)
    if not files:
        raise EvaluationGenerationError(f"no footage found in {footage_dir}")
    return files


def _common_base_dir(footage_dir: Path, music_path: Path) -> Path:
    try:
        return Path(os.path.commonpath([str(footage_dir), str(music_path.parent)]))
    except ValueError as e:
        raise EvaluationGenerationError(
            f"footage folder ({footage_dir}) and music file ({music_path}) share no common parent"
        ) from e


# ---------------------------------------------------------------------------
# M2: cutter-vs-baseline matched pairs
# ---------------------------------------------------------------------------


def generate_pair(
    footage_set: FootageSetSpec,
    music: MusicTrackSpec,
    mood: Mood,
    duration_s: float,
    pair_id: str,
    out_dir: Path,
    *,
    baseline_seed: int,
    ffmpeg_exe: str | None = None,
    evaluation_phase: str = "development",
) -> PairManifest:
    """Generate one matched cutter-v0/baseline-v0 pair: same footage,
    same music, same mood, same duration, same MusicMap/FootageMaps
    (hence the same beat grid and candidate constraints, by construction
    -- see the module docstring), only the selection function differs."""
    footage_dir = Path(footage_set.footage_dir).resolve()
    music_path = Path(music.music_path).resolve()
    out_dir = Path(out_dir).resolve()

    footage_files = _discover_footage(footage_dir)
    if not music_path.is_file():
        raise EvaluationGenerationError(f"music file does not exist: {music_path}")
    base_dir = _common_base_dir(footage_dir, music_path)

    try:
        music_map = analyze_music(music_path, ffmpeg_exe=ffmpeg_exe)
        footage_maps = {
            _relpath(f, base_dir): analyze_footage(f, ffmpeg_exe=ffmpeg_exe) for f in footage_files
        }
    except Exception as e:
        raise EvaluationGenerationError(f"analysis failed for pair {pair_id}: {e}") from e

    rel_music_path = _relpath(music_path, base_dir)

    try:
        cutter_tl = cut(music_map, rel_music_path, footage_maps, mood, duration_s)
        baseline_tl = cut_baseline(
            music_map, rel_music_path, footage_maps, mood, duration_s, seed=baseline_seed
        )
    except Exception as e:
        raise EvaluationGenerationError(f"cutting failed for pair {pair_id}: {e}") from e

    timelines_dir = out_dir / "timelines"
    renders_dir = out_dir / "renders"
    manifests_dir = out_dir / "manifests"
    for d in (timelines_dir, renders_dir, manifests_dir):
        d.mkdir(parents=True, exist_ok=True)

    cutter_timeline_path = timelines_dir / f"{pair_id}_cutter.json"
    baseline_timeline_path = timelines_dir / f"{pair_id}_baseline.json"
    cutter_tl.save(cutter_timeline_path)
    baseline_tl.save(baseline_timeline_path)

    cutter_video_path = renders_dir / f"{pair_id}_cutter.mp4"
    baseline_video_path = renders_dir / f"{pair_id}_baseline.mp4"

    try:
        t0 = time.monotonic()
        render(cutter_tl, base_dir, cutter_video_path, ffmpeg_exe=ffmpeg_exe)
        cutter_render_seconds = time.monotonic() - t0

        t0 = time.monotonic()
        render(baseline_tl, base_dir, baseline_video_path, ffmpeg_exe=ffmpeg_exe)
        baseline_render_seconds = time.monotonic() - t0
        contact_sheets_dir = out_dir / "contact_sheets"
        generate_contact_sheet(
            cutter_tl, base_dir, contact_sheets_dir / f"{pair_id}_cutter.png"
        )
        generate_contact_sheet(
            baseline_tl, base_dir, contact_sheets_dir / f"{pair_id}_baseline.png"
        )
    except Exception as e:
        raise EvaluationGenerationError(f"rendering failed for pair {pair_id}: {e}") from e

    manifest = PairManifest(
        pair_id=pair_id,
        footage_set_id=footage_set.footage_set_id,
        footage_files=[_relpath(f, base_dir) for f in footage_files],
        music_id=music.music_id,
        music_file=rel_music_path,
        mood=mood,
        duration_s=duration_s,
        baseline_seed=baseline_seed,
        evaluation_phase=evaluation_phase,
        git_commit=get_git_commit(),
        git_worktree_dirty=is_git_worktree_dirty(),
        source_code_fingerprint=get_source_code_fingerprint(),
        analysis_parameters={
            "footage": _analysis_parameters(analyze_footage),
            "music": _analysis_parameters(analyze_music),
        },
        input_sha256={
            **{_relpath(file, base_dir): _sha256_file(file) for file in footage_files},
            rel_music_path: _sha256_file(music_path),
        },
        media_base_dir=str(base_dir),
        cutter_timeline_path=str(cutter_timeline_path),
        baseline_timeline_path=str(baseline_timeline_path),
        cutter_video_path=str(cutter_video_path),
        baseline_video_path=str(baseline_video_path),
        cutter_render_seconds=cutter_render_seconds,
        baseline_render_seconds=baseline_render_seconds,
        cutter_duration_frames=cutter_tl.duration_frames,
        baseline_duration_frames=baseline_tl.duration_frames,
    )
    manifest.save(manifests_dir / f"{pair_id}.json")
    return manifest


def generate_dataset(
    footage_sets: list[FootageSetSpec],
    music_tracks: list[MusicTrackSpec],
    moods: list[Mood],
    duration_s: float,
    out_dir: Path,
    *,
    base_seed: int = 0,
    ffmpeg_exe: str | None = None,
    evaluation_phase: str = "development",
) -> list[PairManifest]:
    """One pair per (footage set, music track, mood) combination -- the
    full MVP.md test-set grid is 5 footage sets x 3 tracks x 2 moods = 30
    pairs (60 rendered outputs). Smaller grids (fewer sets/tracks) work
    identically for development and testing."""
    manifests: list[PairManifest] = []
    index = 0
    for footage_set in footage_sets:
        for music in music_tracks:
            for mood in moods:
                index += 1
                pair_id = f"pair{index:03d}"
                manifests.append(
                    generate_pair(
                        footage_set, music, mood, duration_s, pair_id, out_dir,
                        baseline_seed=base_seed + index,
                        ffmpeg_exe=ffmpeg_exe,
                        evaluation_phase=evaluation_phase,
                    )
                )
    return manifests


def _write_rating_template(dataset_dir: Path, pair_ids: list[str]) -> None:
    path = dataset_dir / "rating_sheet_template.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["pair_id", "rater_id", "chosen_video", "reason", "timestamp"])
        for pid in pair_ids:
            writer.writerow([pid, "", "", "", ""])


def _write_secondary_rating_templates(
    dataset_dir: Path,
    pair_ids: list[str],
    mood_pair_ids: list[str],
) -> None:
    templates = {
        "m3_rating_sheet_template.csv": (
            ["mood_pair_id", "rater_id", "guessed_energetic", "timestamp"],
            [[pair_id, "", "", ""] for pair_id in mood_pair_ids],
        ),
        "m4_rating_sheet_template.csv": (
            ["output_id", "rater_id", "would_post", "timestamp"],
            [[f"{pair_id}_{side}", "", "", ""] for pair_id in pair_ids for side in ("A", "B")],
        ),
        "m5_rating_sheet_template.csv": (
            ["output_id", "rater_id", "in_time_rating", "timestamp"],
            [[f"{pair_id}_{side}", "", "", ""] for pair_id in pair_ids for side in ("A", "B")],
        ),
    }
    for name, (header, rows) in templates.items():
        with open(dataset_dir / name, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(rows)


_INSTRUCTIONS_TEXT = """# Rater instructions

For each pair (one `pairNNN/` folder), watch `A.mp4` and `B.mp4`. Both are
the same length, the same music, and the same mood -- only which moments
were chosen differs.

**Question: which is the better edit?**

Fill in one row of `rating_sheet_template.csv` per pair:
- `pair_id`: the folder name (e.g. `pair001`)
- `rater_id`: your own name or id, the same value on every row you fill in
- `chosen_video`: `A` or `B`
- `reason`: optional, a short note on why
- `timestamp`: when you rated it, e.g. `2026-01-01T12:00:00Z`

Do not look at any file outside this `dataset/` folder -- there is
nothing there that would tell you which system made which video, and
nothing to gain from trying to guess.

For each `moodNNN/` folder, watch both videos and record which one was
asked to be energetic in `m3_rating_sheet_template.csv` (`A` or `B`).

For each `pairNNN_A` and `pairNNN_B` output, record whether you would post
it after small manual tweaks in `m4_rating_sheet_template.csv`, and rate
whether its cuts feel in time with the music from 1 to 5 in
`m5_rating_sheet_template.csv`. Use the same rater ID in all sheets.
"""


def build_blind_dataset(manifests: list[PairManifest], out_dir: Path, *, blind_seed: int) -> AnswerKey:
    """Copy each pair's videos into <out>/dataset/<pair_id>/{A,B}.mp4 with
    a random (but reproducible, per poc.evaluate.blind) A/B assignment,
    and write the private answer key to <out>/answer_key.json --
    deliberately NOT inside dataset/. Hand dataset/ to raters; never hand
    them answer_key.json or anything under manifests/ or renders/.
    """
    out_dir = Path(out_dir)
    pair_ids = [m.pair_id for m in manifests]
    answer_key = assign_blind_labels(pair_ids, seed=blind_seed)

    dataset_dir = out_dir / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    for m in manifests:
        assignment = answer_key.assignments[m.pair_id]
        pair_dir = dataset_dir / m.pair_id
        pair_dir.mkdir(parents=True, exist_ok=True)
        cutter_src, baseline_src = Path(m.cutter_video_path), Path(m.baseline_video_path)
        a_src, b_src = (cutter_src, baseline_src) if assignment.a_is_cutter else (baseline_src, cutter_src)
        shutil.copy2(a_src, pair_dir / "A.mp4")
        shutil.copy2(b_src, pair_dir / "B.mp4")

    answer_key.save(out_dir / "answer_key.json")
    _write_rating_template(dataset_dir, pair_ids)
    mood_pair_ids = sorted(
        p.name for p in dataset_dir.glob("mood*") if p.is_dir()
    )
    _write_secondary_rating_templates(dataset_dir, pair_ids, mood_pair_ids)
    (dataset_dir / "instructions.md").write_text(_INSTRUCTIONS_TEXT, encoding="utf-8")
    return answer_key


# ---------------------------------------------------------------------------
# M3: mood-identification pairs (energetic vs. calm, same footage+music)
# ---------------------------------------------------------------------------


class MoodPairManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood_pair_id: str
    footage_set_id: str
    music_id: str
    duration_s: float
    evaluation_phase: str = "development"
    a_is_energetic: bool
    energetic_timeline_path: str
    calm_timeline_path: str
    dataset_a_path: str
    dataset_b_path: str


def _mood_blind_rng(mood_pair_id: str, seed: int) -> random.Random:
    return random.Random(f"mood:{seed}:{mood_pair_id}")


def generate_mood_identification_pair(
    footage_set: FootageSetSpec,
    music: MusicTrackSpec,
    duration_s: float,
    mood_pair_id: str,
    out_dir: Path,
    *,
    blind_seed: int,
    ffmpeg_exe: str | None = None,
    evaluation_phase: str = "development",
) -> MoodPairManifest:
    """M3's artifact: energetic and calm cutter-v0 outputs of the same
    footage+music, blinded as A/B the same way M2's pairs are, written
    directly into <out>/dataset/<mood_pair_id>/ alongside the M2 pairs."""
    footage_dir = Path(footage_set.footage_dir).resolve()
    music_path = Path(music.music_path).resolve()
    out_dir = Path(out_dir).resolve()

    footage_files = _discover_footage(footage_dir)
    if not music_path.is_file():
        raise EvaluationGenerationError(f"music file does not exist: {music_path}")
    base_dir = _common_base_dir(footage_dir, music_path)

    try:
        music_map = analyze_music(music_path, ffmpeg_exe=ffmpeg_exe)
        footage_maps = {
            _relpath(f, base_dir): analyze_footage(f, ffmpeg_exe=ffmpeg_exe) for f in footage_files
        }
    except Exception as e:
        raise EvaluationGenerationError(f"analysis failed for mood pair {mood_pair_id}: {e}") from e

    rel_music_path = _relpath(music_path, base_dir)
    try:
        energetic_tl = cut(music_map, rel_music_path, footage_maps, "energetic", duration_s)
        calm_tl = cut(music_map, rel_music_path, footage_maps, "calm", duration_s)
    except Exception as e:
        raise EvaluationGenerationError(f"cutting failed for mood pair {mood_pair_id}: {e}") from e

    timelines_dir = out_dir / "timelines"
    timelines_dir.mkdir(parents=True, exist_ok=True)
    energetic_timeline_path = timelines_dir / f"{mood_pair_id}_energetic.json"
    calm_timeline_path = timelines_dir / f"{mood_pair_id}_calm.json"
    energetic_tl.save(energetic_timeline_path)
    calm_tl.save(calm_timeline_path)

    renders_dir = out_dir / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    energetic_video_path = renders_dir / f"{mood_pair_id}_energetic.mp4"
    calm_video_path = renders_dir / f"{mood_pair_id}_calm.mp4"
    try:
        render(energetic_tl, base_dir, energetic_video_path, ffmpeg_exe=ffmpeg_exe)
        render(calm_tl, base_dir, calm_video_path, ffmpeg_exe=ffmpeg_exe)
        contact_sheets_dir = out_dir / "contact_sheets"
        generate_contact_sheet(
            energetic_tl, base_dir, contact_sheets_dir / f"{mood_pair_id}_energetic.png"
        )
        generate_contact_sheet(
            calm_tl, base_dir, contact_sheets_dir / f"{mood_pair_id}_calm.png"
        )
    except Exception as e:
        raise EvaluationGenerationError(f"rendering failed for mood pair {mood_pair_id}: {e}") from e

    a_is_energetic = _mood_blind_rng(mood_pair_id, blind_seed).random() < 0.5
    a_src, b_src = (
        (energetic_video_path, calm_video_path) if a_is_energetic else (calm_video_path, energetic_video_path)
    )

    dataset_dir = out_dir / "dataset" / mood_pair_id
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dataset_a_path = dataset_dir / "A.mp4"
    dataset_b_path = dataset_dir / "B.mp4"
    shutil.copy2(a_src, dataset_a_path)
    shutil.copy2(b_src, dataset_b_path)

    return MoodPairManifest(
        mood_pair_id=mood_pair_id,
        footage_set_id=footage_set.footage_set_id,
        music_id=music.music_id,
        duration_s=duration_s,
        evaluation_phase=evaluation_phase,
        a_is_energetic=a_is_energetic,
        energetic_timeline_path=str(energetic_timeline_path),
        calm_timeline_path=str(calm_timeline_path),
        dataset_a_path=str(dataset_a_path),
        dataset_b_path=str(dataset_b_path),
    )


def generate_mood_dataset(
    footage_sets: list[FootageSetSpec],
    music_tracks: list[MusicTrackSpec],
    duration_s: float,
    out_dir: Path,
    *,
    blind_seed: int,
    ffmpeg_exe: str | None = None,
    evaluation_phase: str = "development",
    pair_manifests: list[PairManifest] | None = None,
) -> list[MoodPairManifest]:
    """Generate M3 pairs, reusing M2 cutter renders when both moods exist."""
    out_dir = Path(out_dir)
    manifests: list[MoodPairManifest] = []
    assignments: dict[str, MoodBlindAssignment] = {}
    pair_by_key = {
        (manifest.footage_set_id, manifest.music_id, manifest.mood): manifest
        for manifest in pair_manifests or []
    }
    index = 0
    for footage_set in footage_sets:
        for music in music_tracks:
            index += 1
            mood_pair_id = f"mood{index:03d}"
            energetic = pair_by_key.get((footage_set.footage_set_id, music.music_id, "energetic"))
            calm = pair_by_key.get((footage_set.footage_set_id, music.music_id, "calm"))
            if energetic is not None and calm is not None:
                a_is_energetic = _mood_blind_rng(mood_pair_id, blind_seed).random() < 0.5
                source_a = Path(energetic.cutter_video_path if a_is_energetic else calm.cutter_video_path)
                source_b = Path(calm.cutter_video_path if a_is_energetic else energetic.cutter_video_path)
                dataset_dir = out_dir / "dataset" / mood_pair_id
                dataset_dir.mkdir(parents=True, exist_ok=True)
                dataset_a_path = dataset_dir / "A.mp4"
                dataset_b_path = dataset_dir / "B.mp4"
                shutil.copy2(source_a, dataset_a_path)
                shutil.copy2(source_b, dataset_b_path)
                manifest = MoodPairManifest(
                    mood_pair_id=mood_pair_id,
                    footage_set_id=footage_set.footage_set_id,
                    music_id=music.music_id,
                    duration_s=duration_s,
                    evaluation_phase=evaluation_phase,
                    a_is_energetic=a_is_energetic,
                    energetic_timeline_path=energetic.cutter_timeline_path,
                    calm_timeline_path=calm.cutter_timeline_path,
                    dataset_a_path=str(dataset_a_path),
                    dataset_b_path=str(dataset_b_path),
                )
            else:
                manifest = generate_mood_identification_pair(
                    footage_set,
                    music,
                    duration_s,
                    mood_pair_id,
                    out_dir,
                    blind_seed=blind_seed,
                    ffmpeg_exe=ffmpeg_exe,
                    evaluation_phase=evaluation_phase,
                )
            manifests.append(manifest)
            assignments[mood_pair_id] = MoodBlindAssignment(
                mood_pair_id=mood_pair_id,
                a_is_energetic=manifest.a_is_energetic,
            )

    mood_manifests_dir = out_dir / "mood_manifests"
    mood_manifests_dir.mkdir(parents=True, exist_ok=True)
    for manifest in manifests:
        path = mood_manifests_dir / f"{manifest.mood_pair_id}.json"
        path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    MoodAnswerKey(seed=blind_seed, assignments=assignments).save(out_dir / "mood_answer_key.json")
    return manifests
