"""Integration tests for poc.evaluate (Step 9): real synthetic media
through generate_pair/generate_dataset/build_blind_dataset, manifest
reproducibility, generator-identity hiding at the actual file level (not
just the data model), and the real CLI subcommands as subprocesses.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import pytest

from poc.evaluate import (
    AnswerKey,
    FootageSetSpec,
    MusicTrackSpec,
    build_blind_dataset,
    generate_dataset,
    generate_pair,
)
from poc.timeline.validate import validate

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


def _make_footage(path: Path, duration_s: float = 6.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=30", "-t", str(duration_s), str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _make_click_music(path: Path, duration_s: float = 20.0, bpm: float = 120.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    period = 60.0 / bpm
    expr = f"if(lt(mod(t\\,{period}),0.06),sin(2*PI*1000*t),0)"
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"aevalsrc='{expr}':d={duration_s}:s=22050", "-c:a", "libmp3lame", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


@pytest.fixture
def project(tmp_path: Path) -> dict:
    footage_dir = tmp_path / "footage" / "setA"
    for i in range(5):
        _make_footage(footage_dir / f"clip{i}.mp4")
    music_path = tmp_path / "music" / "trackA.mp3"
    _make_click_music(music_path)
    return {"footage_dir": footage_dir, "music_path": music_path, "out_dir": tmp_path / "eval_out"}


# ---------------------------------------------------------------------------
# generate_pair: real, complete artifact
# ---------------------------------------------------------------------------

def test_generate_pair_produces_a_valid_matched_pair(project):
    footage_set = FootageSetSpec("setA", project["footage_dir"])
    music = MusicTrackSpec("trackA", project["music_path"])
    manifest = generate_pair(
        footage_set, music, "energetic", 8.0, "pair001", project["out_dir"], baseline_seed=1
    )

    assert manifest.pair_id == "pair001"
    assert Path(manifest.cutter_video_path).exists()
    assert Path(manifest.baseline_video_path).exists()
    assert Path(manifest.cutter_timeline_path).exists()
    assert Path(manifest.baseline_timeline_path).exists()

    from poc.timeline.schema import Timeline

    cutter_tl = Timeline.load(manifest.cutter_timeline_path)
    baseline_tl = Timeline.load(manifest.baseline_timeline_path)
    assert cutter_tl.provenance.generator == "cutter-v0"
    assert baseline_tl.provenance.generator == "baseline-v0"
    assert validate(cutter_tl) == []
    assert validate(baseline_tl) == []
    # same beat grid / candidate constraints by construction (shared MusicMap/FootageMaps)
    assert cutter_tl.duration_frames == baseline_tl.duration_frames
    assert cutter_tl.music.in_ms == baseline_tl.music.in_ms
    assert [c.start_frame for c in cutter_tl.sorted_clips()] == [c.start_frame for c in baseline_tl.sorted_clips()]


def test_generate_pair_records_git_commit_when_available(project):
    footage_set = FootageSetSpec("setA", project["footage_dir"])
    music = MusicTrackSpec("trackA", project["music_path"])
    manifest = generate_pair(
        footage_set, music, "calm", 8.0, "pair001", project["out_dir"], baseline_seed=1
    )
    # This repo is a git checkout, so a commit hash should be present.
    assert manifest.git_commit is not None
    assert len(manifest.git_commit) == 40


# ---------------------------------------------------------------------------
# manifest reproducibility
# ---------------------------------------------------------------------------

def test_manifest_is_reproducible_across_two_runs(project):
    footage_set = FootageSetSpec("setA", project["footage_dir"])
    music = MusicTrackSpec("trackA", project["music_path"])
    m1 = generate_pair(footage_set, music, "energetic", 8.0, "pairA", project["out_dir"] / "run1", baseline_seed=5)
    m2 = generate_pair(footage_set, music, "energetic", 8.0, "pairA", project["out_dir"] / "run2", baseline_seed=5)

    assert m1.footage_set_id == m2.footage_set_id
    assert m1.footage_files == m2.footage_files
    assert m1.music_id == m2.music_id
    assert m1.mood == m2.mood
    assert m1.duration_s == m2.duration_s
    assert m1.baseline_seed == m2.baseline_seed
    assert m1.cutter_version == m2.cutter_version
    assert m1.git_commit == m2.git_commit
    assert m1.cutter_duration_frames == m2.cutter_duration_frames
    assert m1.baseline_duration_frames == m2.baseline_duration_frames

    from poc.timeline.schema import Timeline

    tl1 = Timeline.load(m1.cutter_timeline_path)
    tl2 = Timeline.load(m2.cutter_timeline_path)
    data1 = tl1.model_dump()
    data2 = tl2.model_dump()
    data1["provenance"].pop("created_at")
    data2["provenance"].pop("created_at")
    assert data1 == data2  # identical edit decisions given identical inputs


# ---------------------------------------------------------------------------
# generator identity is hidden from rating artifacts -- at the file level
# ---------------------------------------------------------------------------

def test_dataset_folder_contains_no_trace_of_generator_identity(project):
    footage_set = FootageSetSpec("setA", project["footage_dir"])
    music = MusicTrackSpec("trackA", project["music_path"])
    manifests = generate_dataset([footage_set], [music], ["energetic", "calm"], 8.0, project["out_dir"], base_seed=0)
    build_blind_dataset(manifests, project["out_dir"], blind_seed=3)

    dataset_dir = project["out_dir"] / "dataset"
    for path in dataset_dir.rglob("*"):
        if path.is_file():
            assert "cutter" not in path.name.lower()
            assert "baseline" not in path.name.lower()
            if path.suffix in (".md", ".csv"):
                text = path.read_text(encoding="utf-8").lower()
                assert "cutter" not in text
                assert "baseline" not in text

    # the answer key -- the only place the mapping exists -- must NOT be
    # inside the dataset folder raters are handed.
    assert not (dataset_dir / "answer_key.json").exists()
    assert (project["out_dir"] / "answer_key.json").exists()


def test_dataset_videos_are_named_only_a_and_b(project):
    footage_set = FootageSetSpec("setA", project["footage_dir"])
    music = MusicTrackSpec("trackA", project["music_path"])
    manifests = generate_dataset([footage_set], [music], ["energetic"], 8.0, project["out_dir"], base_seed=0)
    build_blind_dataset(manifests, project["out_dir"], blind_seed=1)

    for m in manifests:
        pair_dir = project["out_dir"] / "dataset" / m.pair_id
        assert {p.name for p in pair_dir.iterdir()} == {"A.mp4", "B.mp4"}


def test_answer_key_correctly_identifies_which_video_is_which(project):
    footage_set = FootageSetSpec("setA", project["footage_dir"])
    music = MusicTrackSpec("trackA", project["music_path"])
    manifests = generate_dataset([footage_set], [music], ["energetic"], 8.0, project["out_dir"], base_seed=0)
    answer_key = build_blind_dataset(manifests, project["out_dir"], blind_seed=1)

    import filecmp

    for m in manifests:
        assignment = answer_key.assignments[m.pair_id]
        pair_dir = project["out_dir"] / "dataset" / m.pair_id
        expected_a_src = m.cutter_video_path if assignment.a_is_cutter else m.baseline_video_path
        assert filecmp.cmp(pair_dir / "A.mp4", expected_a_src, shallow=False)


# ---------------------------------------------------------------------------
# the real CLI subcommands, as subprocesses
# ---------------------------------------------------------------------------

def test_cli_eval_generate_ingest_report_end_to_end(project):
    out_dir = project["out_dir"]
    gen = subprocess.run(
        [
            sys.executable, "-m", "poc", "eval", "generate",
            "--footage-set", f"setA={project['footage_dir']}",
            "--music", f"trackA={project['music_path']}",
            "--mood", "energetic",
            "--duration", "8",
            "--out", str(out_dir),
            "--seed", "0", "--blind-seed", "1",
        ],
        capture_output=True, text=True,
    )
    assert gen.returncode == 0, gen.stderr
    assert (out_dir / "answer_key.json").exists()
    assert (out_dir / "dataset" / "pair001" / "A.mp4").exists()

    answer_key = AnswerKey.load(out_dir / "answer_key.json")
    a_is_cutter = answer_key.assignments["pair001"].a_is_cutter
    chosen = "A" if a_is_cutter else "B"  # rate for the cutter, to get a deterministic PASS

    ratings_csv = out_dir / "ratings.csv"
    ratings_csv.write_text(
        "pair_id,rater_id,chosen_video,reason,timestamp\n"
        f"pair001,alice,{chosen},,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    ingest = subprocess.run(
        [sys.executable, "-m", "poc", "eval", "ingest", "--out", str(out_dir), "--ratings", str(ratings_csv)],
        capture_output=True, text=True,
    )
    assert ingest.returncode == 0, ingest.stderr
    assert (out_dir / "ratings.jsonl").exists()

    report = subprocess.run(
        [sys.executable, "-m", "poc", "eval", "report", "--out", str(out_dir)],
        capture_output=True, text=True,
    )
    assert "M2: pairwise preference" in report.stdout
    assert "cutter wins       : 1" in report.stdout


def test_cli_eval_ingest_warns_on_unknown_pair(project):
    out_dir = project["out_dir"]
    subprocess.run(
        [
            sys.executable, "-m", "poc", "eval", "generate",
            "--footage-set", f"setA={project['footage_dir']}",
            "--music", f"trackA={project['music_path']}",
            "--mood", "energetic", "--duration", "8", "--out", str(out_dir),
        ],
        check=True, capture_output=True, text=True,
    )
    ratings_csv = out_dir / "bad_ratings.csv"
    ratings_csv.write_text(
        "pair_id,rater_id,chosen_video,reason,timestamp\n"
        "pair_does_not_exist,alice,A,,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    ingest = subprocess.run(
        [sys.executable, "-m", "poc", "eval", "ingest", "--out", str(out_dir), "--ratings", str(ratings_csv)],
        capture_output=True, text=True,
    )
    assert ingest.returncode == 0
    assert "unknown pair_id" in ingest.stderr
