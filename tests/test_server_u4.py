import io
import time
from pathlib import Path

import av
import pytest
from fastapi.testclient import TestClient

from poc.server.app import create_app

FIX = Path(__file__).parent / "fixtures" / "ingest"
MUSIC = Path(__file__).resolve().parent.parent / "experiments" / "beat_flash" / "fixtures" / "synth_128.wav"
TOKEN = "test-token-123"


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.headers.update({"X-Editor-Token": TOKEN})
        yield c


def _project(client):
    r = client.post("/api/projects", json={"name": "U4"})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _import(client, pid, *paths):
    r = client.post(f"/api/projects/{pid}/assets", json={"paths": [str(p) for p in paths]})
    assert r.status_code == 200, r.text
    return {a["file_name"]: a for a in r.json()["assets"]}


def _wait(client, jid, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish in time")


def _two_clips(client, pid):
    assets = _import(client, pid, FIX / "tone_a.mp4", FIX / "tone_b.mp4")
    a, b = assets["tone_a.mp4"], assets["tone_b.mp4"]
    t = client.get(f"/api/projects/{pid}/timeline").json()
    tl = t["timeline"]
    tl["duration_ticks"] = 4000
    tl["clips"] = [
        {"clip_id": "c1", "asset_id": a["asset_id"], "track_id": "video",
         "start_tick": 0, "duration_ticks": 2000, "source_in_ms": 1000, "source_out_ms": 3000},
        {"clip_id": "c2", "asset_id": b["asset_id"], "track_id": "video",
         "start_tick": 2000, "duration_ticks": 2000, "source_in_ms": 1000, "source_out_ms": 3000},
    ]
    r = client.put(f"/api/projects/{pid}/timeline", json={"base_revision": t["revision"], "timeline": tl})
    assert r.status_code == 200, r.text


def _audio_info(data: bytes):
    with av.open(io.BytesIO(data)) as c:
        s = c.streams.audio[0]
        samples = sum(f.samples for f in c.decode(audio=0))
        return s.codec_context.name, s.codec_context.sample_rate, s.codec_context.channels, samples


def test_render_job_uses_the_profile_encoder_and_reports_timings(client):
    pid = _project(client)
    _two_clips(client, pid)
    j = _wait(client, client.post(f"/api/projects/{pid}/render", json={"profile": "preview_540p"}).json()["job_id"])
    assert j["status"] == "done", j
    assert j["encoder"] == {"crf": 28, "preset": "veryfast", "audio_bitrate": "128k"}
    assert {"audio", "video", "mux", "verify", "total"} <= set(j["timings"])
    assert j["report"]["ok"] is True, j["report"]


def test_audio_preview_endpoint_serves_aac_for_music(client):
    pid = _project(client)
    music = _import(client, pid, MUSIC)["synth_128.wav"]
    r = client.get(f"/api/projects/{pid}/assets/{music['asset_id']}/audio")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mp4")
    codec, rate, channels, samples = _audio_info(r.content)
    assert (codec, rate, channels) == ("aac", 48000, 2)
    assert samples == pytest.approx(music["duration_ms"] * 48, rel=0.02)
    again = client.get(f"/api/projects/{pid}/assets/{music['asset_id']}/audio")
    assert again.content == r.content          # cached, deterministic


def test_audio_preview_supports_range_and_cookie_auth(tmp_path):
    app = create_app(data_dir=tmp_path / "d", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.get("/")
        c.headers.update({"X-Editor-Token": TOKEN})
        pid = _project(c)
        music = _import(c, pid, MUSIC)["synth_128.wav"]
        del c.headers["X-Editor-Token"]
        r = c.get(f"/api/projects/{pid}/assets/{music['asset_id']}/audio", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206
        assert len(r.content) == 100
        assert c.post(f"/api/projects/{pid}/assets/{music['asset_id']}/audio").status_code in (401, 405)


def test_audio_preview_for_video_without_audio_is_404(client):
    pid = _project(client)
    assets = _import(client, pid, FIX / "solid_red.mp4", FIX / "tone_a.mp4")
    assert client.get(f"/api/projects/{pid}/assets/{assets['solid_red.mp4']['asset_id']}/audio").status_code == 404
    assert client.get(f"/api/projects/{pid}/assets/{assets['tone_a.mp4']['asset_id']}/audio").status_code == 200
