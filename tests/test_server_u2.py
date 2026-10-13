from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.server.app import create_app

FIX = Path(__file__).parent / "fixtures" / "ingest"
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


def _save(client, pid, mutate):
    t = client.get(f"/api/projects/{pid}/timeline").json()
    tl = t["timeline"]
    mutate(tl)
    return client.put(f"/api/projects/{pid}/timeline",
                      json={"base_revision": t["revision"], "timeline": tl})


def _two_clips(client, pid):
    a, b = _import(client, pid, "tone_a.mp4", "tone_b.mp4")

    def mutate(tl):
        tl["duration_ticks"] = 4000
        tl["clips"] = [
            {"clip_id": "c1", "asset_id": a["asset_id"], "track_id": "video",
             "start_tick": 0, "duration_ticks": 2000, "source_in_ms": 1000, "source_out_ms": 3000},
            {"clip_id": "c2", "asset_id": b["asset_id"], "track_id": "video",
             "start_tick": 2000, "duration_ticks": 2000, "source_in_ms": 1000, "source_out_ms": 3000,
             "transition": {"transition_id": "fade", "duration_ticks": 500}},
        ]
    r = _save(client, pid, mutate)
    assert r.status_code == 200, r.text


def _one_clip_too_long(client, pid):
    a = _import(client, pid, "tone_a.mp4")[0]

    def mutate(tl):
        tl["duration_ticks"] = 1000
        tl["clips"] = [{"clip_id": "c1", "asset_id": a["asset_id"], "track_id": "video",
                        "start_tick": 0, "duration_ticks": 2000,
                        "source_in_ms": 0, "source_out_ms": 2000}]
    return _save(client, pid, mutate)


# ---------------- U1 follow-ups: streaming media + hardening ----------------

def test_index_sets_strict_httponly_session_cookie(tmp_path):
    app = create_app(data_dir=tmp_path / "d", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        cookie = c.get("/").headers.get("set-cookie", "").lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie


def test_index_has_strict_csp_and_no_store(tmp_path):
    app = create_app(data_dir=tmp_path / "d", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        r = c.get("/")
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "http" not in csp
        assert r.headers["cache-control"] == "no-store"


def test_media_streams_with_cookie_but_cookie_cannot_change_state(tmp_path):
    app = create_app(data_dir=tmp_path / "d", token=TOKEN, allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.get("/")  # receives the session cookie
        c.headers.update({"X-Editor-Token": TOKEN})
        pid = _project(c)
        a = _import(c, pid, "tone_a.mp4")[0]
        del c.headers["X-Editor-Token"]
        r = c.get(f"/api/projects/{pid}/assets/{a['asset_id']}/proxy", headers={"Range": "bytes=0-9"})
        assert r.status_code == 206
        assert len(r.content) == 10
        thumb = c.get(f"/api/projects/{pid}/assets/{a['asset_id']}/thumbnails/0")
        assert thumb.status_code == 200
        assert c.post("/api/projects", json={"name": "x"}).status_code == 401
        assert c.get(f"/api/projects/{pid}/timeline").status_code == 401


# ---------------- U2: catalogue, readiness check, issues, text ----------------

def test_catalogue_lists_items_with_renderability_and_schema(client):
    r = client.get("/api/catalogue")
    assert r.status_code == 200
    data = r.json()
    t = {i["transition_id"]: i for i in data["transitions"]}
    assert t["fade"]["renderable"] is True
    assert t["dissolve"]["renderable"] is False
    e = {i["effect_id"]: i for i in data["effects"]}
    assert e["brightness"]["renderable"] is True
    assert any(f["name"] == "amount" for f in e["brightness"]["parameter_schema"])


def test_check_reports_render_readiness(client):
    pid = _project(client)
    _two_clips(client, pid)
    assert client.post(f"/api/projects/{pid}/check").json() == {"ok": True, "error": None}


def test_check_explains_why_a_timeline_cannot_render(client):
    pid = _project(client)
    assert _one_clip_too_long(client, pid).status_code == 200
    r = client.post(f"/api/projects/{pid}/check").json()
    assert r["ok"] is False
    assert r["error"]


def test_save_returns_issue_messages(client):
    pid = _project(client)
    r = _one_clip_too_long(client, pid)
    assert r.status_code == 200
    issues = r.json()["issues"]
    assert issues
    assert all(set(i) >= {"code", "message"} and i["message"] for i in issues)


def test_text_assets_survive_save(client):
    pid = _project(client)

    def mutate(tl):
        tl["assets"] = [{"asset_id": "txt1", "kind": "text", "text": "नमस्ते"}]
        tl["clips"] = [{"clip_id": "t1", "asset_id": "txt1", "track_id": "overlay",
                        "start_tick": 0, "duration_ticks": 1000}]
    assert _save(client, pid, mutate).status_code == 200
    tl = client.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    assert any(a["asset_id"] == "txt1" and a["text"] == "नमस्ते" for a in tl["assets"])
    assert [c["clip_id"] for c in tl["clips"]] == ["t1"]