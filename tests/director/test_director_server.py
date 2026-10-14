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


# ---------------- LD-2: /revise ----------------

def _auto_edit(client, pid, assets, **extra):
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    job = _wait(client, client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"],
        "mood": "calm", "duration_s": 8, **extra}).json()["job_id"])
    assert job["status"] == "done", job
    return client.get(f"/api/projects/{pid}/timeline").json()


def test_revise_proposes_without_saving_then_renders_after_apply(client):
    pid, assets = _project(client)
    saved = _auto_edit(client, pid, assets)
    clips_before = [c for c in saved["timeline"]["clips"] if c["track_id"] == "video"]
    r = client.post(f"/api/projects/{pid}/revise", json={"text": "remove clip 2", "timeline": saved["timeline"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "remove" and body["summary"] and body["questions"] == []
    proposed = body["timeline"]
    assert len([c for c in proposed["clips"] if c["track_id"] == "video"]) == len(clips_before) - 1
    assert client.get(f"/api/projects/{pid}/timeline").json()["revision"] == saved["revision"]   # nothing saved
    # chain a second change on the proposal, then apply it like the UI does
    body2 = client.post(f"/api/projects/{pid}/revise",
                        json={"text": "make it black and white", "timeline": proposed}).json()
    put = client.put(f"/api/projects/{pid}/timeline",
                     json={"base_revision": saved["revision"], "timeline": body2["timeline"]})
    assert put.status_code == 200, put.text
    job = _wait(client, client.post(f"/api/projects/{pid}/render", json={"profile": "preview_540p"}).json()["job_id"])
    assert job["status"] == "done", job
    assert job["report"]["ok"] is True, job["report"]["problems"]


def test_revise_answers_questions_and_refuses_impossible_changes(client):
    pid, assets = _project(client)
    tl = _auto_edit(client, pid, assets)["timeline"]
    why = client.post(f"/api/projects/{pid}/revise", json={"text": "why clip 1?", "timeline": tl}).json()
    assert why["intent"] == "explain" and "score" in why["answer"] and why["timeline"] is None
    impossible = client.post(f"/api/projects/{pid}/revise",
                             json={"text": "shorten clip 1 by 30 seconds", "timeline": tl}).json()
    assert impossible["timeline"] is None and impossible["questions"]
    undo = client.post(f"/api/projects/{pid}/revise", json={"text": "undo", "timeline": tl}).json()
    assert undo["intent"] == "undo" and undo["timeline"] is None


@pytest.mark.parametrize("bad", [{"text": "x", "timeline": {"nope": 1}}, {"timeline": {}}, {"text": 3, "timeline": {}}])
def test_revise_validates(client, bad):
    pid, _ = _project(client)
    assert client.post(f"/api/projects/{pid}/revise", json=bad).status_code == 422


# ---------------------------------------------------------------- Phase 2: footage

from poc.perceive.shots import describe, faces_available  # noqa: E402
from tests import shot_fixtures as fx  # noqa: E402


def _wait_footage(c, pid):
    end = time.time() + 120
    while time.time() < end:
        body = c.get(f"/api/projects/{pid}/footage").json()
        if body["pending"] == 0:
            return body
        time.sleep(0.2)
    raise AssertionError("footage was not looked at in time")


@pytest.mark.skipif(not faces_available(), reason="needs OpenCV 4.x (Haar face cascades)")
def test_footage_is_watched_after_import_and_used_by_the_director(client, tmp_path):
    tour = fx.tour(tmp_path / "media" / "tour.mp4")
    pid = client.post("/api/projects", json={"name": "Footage"}).json()["project_id"]
    assets = client.post(f"/api/projects/{pid}/assets", json={"paths": [str(tour), str(MUSIC)]}).json()["assets"]
    video = next(a for a in assets if a["kind"] == "video")
    body = _wait_footage(client, pid)
    entry = body["assets"][video["asset_id"]]
    assert entry["status"] == "ready" and entry["faces"] is True and entry["labels"]
    assert set(body["assets"]) == {video["asset_id"]}                 # music is not looked at

    # auto-edit that prefers close-ups: every clip it can swap shows the close-up part
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    job = client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": rev, "music_asset_id": next(a for a in assets if a["kind"] != "video")["asset_id"],
        "mood": "energetic", "duration_s": 3, "shots": {"prefer": ["close-up"]}}).json()
    assert _wait(client, job["job_id"])["status"] == "done"
    tl = client.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    clips = [c for c in tl["clips"] if c["track_id"] == "video"]
    close = [c for c in clips if 3500 <= c["source_in_ms"] and c["source_out_ms"] <= 8500]
    assert close, clips                                               # the close-up is 4..8 s of the file

    # the Director answers from the footage, and changes it on request
    r = client.post(f"/api/projects/{pid}/revise", json={"text": "which clips are close-ups?", "timeline": tl}).json()
    assert r["answer"] and "close-up" in r["answer"]
    r = client.post(f"/api/projects/{pid}/revise", json={"text": "no close-ups", "timeline": tl}).json()
    assert r["timeline"] and r["operations"][0]["op"] == "replace_source", r


def test_footage_endpoint_and_shot_validation(client):
    pid, assets = _project(client)
    body = _wait_footage(client, pid)
    assert set(body["assets"]) == {a["asset_id"] for a in assets.values() if a["kind"] == "video"}
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    music = next(a for a in assets.values() if a["kind"] != "video")["asset_id"]
    r = client.post(f"/api/projects/{pid}/auto-edit", json={"base_revision": rev, "music_asset_id": music,
                    "mood": "calm", "duration_s": 3, "shots": {"prefer": ["sunsets"]}})
    assert r.status_code == 422


# ---------------------------------------------------------------- Phase 3: style

@pytest.mark.parametrize("mood,style,look", [("energetic", "bold", None), ("calm", "auto", "vintage")])
def test_styled_auto_edit_renders_and_verifies(client, mood, style, look):
    pid, assets = _project(client)
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    body = {"base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"], "mood": mood,
            "duration_s": 10, "style": style, **({"look": look} if look else {})}
    job = _wait(client, client.post(f"/api/projects/{pid}/auto-edit", json=body).json()["job_id"])
    assert job["status"] == "done", job
    tl = client.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    video = [c for c in tl["clips"] if c["track_id"] == "video"]
    effects = {e["effect_id"] for c in video for e in c["effects"]}
    assert f"look_{look or ('cinematic' if style == 'bold' else 'warm')}" in effects
    assert client.post(f"/api/projects/{pid}/check").json() == {"ok": True, "error": None}
    render = _wait(client, client.post(f"/api/projects/{pid}/render", json={"profile": "preview_540p"}).json()["job_id"])
    assert render["status"] == "done", render
    assert render["report"]["ok"] is True, render["report"]["problems"]


