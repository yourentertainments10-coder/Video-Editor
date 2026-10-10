from __future__ import annotations

from pathlib import Path

from poc.__main__ import _build_parser, _eval_generate_command, _prepare_output_dir, _validate_media_inputs
from poc.execute.render import MediaProbe


def test_mvp_duration_is_rejected_before_media_probe(monkeypatch, tmp_path):
    def unexpected_probe(*args, **kwargs):
        raise AssertionError("duration validation should happen before probing")

    monkeypatch.setattr("poc.__main__.probe", unexpected_probe)
    message = _validate_media_inputs(14.9, [], [], allow_development=False)
    assert message == "duration must be 15-30 seconds for MVP inputs"


def test_mvp_requires_five_supported_video_files(tmp_path):
    footage_dir = tmp_path / "footage"
    footage_dir.mkdir()
    for index in range(4):
        (footage_dir / f"clip{index}.mp4").write_bytes(b"not probed")
    message = _validate_media_inputs(
        15, [("setA", footage_dir)], [], allow_development=False
    )
    assert "MVP requires 5-20 (.mp4/.mov)" in message


def test_mvp_requires_four_times_target_footage_duration(monkeypatch, tmp_path):
    footage_dir = tmp_path / "footage"
    footage_dir.mkdir()
    for index in range(5):
        (footage_dir / f"clip{index}.mp4").write_bytes(b"synthetic")
    music_path = tmp_path / "track.mp3"
    music_path.write_bytes(b"synthetic")

    def fake_probe(path: Path, ffmpeg_exe: str) -> MediaProbe:
        if path == music_path.resolve():
            return MediaProbe(duration_ms=120_000, fps=None, has_video=False, has_audio=True)
        return MediaProbe(duration_ms=10_000, fps=30.0, has_video=True, has_audio=False)

    monkeypatch.setattr("poc.__main__.probe", fake_probe)
    message = _validate_media_inputs(
        15,
        [("setA", footage_dir)],
        [("trackA", music_path)],
        allow_development=False,
    )
    assert "MVP requires at least 4x the target (60.0s for a 15s edit)" in message


def test_development_mode_explicitly_skips_mvp_media_limits(tmp_path):
    message = _validate_media_inputs(8, [], [], allow_development=True)
    assert message is None


def test_official_dataset_requires_clean_worktree(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("poc.__main__.is_git_worktree_dirty", lambda: True)
    args = _build_parser().parse_args([
        "eval", "generate",
        "--footage-set", "set_a=footage",
        "--music", "track_a=music.mp3",
        "--duration", "20",
        "--out", str(tmp_path / "official_eval"),
    ])
    assert _eval_generate_command(args) == 1
    assert "requires a clean tracked worktree" in capsys.readouterr().err


def test_output_parent_must_be_a_directory(tmp_path):
    output_parent = tmp_path / "not-a-directory"
    output_parent.write_text("file", encoding="utf-8")
    assert "exists but is not a directory" in _prepare_output_dir(output_parent)
