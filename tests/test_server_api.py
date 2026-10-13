import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.server.app import create_app, validate_bind_host

FIX = Path(__file__).parent / "fixtures" / "ingest"
UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
TOKEN = "test-token-123"


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.headers.update({"X-Editor-Token": TOKEN})
        yield c


def _project(client):
    r = client.post("/api/projects", json={"name": "Demo"})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _import(client, pid, *names):
    r = client.post(f"/api/projects/{pid}/assets", json={"paths": [str(FIX / n) for n in names]})
    assert r.status_code == 200, r.text
    return r.json()["assets"]


def _wait(client, jid, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish in time")


def _timeline_with_two_clips(client, pid):
    a, b = _import(client, pid, "tone_a.mp4", "tone_b.mp4")
    t = client.get(f"/api/projects/{pid}/timeline").json()
    tl = t["timeline"]
    tl["duration_ticks"] = 4000
    tl["clips"] = [
        {"clip_id": "c1", "asset_id": a["asset_id"], "track_id": "video",
         "start_tick": 0, "duration_ticks": 2000, "source_in_ms": 1000, "source_out_ms": 3000},
        {"clip_id": "c2", "asset_id": b["asset_id"], "track_id": "video",
         "start_tick": 2000, "duration_ticks": 2000, "source_in_ms": 1000, "source_out_ms": 3000,
         "transition": {"transition_id": "fade", "duration_ticks": 500}},
    ]
    r = client.put(f"/api/projects/{pid}/timeline",
                   json={"base_revision": t["revision"], "timeline": tl})
    assert r.status_code == 200, r.text


# ---------------- security ----------------

def test_requests_without_token_are_rejected(tmp_path):
    app = create_app(data_dir=tmp_path / "d", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 401
        assert c.get("/api/health", headers={"X-Editor-Token": "wrong"}).status_code == 401
        assert c.get("/api/health", headers={"X-Editor-Token": TOKEN}).status_code == 200


def test_foreign_host_header_is_rejected(client):
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::", "example.com"])
def test_server_refuses_non_loopback_bind(host):
    with pytest.raises(ValueError):
        validate_bind_host(host)


def test_loopback_bind_is_allowed():
    validate_bind_host("127.0.0.1")


def test_cross_origin_requests_get_no_cors_grant(client):
    r = client.options("/api/health", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") is None


def test_network_paths_cannot_be_imported(client):
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/assets", json={"paths": ["https://example.com/a.mp4"]})
    assert r.status_code == 422


def test_unknown_ids_are_404(client):
    pid = _project(client)
    assert client.get(f"/api/projects/{pid}/assets/nope/proxy").status_code == 404
    assert client.get("/api/projects/does-not-exist/assets").status_code == 404
    assert client.get("/api/jobs/does-not-exist").status_code == 404


# ---------------- media ----------------

def test_import_lists_assets_with_probe_info(client):
    pid = _project(client)
    assets = _import(client, pid, "tone_a.mp4", "exif_rot6.jpg")
    by_name = {a["file_name"]: a for a in assets}
    assert by_name["tone_a.mp4"]["kind"] == "video"
    assert by_name["tone_a.mp4"]["has_audio"] is True
    assert (by_name["exif_rot6.jpg"]["width"], by_name["exif_rot6.jpg"]["height"]) == (480, 640)
    listed = client.get(f"/api/projects/{pid}/assets").json()["assets"]
    assert {a["asset_id"] for a in listed} == {a["asset_id"] for a in assets}


def test_proxy_supports_range_requests(client):
    pid = _project(client)
    a = _import(client, pid, "tone_a.mp4")[0]
    url = f"/api/projects/{pid}/assets/{a['asset_id']}/proxy"
    full = client.get(url)
    assert full.status_code == 200
    assert full.headers["content-type"] == "video/mp4"
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100
    assert part.content == full.content[:100]


def test_thumbnail_is_jpeg(client):
    pid = _project(client)
    a = _import(client, pid, "tone_a.mp4")[0]
    r = client.get(f"/api/projects/{pid}/assets/{a['asset_id']}/thumbnails/0")
    assert r.status_code == 200
    assert r.content[:2] == b"\xff\xd8"


# ---------------- timeline ----------------

def test_new_project_has_default_tracks(client):
    pid = _project(client)
    tl = client.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    kinds = {t["track_id"]: t["kind"] for t in tl["tracks"]}
    assert kinds == {"video": "video", "overlay": "overlay", "voice": "audio", "music": "music"}
    assert tl["clips"] == []


def test_timeline_save_uses_revisions(client):
    pid = _project(client)
    t = client.get(f"/api/projects/{pid}/timeline").json()
    rev, tl = t["revision"], t["timeline"]
    tl["duration_ticks"] = 2000
    ok = client.put(f"/api/projects/{pid}/timeline", json={"base_revision": rev, "timeline": tl})
    assert ok.status_code == 200
    assert ok.json()["revision"] == rev + 1
    stale = client.put(f"/api/projects/{pid}/timeline", json={"base_revision": rev, "timeline": tl})
    assert stale.status_code == 409
    bad = client.put(f"/api/projects/{pid}/timeline",
                     json={"base_revision": rev + 1, "timeline": {"nonsense": 1}})
    assert bad.status_code == 422


def test_saved_timeline_survives_server_restart(tmp_path):
    data = tmp_path / "data"
    app = create_app(data_dir=data, token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.headers.update({"X-Editor-Token": TOKEN})
        pid = _project(c)
        _timeline_with_two_clips(c, pid)
    app2 = create_app(data_dir=data, token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app2) as c2:
        c2.headers.update({"X-Editor-Token": TOKEN})
        tl = c2.get(f"/api/projects/{pid}/timeline").json()["timeline"]
        assert [clip["clip_id"] for clip in tl["clips"]] == ["c1", "c2"]


# ---------------- render jobs ----------------

def test_render_job_produces_verified_file(client):
    pid = _project(client)
    _timeline_with_two_clips(client, pid)
    r = client.post(f"/api/projects/{pid}/render", json={"profile": "preview_540p"})
    assert r.status_code == 202, r.text
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "done", j
    assert j["progress"] == 1.0
    assert j["report"]["ok"] is True, j["report"]
    assert Path(j["output"]).is_file()


def test_render_job_can_be_cancelled(client):
    pid = _project(client)
    _timeline_with_two_clips(client, pid)
    jid = client.post(f"/api/projects/{pid}/render", json={"profile": "youtube_1080p"}).json()["job_id"]
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 202
    j = _wait(client, jid)
    assert j["status"] == "cancelled", j
    assert j["output"] is None


def test_unknown_profile_is_rejected(client):
    pid = _project(client)
    assert client.post(f"/api/projects/{pid}/render", json={"profile": "8k_ultra"}).status_code == 422


# ---------------- built UI ----------------

def test_index_page_carries_token_for_same_origin_ui(tmp_path):
    app = create_app(data_dir=tmp_path / "d", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert TOKEN in r.text


def test_built_ui_loads_nothing_from_the_internet():
    html = (UI_DIST / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'(src|href)\s*=\s*"(https?:)?//', html)