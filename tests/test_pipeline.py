"""Integration tests for poc.pipeline (Step 8): the full, wired-together
end-to-end pipeline. Every test here generates small synthetic media with
FFmpeg's lavfi sources and drives poc.pipeline.run_pipeline (or, once, the
actual `python -m poc run` CLI as a subprocess) rather than calling
Step 5/6/7/4's own functions directly -- this is what proves the wiring
itself works, not any individual component in isolation.

Music needs genuine rhythmic content for the beat tracker to find
anything at all (a pure sine tone has no detectable beats -- confirmed
during Step 8 development, see test_missing_beats_in_music_fails_clearly
below for that exact failure mode as a *feature*, not a bug). A simple
periodic click (a short tone burst every 0.5 s = 120 BPM) is enough.

A note on output duration precision (found during Step 8 integration,
not present in any single component's own tests): Step 6/7's candidate
generation steps in_ms by exactly 250 ms (docs/MVP.md Section 6,
verbatim), which is 7.5 frames at 30 fps -- not a whole number. Roughly
half of all selected clips therefore start on a source timestamp that
does not land exactly on the source's own frame grid. Step 4's renderer
re-encodes each segment with an accurate `-ss` seek, and a segment whose
seek point falls mid-frame can come out exactly one frame short. This is
a genuine interaction between two independently-correct, independently-
approved components (Step 4's renderer matches docs/MVP.md's renderer
notes exactly; Step 7's candidate stepping matches docs/MVP.md Section 6
exactly) that only surfaces once they are wired together -- exactly what
Step 8 exists to discover, not silently fix. Tests here assert duration
within a small, explicit, documented tolerance rather than to the exact
frame, and one test measures and bounds the effect directly so a future
regression that makes it worse is caught.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import pytest

from poc.execute.render import probe
from poc.pipeline import PipelineError, run_pipeline
from poc.timeline.schema import Timeline
from poc.timeline.validate import validate

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
REAL_MUSIC = (
    Path(__file__).resolve().parent.parent
    / "experiments" / "beat_flash" / "fixtures" / "real" / "Chor.mp3"
)


def _make_footage(path: Path, duration_s: float, size: str = "320x240", fps: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate={fps}", "-t", str(duration_s), str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _make_click_music(path: Path, duration_s: float, bpm: float = 120.0) -> None:
    """A short tone burst every 60/bpm seconds -- real, detectable rhythm,
    not a pure tone (which has zero beats -- see the module docstring)."""
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
    footage_dir = tmp_path / "project" / "footage"
    for i in range(5):
        _make_footage(footage_dir / f"clip{i}.mp4", duration_s=6.0)
    music_path = tmp_path / "project" / "song.mp3"
    _make_click_music(music_path, duration_s=20.0)
    return {"footage_dir": footage_dir, "music_path": music_path, "out_dir": tmp_path / "output"}


# ---------------------------------------------------------------------------
# happy path: cutter mode
# ---------------------------------------------------------------------------

def test_full_pipeline_cutter_mode_produces_every_artifact(project):
    output_path = project["out_dir"] / "final.mp4"
    result = run_pipeline(
        project["footage_dir"], project["music_path"], "energetic", 10.0, output_path
    )

    assert result.music_map_path.exists()
    assert len(result.footage_map_paths) == 5
    for p in result.footage_map_paths.values():
        assert p.exists()
    assert result.timeline_path.exists()
    assert output_path.exists()

    tl = Timeline.load(result.timeline_path)
    assert tl.provenance.generator == "cutter-v0"
    assert validate(tl) == []


def test_full_pipeline_output_is_a_playable_video_within_documented_tolerance(project):
    output_path = project["out_dir"] / "final.mp4"
    result = run_pipeline(
        project["footage_dir"], project["music_path"], "energetic", 10.0, output_path
    )
    media = probe(output_path, FFMPEG_EXE)
    assert media.has_video
    assert media.has_audio
    assert media.fps == pytest.approx(30.0, abs=0.5)

    expected_s = result.timeline.duration_ms / 1000.0
    n_frames, decoded_s = imageio_ffmpeg.count_frames_and_secs(str(output_path))
    # See the module docstring: a segment whose source seek point falls
    # off the source's frame grid can render one frame short. Bounded,
    # documented tolerance, not an exact match.
    assert decoded_s == pytest.approx(expected_s, abs=0.5)
    assert n_frames >= result.timeline.duration_frames - len(result.timeline.clips)


def _without_timestamp(tl: Timeline) -> dict:
    """Timeline equality modulo provenance.created_at, which is a fresh
    wall-clock timestamp on every call by design (Step 2's Provenance
    model) and is expected to differ between two otherwise-identical runs."""
    data = tl.model_dump()
    data["provenance"].pop("created_at", None)
    return data


def test_full_pipeline_is_deterministic_across_repeated_runs(project):
    output_a = project["out_dir"] / "a.mp4"
    output_b = project["out_dir"] / "b.mp4"
    result_a = run_pipeline(project["footage_dir"], project["music_path"], "calm", 10.0, output_a)
    result_b = run_pipeline(project["footage_dir"], project["music_path"], "calm", 10.0, output_b)
    assert _without_timestamp(result_a.timeline) == _without_timestamp(result_b.timeline)


def test_full_pipeline_writes_relative_posix_paths_in_the_timeline(project):
    output_path = project["out_dir"] / "final.mp4"
    result = run_pipeline(
        project["footage_dir"], project["music_path"], "energetic", 10.0, output_path
    )
    for clip in result.timeline.clips:
        assert not clip.source.path.startswith("/")
        assert ":" not in clip.source.path  # no Windows drive letter
    assert not result.timeline.music.path.startswith("/")


# ---------------------------------------------------------------------------
# baseline mode
# ---------------------------------------------------------------------------

def test_full_pipeline_baseline_mode(project):
    output_path = project["out_dir"] / "baseline.mp4"
    result = run_pipeline(
        project["footage_dir"], project["music_path"], "calm", 10.0, output_path,
        baseline=True, seed=42,
    )
    assert result.timeline.provenance.generator == "baseline-v0"
    assert result.timeline.provenance.seed == 42
    assert validate(result.timeline) == []
    media = probe(output_path, FFMPEG_EXE)
    assert media.has_video and media.has_audio


def test_baseline_mode_deterministic_with_fixed_seed(project):
    output_a = project["out_dir"] / "a.mp4"
    output_b = project["out_dir"] / "b.mp4"
    result_a = run_pipeline(
        project["footage_dir"], project["music_path"], "calm", 10.0, output_a, baseline=True, seed=7
    )
    result_b = run_pipeline(
        project["footage_dir"], project["music_path"], "calm", 10.0, output_b, baseline=True, seed=7
    )
    assert _without_timestamp(result_a.timeline) == _without_timestamp(result_b.timeline)


# ---------------------------------------------------------------------------
# error handling: fail clearly
# ---------------------------------------------------------------------------

def test_missing_footage_folder_fails_clearly(project):
    missing_dir = project["footage_dir"].parent / "does_not_exist"
    with pytest.raises(PipelineError, match="does not exist"):
        run_pipeline(missing_dir, project["music_path"], "energetic", 10.0, project["out_dir"] / "o.mp4")


def test_empty_footage_folder_fails_clearly(tmp_path, project):
    empty_dir = tmp_path / "empty_footage"
    empty_dir.mkdir()
    with pytest.raises(PipelineError, match="no footage found"):
        run_pipeline(empty_dir, project["music_path"], "energetic", 10.0, project["out_dir"] / "o.mp4")


def test_nonexistent_music_file_fails_clearly(project):
    missing_music = project["music_path"].parent / "no_such_song.mp3"
    with pytest.raises(PipelineError, match="does not exist"):
        run_pipeline(project["footage_dir"], missing_music, "energetic", 10.0, project["out_dir"] / "o.mp4")


def test_unanalyzable_music_file_fails_clearly(project, tmp_path):
    bogus_music = tmp_path / "project" / "not_audio.mp3"
    bogus_music.write_text("this is not an audio file")
    with pytest.raises(PipelineError, match="music analysis failed"):
        run_pipeline(project["footage_dir"], bogus_music, "energetic", 10.0, project["out_dir"] / "o.mp4")


def test_music_with_no_detectable_beats_fails_clearly(project, tmp_path):
    # A pure sine tone has no rhythmic onsets at all: zero beats detected.
    pure_tone = tmp_path / "project" / "tone.mp3"
    subprocess.run(
        [FFMPEG_EXE, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=10", "-c:a", "libmp3lame", str(pure_tone)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    with pytest.raises(PipelineError, match="could not be satisfied"):
        run_pipeline(project["footage_dir"], pure_tone, "energetic", 10.0, project["out_dir"] / "o.mp4")


def test_duration_exceeding_available_beats_truncates_gracefully_not_an_error(project):
    # Step 7's build_grid (already approved) stops at the last beat at or
    # before the requested duration -- if beats run out after at least one
    # slot was built, it returns a shorter-than-requested but valid
    # Timeline rather than raising. GridError is reserved for when not
    # even one slot can be built at all (see test_music_with_no_detectable_
    # beats_fails_clearly for that case). A duration merely longer than
    # the available music is not, by itself, a failure.
    output_path = project["out_dir"] / "o.mp4"
    result = run_pipeline(
        project["footage_dir"], project["music_path"], "energetic", 500.0, output_path
    )
    assert result.timeline.duration_ms < 500_000
    assert validate(result.timeline) == []
    assert output_path.exists()


def test_duration_shorter_than_any_possible_slot_fails_clearly(project):
    # calm mood at typical energy needs several beats per cut; a duration
    # far below even a single beat's interval cannot build one slot.
    with pytest.raises(PipelineError, match="could not be satisfied"):
        run_pipeline(
            project["footage_dir"], project["music_path"], "calm", 0.01, project["out_dir"] / "o.mp4"
        )


def test_footage_set_too_small_for_slot_length_fails_clearly(tmp_path, project):
    tiny_dir = tmp_path / "tiny_footage"
    _make_footage(tiny_dir / "tiny.mp4", duration_s=0.5)  # far shorter than any slot + margins
    with pytest.raises(PipelineError, match="no valid edit"):
        run_pipeline(tiny_dir, project["music_path"], "energetic", 10.0, project["out_dir"] / "o.mp4")


def test_invalid_mood_fails_clearly(project):
    with pytest.raises(PipelineError, match="invalid request"):
        run_pipeline(
            project["footage_dir"], project["music_path"], "dramatic", 10.0, project["out_dir"] / "o.mp4"
        )


# ---------------------------------------------------------------------------
# the actual CLI, as a real subprocess
# ---------------------------------------------------------------------------

def test_cli_run_command_end_to_end(project):
    output_path = project["out_dir"] / "cli_final.mp4"
    proc = subprocess.run(
        [
            sys.executable, "-m", "poc", "run",
            "--footage", str(project["footage_dir"]),
            "--music", str(project["music_path"]),
            "--mood", "energetic",
            "--duration", "10",
            "--output", str(output_path),
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert output_path.exists()
    assert "cutter-v0" in proc.stdout
    assert (project["out_dir"] / "timeline.json").exists()
    assert (project["out_dir"] / "music_map.json").exists()


def test_cli_reports_errors_on_stderr_with_nonzero_exit(project):
    proc = subprocess.run(
        [
            sys.executable, "-m", "poc", "run",
            "--footage", str(project["footage_dir"].parent / "missing"),
            "--music", str(project["music_path"]),
            "--mood", "energetic",
            "--duration", "10",
            "--output", str(project["out_dir"] / "o.mp4"),
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr


# ---------------------------------------------------------------------------
# one real, local music fixture (skipped when absent -- gitignored)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_MUSIC.exists(), reason="real music fixture not present locally (gitignored)")
def test_real_music_fixture_end_to_end():
    # pytest's tmp_path lands on the system temp drive, which can differ
    # from the drive this repo (and REAL_MUSIC) lives on -- and the
    # Timeline path contract needs footage and music to share a common
    # parent (see poc.pipeline's _common_base_dir). Use a scratch
    # directory on the same drive as the repo instead of tmp_path.
    import shutil
    import tempfile

    repo_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(dir=str(repo_root)) as tmp:
        tmp_path = Path(tmp)
        footage_dir = tmp_path / "footage"
        for i in range(5):
            _make_footage(footage_dir / f"clip{i}.mp4", duration_s=8.0)
        output_path = tmp_path / "output" / "final.mp4"

        result = run_pipeline(footage_dir, REAL_MUSIC, "energetic", 15.0, output_path)

        assert validate(result.timeline) == []
        media = probe(output_path, FFMPEG_EXE)
        assert media.has_video and media.has_audio
        assert media.duration_ms == pytest.approx(result.timeline.duration_ms, abs=500)
