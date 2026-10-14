"""Delivered (AAC-encoded) audio must meet the true-peak limit, not just the WAV before encoding."""
import hashlib
from pathlib import Path

import pytest

from poc.project_assets import ProjectAssetLibrary
from poc.render.audio import encode_delivery_audio, measure_loudness, render_audio_track
from poc.render.editor import EditorRenderer
from poc.render.export import get_profile
from poc.render.transitions import Join, Segment
from poc.timeline import (AudioProperties, EditorClip, EditorSettings, EditorTimeline, Layer,
                          TimeBase, Track)

FIX = Path(__file__).parent / "fixtures" / "ingest"
MUSIC = Path(__file__).resolve().parent.parent / "experiments" / "beat_flash" / "fixtures" / "synth_128.wav"
LAYERS = [Layer(layer_id="v", name="Visual", order=0), Layer(layer_id="a", name="Audio", order=1)]
TRACKS = [Track(track_id="video", kind="video", layer_id="v"),
          Track(track_id="music", kind="music", layer_id="a")]


def _timeline(tmp_path, profile):
    lib = ProjectAssetLibrary.create(tmp_path / "project", project_id="tp")
    a, b, music = (lib.import_asset(p).media_asset for p in (FIX / "tone_a.mp4", FIX / "tone_b.mp4", MUSIC))
    clips = [
        EditorClip(clip_id="a", asset_id=a.asset_id, track_id="video", start_tick=0,
                   duration_ticks=2000, source_in_ms=500, source_out_ms=2500),
        EditorClip(clip_id="b", asset_id=b.asset_id, track_id="video", start_tick=2000,
                   duration_ticks=2000, source_in_ms=500, source_out_ms=2500),
        EditorClip(clip_id="m", asset_id=music.asset_id, track_id="music", start_tick=0,
                   duration_ticks=4000, source_in_ms=0, source_out_ms=4000,
                   audio=AudioProperties(gain_db=0)),
    ]
    p = get_profile(profile)
    tl = EditorTimeline(project_id="tp", timeline_id="t", duration_ticks=4000,
                        time_base=TimeBase(ticks_per_second=1000),
                        settings=EditorSettings(fps_num=p.fps, fps_den=1, width=640, height=360),
                        assets=[a, b, music], layers=LAYERS, tracks=TRACKS, clips=clips)
    return lib, tl, p


@pytest.mark.parametrize("profile", ["preview_540p", "youtube_1080p"])
def test_render_with_sharp_music_passes_true_peak_after_encoding(tmp_path, profile):
    lib, tl, p = _timeline(tmp_path, profile)
    res = EditorRenderer().render(EditorRenderer().compile(tl, lib), out=tmp_path / "o.mp4", profile=p)
    assert res.report.ok, res.report.problems
    lufs, peak = measure_loudness(res.out_path)
    assert peak <= p.true_peak_db + 1e-6
    assert abs(lufs - p.target_lufs) <= 1.0
    assert res.audio["true_peak"] == pytest.approx(peak, abs=0.05)
    assert res.audio["attempts"] >= 1


def _mixed_wav(tmp_path):
    segs = [Segment(FIX / "tone_a.mp4", 500, 2500), Segment(FIX / "tone_b.mp4", 500, 2500)]
    return render_audio_track(segs, [Join("cut", 0)], tmp_path / "mix.wav", fps=30)


def test_delivery_audio_is_deterministic(tmp_path):
    mixed = _mixed_wav(tmp_path)
    one = encode_delivery_audio(mixed, tmp_path / "one.m4a", bitrate="128k")
    two = encode_delivery_audio(mixed, tmp_path / "two.m4a", bitrate="128k")
    assert one == two
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert digest(tmp_path / "one.m4a") == digest(tmp_path / "two.m4a")
    assert not list(tmp_path.glob("*.normalised.wav"))


def test_delivery_audio_of_silence_is_left_silent(tmp_path):
    segs = [Segment(FIX / "solid_red.mp4", 0, 2000)]
    silent = render_audio_track(segs, [], tmp_path / "silent.wav", fps=30)
    report = encode_delivery_audio(silent, tmp_path / "silent.m4a")
    assert report["attempts"] == 1
    assert report["lufs"] <= -70


def test_auto_edit_preview_render_passes_verification(tmp_path):
    # Regression: this exact auto-edit verified at +0.20 dBTP in preview_540p (limit -0.5),
    # because the WAV was normalised to -1 dBTP and AAC encoding added 1.1 dB.
    import time
    from fastapi.testclient import TestClient
    from poc.server.app import create_app

    names = ("tone_a.mp4", "tone_b.mp4", "solid_red.mp4", "solid_green.mp4", "solid_blue.mp4")
    app = create_app(data_dir=tmp_path / "data", token="t", allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.headers.update({"X-Editor-Token": "t"})
        pid = c.post("/api/projects", json={"name": "tp"}).json()["project_id"]
        assets = {a["file_name"]: a for a in c.post(
            f"/api/projects/{pid}/assets",
            json={"paths": [str(FIX / n) for n in names] + [str(MUSIC)]}).json()["assets"]}
        rev = c.get(f"/api/projects/{pid}/timeline").json()["revision"]

        def wait(job_id):
            end = time.time() + 300
            while time.time() < end:
                job = c.get(f"/api/jobs/{job_id}").json()
                if job["status"] in ("done", "failed", "cancelled"):
                    return job
                time.sleep(0.2)
            raise AssertionError("job did not finish")

        edit = wait(c.post(f"/api/projects/{pid}/auto-edit", json={
            "base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"],
            "mood": "energetic", "duration_s": 8}).json()["job_id"])
        assert edit["status"] == "done", edit
        job = wait(c.post(f"/api/projects/{pid}/render", json={"profile": "preview_540p"}).json()["job_id"])
        assert job["status"] == "done", job
        assert job["report"]["ok"] is True, job["report"]["problems"]
        assert job["report"]["measured"]["true_peak"] <= -1.0