def test_style_options_are_validated(client):
    pid, assets = _project(client)
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    base = {"base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"], "mood": "calm", "duration_s": 3}
    assert client.post(f"/api/projects/{pid}/auto-edit", json={**base, "style": "crazy"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/auto-edit", json={**base, "look": "sunset"}).status_code == 422


def test_transition_family_from_a_brief(client):
    pid, assets = _project(client)
    brief = client.post(f"/api/projects/{pid}/brief", json={"text": "10 second reel with zoom transitions"}).json()["brief"]
    assert brief["transition_family"] == "zoom"
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    job = _wait(client, client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"], "mood": "energetic",
        "duration_s": 10, "style": "auto", "transition_family": "zoom"}).json()["job_id"])
    assert job["status"] == "done", job
    tl = client.get(f"/api/projects/{pid}/timeline").json()["timeline"]
    ids = {c["transition"]["transition_id"] for c in tl["clips"] if c["track_id"] == "video" and c.get("transition")}
    assert ids <= {"zoom_through"}
    rev = client.get(f"/api/projects/{pid}/timeline").json()["revision"]
    assert client.post(f"/api/projects/{pid}/auto-edit", json={
        "base_revision": rev, "music_asset_id": assets["synth_128.wav"]["asset_id"], "mood": "calm",
        "duration_s": 3, "transition_family": "sparkle"}).status_code == 422
