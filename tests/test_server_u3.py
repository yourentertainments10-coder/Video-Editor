import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.server.app import create_app

FIX = Path(__file__).parent / "fixtures" / "ingest"
MUSIC = Path(__file__).resolve().parent.parent / "experiments" / "beat_flash" / "fixtures" / "synth_128.wav"
TOKEN = "test-token-123"
VIDEOS = ("tone_a.mp4", "tone_b.mp4", "solid_red.mp4", "solid_green.mp4", "solid_blue.mp4")


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.headers.update({"X-Editor-Token": TOKEN})
        yield c


def _project(client):
    r = client.post("/api/projects", json={"name": "Auto"})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _import_paths(client, pid, paths):
    r = client.post(f"/api/projects/{pid}/assets", json={"paths": [str(p) for p in paths]})
    assert r.status_code == 200, r.text
    return {a["file_name"]: a for a in r.json()["assets"]}


def _wait(client, jid, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish in time")


def _timeline(client, pid):
    return client.get(f"/api/projects/{pid}/timeline").json()


def test_auto_edit_builds_a_beat_synced_renderable_timeline(client):
    pid = _project(client)
    assets = _import_paths(client, pid, [FIX / v for v in VIDEOS] + [MUSIC])
    music_id = assets["synth_128.wav"]["asset_id"]
    before = _timeline(client, pid)
    r = client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": before["revision"], "music_asset_id": music_id,
        "mood": "energetic", "duration_s": 6})
    assert r.status_code == 202, r.text
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "done", j

    after = _timeline(client, pid)
    assert after["revision"] == before["revision"] + 1
    tl = after["timeline"]
    video = [c for c in tl["clips"] if c["track_id"] == "video"]
    music = [c for c in tl["clips"] if c["track_id"] == "music"]
    assert len(video) >= 2
    assert len(music) == 1 and music[0]["asset_id"] == music_id

    beats = [m["position_tick"] for m in tl["markers"] if m["kind"] == "beat"]
    assert beats
    for start in {c["start_tick"] for c in video} - {0}:
        assert min(abs(start - b) for b in beats) <= 34   # within one frame at 30 fps

    assert client.post(f"/api/projects/{pid}/check").json() == {"ok": True, "error": None}


def test_auto_edit_rejects_a_stale_revision(client):
    pid = _project(client)
    assets = _import_paths(client, pid, [FIX / v for v in VIDEOS] + [MUSIC])
    rev = _timeline(client, pid)["revision"]
    r = client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": rev - 1 if rev > 1 else rev + 5,
        "music_asset_id": assets["synth_128.wav"]["asset_id"], "mood": "energetic", "duration_s": 6})
    assert r.status_code == 409


@pytest.mark.parametrize("bad", [
    {"music_asset_id": "nope", "mood": "energetic", "duration_s": 6},
    {"mood": "energetic", "duration_s": 6},
    {"music_asset_id": "VIDEO", "mood": "energetic", "duration_s": 6},
    {"music_asset_id": "MUSIC", "mood": "angry", "duration_s": 6},
    {"music_asset_id": "MUSIC", "mood": "calm", "duration_s": 0},
])
def test_auto_edit_validates_its_request(client, bad):
    pid = _project(client)
    assets = _import_paths(client, pid, [FIX / v for v in VIDEOS] + [MUSIC])
    body = dict(bad)
    if body.get("music_asset_id") == "VIDEO":
        body["music_asset_id"] = assets["tone_a.mp4"]["asset_id"]
    if body.get("music_asset_id") == "MUSIC":
        body["music_asset_id"] = assets["synth_128.wav"]["asset_id"]
    body["base_revision"] = _timeline(client, pid)["revision"]
    assert client.post(f"/api/projects/{pid}/auto-edit", json=body).status_code == 422


def test_auto_edit_failure_is_explained_and_leaves_timeline_untouched(client):
    pid = _project(client)
    assets = _import_paths(client, pid, [FIX / "solid_red.mp4", MUSIC])
    before = _timeline(client, pid)
    r = client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": before["revision"], "music_asset_id": assets["synth_128.wav"]["asset_id"],
        "mood": "energetic", "duration_s": 60})
    assert r.status_code == 202
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "failed"
    assert j["error"]
    assert _timeline(client, pid)["revision"] == before["revision"]
