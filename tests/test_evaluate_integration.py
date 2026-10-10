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
import cv2
import pytest

from poc.evaluate import (
    AnswerKey,
    FootageSetSpec,
    MusicTrackSpec,
    MoodAnswerKey,
    build_blind_dataset,
    compute_m1,
    generate_dataset,
    generate_mood_dataset,
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
    cutter_sheet = project["out_dir"] / "contact_sheets" / "pair001_cutter.png"
    baseline_sheet = project["out_dir"] / "contact_sheets" / "pair001_baseline.png"
    assert cutter_sheet.is_file() and baseline_sheet.is_file()
    assert cv2.imread(str(cutter_sheet)).std() > 0

    from poc.timeline.schema import Timeline

    cutter_tl = Timeline.load(manifest.cutter_timeline_path)
    baseline_tl = Timeline.load(manifest.baseline_timeline_path)
    assert cutter_tl.provenance.generator == "cutter-v0"
    assert baseline_tl.provenance.generator == "baseline-v0"
    assert validate(cutter_tl) == []
    assert validate(baseline_tl) == []
    project["music_path"].write_bytes(project["music_path"].read_bytes() + b"changed")
    Path(manifest.cutter_video_path).unlink()
    m1 = compute_m1([manifest], expected_pair_ids={"pair001", "pair002"})
    assert m1.total_outputs == 4
    assert m1.passing_outputs == 0
    assert any("rendered video is missing" in failure for failure in m1.failures)
    assert any("source changed since generation" in failure for failure in m1.failures)
    assert any("pair002" in failure and "manifest is missing" in failure for failure in m1.failures)
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
    assert m1.analysis_version == "poc-analysis-v1"
    assert len(m1.source_code_fingerprint or "") == 64
    assert len(m1.input_sha256) == 6
    assert all(len(digest) == 64 for digest in m1.input_sha256.values())
    assert m1.media_base_dir == str(project["footage_dir"].parent.parent)
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


def test_m3_reuses_the_existing_m2_cutter_renders(project):
    footage_set = FootageSetSpec("setA", project["footage_dir"])
    music = MusicTrackSpec("trackA", project["music_path"])
    manifests = generate_dataset(
        [footage_set], [music], ["energetic", "calm"], 8.0,
        project["out_dir"], base_seed=0,
    )
    mood_manifests = generate_mood_dataset(
        [footage_set], [music], 8.0, project["out_dir"], blind_seed=17,
        pair_manifests=manifests,
    )
    assert len(list((project["out_dir"] / "renders").glob("*.mp4"))) == 4
    mood_manifest = mood_manifests[0]
    energetic = next(manifest for manifest in manifests if manifest.mood == "energetic")
    calm = next(manifest for manifest in manifests if manifest.mood == "calm")
    expected_a = energetic.cutter_video_path if mood_manifest.a_is_energetic else calm.cutter_video_path
    expected_b = calm.cutter_video_path if mood_manifest.a_is_energetic else energetic.cutter_video_path
    import filecmp
    assert filecmp.cmp(mood_manifest.dataset_a_path, expected_a, shallow=False)
    assert filecmp.cmp(mood_manifest.dataset_b_path, expected_b, shallow=False)


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
            "--phase", "development",
            "--seed", "0", "--blind-seed", "1",
        ],
        capture_output=True, text=True,
    )
    assert gen.returncode == 0, gen.stderr
    assert (out_dir / "answer_key.json").exists()
    assert (out_dir / "dataset" / "pair001" / "A.mp4").exists()

    answer_key = AnswerKey.load(out_dir / "answer_key.json")
    mood_answer_key = MoodAnswerKey.load(out_dir / "mood_answer_key.json")
    raters = [f"rater{i}" for i in range(1, 6)]
    chosen = "A" if answer_key.assignments["pair001"].a_is_cutter else "B"
    ratings_csv = out_dir / "ratings.csv"
    ratings_csv.write_text("pair_id,rater_id,chosen_video,reason,timestamp\n" + "".join(
        f"pair001,{rater},{chosen},,2026-01-01T00:00:00Z\n" for rater in raters
    ), encoding="utf-8")
    ingest_args = [sys.executable, "-m", "poc", "eval", "ingest", "--out", str(out_dir), "--ratings", str(ratings_csv)]
    ingest = subprocess.run(ingest_args, capture_output=True, text=True)
    assert ingest.returncode == 0, ingest.stderr
    duplicate_ingest = subprocess.run(ingest_args, capture_output=True, text=True)
    assert duplicate_ingest.returncode == 0, duplicate_ingest.stderr
    assert "duplicate" in duplicate_ingest.stderr
    assert (out_dir / "ratings.jsonl").exists()

    mood_csv = out_dir / "m3.csv"
    energetic_side = mood_answer_key.energetic_side["mood001"]
    mood_csv.write_text(
        "mood_pair_id,rater_id,guessed_energetic,timestamp\n" + "".join(
            f"mood001,{rater},{energetic_side},2026-01-01T00:00:00Z\n" for rater in raters
        ), encoding="utf-8",
    )
    output_ids = ["pair001_A", "pair001_B"]
    for metric, header, value in (
        ("M4", "output_id,rater_id,would_post,timestamp", "yes"),
        ("M5", "output_id,rater_id,in_time_rating,timestamp", "4"),
    ):
        csv_path = out_dir / f"{metric.lower()}.csv"
        csv_path.write_text(header + "\n" + "".join(
            f"{output_id},{rater},{value},2026-01-01T00:00:00Z\n"
            for output_id in output_ids for rater in raters
        ), encoding="utf-8")
        metric_ingest = subprocess.run(
            [sys.executable, "-m", "poc", "eval", "ingest", "--metric", metric,
             "--out", str(out_dir), "--ratings", str(csv_path)],
            capture_output=True, text=True,
        )
        assert metric_ingest.returncode == 0, metric_ingest.stderr
    mood_ingest = subprocess.run(
        [sys.executable, "-m", "poc", "eval", "ingest", "--metric", "M3",
         "--out", str(out_dir), "--ratings", str(mood_csv)],
        capture_output=True, text=True,
    )
    assert mood_ingest.returncode == 0, mood_ingest.stderr

    report_args = [sys.executable, "-m", "poc", "eval", "report", "--out", str(out_dir)]
    undeclared_report = subprocess.run(report_args, capture_output=True, text=True)
    assert undeclared_report.returncode == 2
    assert "missing responses: unchecked" in undeclared_report.stdout

    partial_report = subprocess.run(
        report_args + [part for rater in raters[:4] for part in ("--expected-rater", rater)],
        capture_output=True, text=True,
    )
    assert partial_report.returncode == 2
    assert "collection complete: no" in partial_report.stdout
    assert "exactly 5 declared raters=not generated" in partial_report.stdout

    report = subprocess.run(
        report_args + [part for rater in raters for part in ("--expected-rater", rater)],
        capture_output=True, text=True,
    )
    assert "M2: pairwise preference" in report.stdout
    assert "cutter wins       : 5" in report.stdout
    assert "M2 valid responses: 5/5" in report.stdout
    assert "M3 (mood id): 5/5 correct" in report.stdout
    assert "5/5 responses" in report.stdout
    assert "M4 (usability): 2/2 outputs pass" in report.stdout
    assert "10/10 responses" in report.stdout
    assert "M5 (sync): mean 4.00/5 from 10 ratings" in report.stdout
    assert "collection complete: yes" in report.stdout
    assert "evaluation phase: development" in report.stdout
    assert "official verdict eligible: no" in report.stdout
    assert "duplicate M2 ratings ignored: 5" in report.stdout
    assert "DIAGNOSTIC ONLY" in report.stdout
    assert report.returncode == 2


def test_cli_eval_ingest_warns_on_unknown_pair(project):
    out_dir = project["out_dir"]
    subprocess.run(
        [
            sys.executable, "-m", "poc", "eval", "generate",
            "--footage-set", f"setA={project['footage_dir']}",
            "--music", f"trackA={project['music_path']}",
            "--mood", "energetic", "--duration", "8", "--out", str(out_dir), "--phase", "development",
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
