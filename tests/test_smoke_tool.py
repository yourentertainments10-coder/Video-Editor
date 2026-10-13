import json
import shutil
from pathlib import Path

from tools.smoke_render import main


FIX = Path(__file__).parent / "fixtures" / "ingest"


def test_smoke_tool_writes_success_report(tmp_path):
    footage = tmp_path / "footage"
    footage.mkdir()
    for name in ("tone_a.mp4", "tone_b.mp4", "solid_red.mp4", "solid_green.mp4", "solid_blue.mp4"):
        shutil.copyfile(FIX / name, footage / name)
    music = tmp_path / "music.wav"
    shutil.copyfile(
        Path(__file__).parent.parent / "experiments" / "beat_flash" / "fixtures" / "synth_128.wav",
        music,
    )
    out = tmp_path / "smoke"

    status = main([
        "--footage", str(footage), "--music", str(music), "--out", str(out),
        "--duration", "6", "--profile", "youtube_1080p",
    ])

    report_path = out / "smoke_report.json"
    assert report_path.is_file()
    assert (out / "smoke_report.md").is_file()
    assert (out / "probe.json").is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True, report.get("verify_report")
    assert status == 0
