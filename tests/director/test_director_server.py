"""Local Director in the app: /brief endpoint and auto-edit transition/title options."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.director.apply import TITLE_ASSET_ID, TITLE_CLIP_ID, apply_style
from poc.server.app import create_app
from poc.timeline import (EditorClip, EditorSettings, EditorTimeline, Layer, MediaAsset, TimeBase,
                          Track, TransitionRef)

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "ingest"
MUSIC = ROOT / "experiments" / "beat_flash" / "fixtures" / "synth_128.wav"
VIDEOS = ("tone_a.mp4", "tone_b.mp4", "solid_red.mp4", "solid_green.mp4", "solid_blue.mp4")


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", token="t", allowed_hosts=("testserver",))
    with TestClient(app) as c:
        c.headers.update({"X-Editor-Token": "t"})
        yield c


def _project(c):
    pid = c.post("/api/projects", json={"name": "LD"}).json()["project_id"]
    r = c.post(f"/api/projects/{pid}/assets", json={"paths": [str(FIX / v) for v in VIDEOS] + [str(MUSIC)]})
    return pid, {a["file_name"]: a for a in r.json()["assets"]}


def _wait(c, job_id):
    end = time.time() + 300
    while time.time() < end:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_brief_endpoint_reads_the_brief_and_changes_nothing(client):
    pid, assets = _project(client)
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    r = client.post(f"/api/projects/{pid}/brief",
                    json={"text": "8 second calm reel with smooth fades, title 'Hello', skip solid_red"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["brief"]["duration_s"] == 8
    assert body["brief"]["profile"] == "reels_9x16"
    assert body["brief"]["mood"] == "calm"
    assert body["brief"]["transition_policy"] == "soft"
    assert body["brief"]["title"] == "Hello"
    assert assets["solid_red.mp4"]["asset_id"] not in body["video_asset_ids"]
    assert len(body["video_asset_ids"]) == 4
    assert body["questions"] == []
    assert any("Leaving out: solid_red.mp4" in line for line in body["understood"])
    assert client.get(f"/api/projects/{pid}/timeline").json()["revision"] == rev


@pytest.mark.parametrize("bad", [{"text": 5}, {}, {"text": "x" * 1001}])
def test_brief_endpoint_validates(client, bad):
    pid, _ = _project(client)
    assert client.post(f"/api/projects/{pid}/brief", json=bad).status_code == 422


@pytest.mark.parametrize("extra", [{"transition_policy": "wipe"}, {"title": ""}, {"title": "x" * 81}, {"title": 3}])
def test_auto_edit_validates_style_options(client, extra):
    pid, assets = _project(client)
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    body = {"base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"],
            "mood": "calm", "duration_s": 6, **extra}
    assert client.post(f"/api/projects/{pid}/auto-edit", json=body).status_code == 422


def test_brief_to_auto_edit_to_render(client):
    pid, assets = _project(client)
    brief = client.post(f"/api/projects/{pid}/brief",
                        json={"text": "8 second calm landscape video, smooth fades, title 'Goa 2026'"}).json()
    b = brief["brief"]
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    job = _wait(client, client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"],
        "mood": b["mood"], "duration_s": b["duration_s"], "video_asset_ids": brief["video_asset_ids"],
        "transition_policy": b["transition_policy"], "title": b["title"]}).json()["job_id"])
    assert job["status"] == "done", job
    tl = client.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    fades = [c for c in tl["clips"] if c["track_id"] == "video" and c.get("transition")]
    assert fades and all(c["transition"]["transition_id"] == "fade" for c in fades)
    title = [c for c in tl["clips"] if c["clip_id"] == TITLE_CLIP_ID]
    assert len(title) == 1 and title[0]["track_id"] == "overlay"
    assert any(a["asset_id"] == TITLE_ASSET_ID and a["text"] == "Goa 2026" for a in tl["assets"])
    assert client.post(f"/api/projects/{pid}/check").json() == {"ok": True, "error": None}
    render = _wait(client, client.post(f"/api/projects/{pid}/render",
                                       json={"profile": "preview_540p"}).json()["job_id"])
    assert render["status"] == "done", render
    assert render["report"]["ok"] is True, render["report"]["problems"]


def test_cuts_only_auto_edit_has_no_transitions(client):
    pid, assets = _project(client)
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    job = _wait(client, client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"],
        "mood": "energetic", "duration_s": 6, "transition_policy": "cuts_only"}).json()["job_id"])
    assert job["status"] == "done", job
    tl = client.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    assert not any(c.get("transition") for c in tl["clips"])


# ---------------- apply_style unit tests ----------------

def _tl(clips, assets):
    return EditorTimeline(
        project_id="p", timeline_id="t", duration_ticks=4000, time_base=TimeBase(ticks_per_second=1000),
        settings=EditorSettings(fps_num=30, fps_den=1, width=640, height=360), assets=assets,
        layers=[Layer(layer_id="v", name="V", order=0)],
        tracks=[Track(track_id="video", kind="video", layer_id="v"),
                Track(track_id="overlay", kind="overlay", layer_id="v")],
        clips=clips)


def _clip(cid, start, dur, src_in, asset="a"):
    return EditorClip(clip_id=cid, asset_id=asset, track_id="video", start_tick=start, duration_ticks=dur,
                      source_in_ms=src_in, source_out_ms=src_in + dur)


ASSET = MediaAsset(asset_id="a", kind="video", uri="file:///a.mp4", duration_ms=10000)


def test_soft_fades_respect_clip_length_and_source_handles():
    tl = _tl([_clip("c1", 0, 1000, 1000), _clip("c2", 1000, 300, 0), _clip("c3", 1300, 1000, 2000),
              _clip("c4", 2300, 1700, 5000)], [ASSET])
    out = apply_style(tl, transition_policy="soft")
    t = {c.clip_id: c.transition for c in out.clips}
    assert t["c1"] is None                      # first clip
    assert t["c2"] is None                      # no source before c2 (source_in 0)
    assert t["c3"].duration_ticks == 133        # c2 is 300 ms -> at most 150 ms -> 4 frames
    assert t["c4"].duration_ticks == 400        # full soft fade
    assert all(c.transition is None for c in tl.clips)   # input untouched


def test_cuts_only_removes_transitions_and_title_is_not_duplicated():
    tl = _tl([_clip("c1", 0, 2000, 1000),
              _clip("c2", 2000, 2000, 3000).model_copy(update={"transition": TransitionRef(transition_id="fade")})],
             [ASSET])
    out = apply_style(apply_style(tl, transition_policy="cuts_only", title="One"), title="Two")
    assert not any(c.transition for c in out.clips)
    titles = [a for a in out.assets if a.asset_id == TITLE_ASSET_ID]
    assert [a.text for a in titles] == ["Two"]
    assert sum(c.clip_id == TITLE_CLIP_ID for c in out.clips) == 1


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError):
        apply_style(_tl([], [ASSET]), transition_policy="wipe")
