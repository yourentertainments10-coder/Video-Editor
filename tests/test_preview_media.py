"""Preview media requests must be cheap: the browser sends many Range requests per file.

Regression: every proxy/audio Range request re-loaded the project and re-probed the source
file (~60 ms each), so fast-cut previews (a new file at almost every cut) stalled.
"""
import os
from pathlib import Path

from fastapi.testclient import TestClient

import poc.server.app as server
from poc.server.app import create_app

FIX = Path(__file__).parent / "fixtures" / "ingest"
H = {"X-Editor-Token": "t"}


def _setup(tmp_path, monkeypatch):
    calls = []
    real = server.make_proxy

    def counting(asset, cache):
        calls.append(asset.asset_id)
        return real(asset, cache)

    monkeypatch.setattr(server, "make_proxy", counting)
    app = create_app(data_dir=tmp_path / "d", token="t", allowed_hosts=("testserver",))
    return app, calls


def test_proxy_lookup_is_done_once_per_file(tmp_path, monkeypatch):
    app, calls = _setup(tmp_path, monkeypatch)
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={"name": "p"}, headers=H).json()["project_id"]
        aid = c.post(f"/api/projects/{pid}/assets", json={"paths": [str(FIX / "tone_a.mp4")]},
                     headers=H).json()["assets"][0]["asset_id"]
        url = f"/api/projects/{pid}/assets/{aid}/proxy"
        first = c.get(url, headers={**H, "Range": "bytes=0-99"})
        assert first.status_code == 206 and len(first.content) == 100
        for start in (100, 5000, 0):
            assert c.get(url, headers={**H, "Range": f"bytes={start}-"}).status_code == 206
        assert calls == [aid]

        # a changed project re-checks (the asset could have been replaced) ...
        project = tmp_path / "d" / "projects" / pid / "project.json"
        stat = project.stat()
        os.utime(project, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        assert c.get(url, headers={**H, "Range": "bytes=0-9"}).status_code == 206
        assert calls == [aid, aid]

        # ... and so does a deleted cache file
        for proxy in (tmp_path / "d" / "cache" / "proxies").glob("*.mp4"):
            proxy.unlink()
        assert c.get(url, headers={**H, "Range": "bytes=0-9"}).status_code == 206
        assert calls == [aid, aid, aid]


def test_unknown_asset_is_still_a_404(tmp_path, monkeypatch):
    app, _ = _setup(tmp_path, monkeypatch)
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={"name": "p"}, headers=H).json()["project_id"]
        assert c.get(f"/api/projects/{pid}/assets/nope/proxy", headers=H).status_code == 404
        assert c.get(f"/api/projects/{pid}/assets/nope/audio", headers=H).status_code == 404


def test_proxy_has_a_keyframe_every_half_second(tmp_path):
    """Previews seek at nearly every cut; a seek decodes from the previous keyframe."""
    import av

    from poc.ingest.proxy import make_proxy
    from poc.timeline import MediaAsset

    asset = MediaAsset(asset_id="v", kind="video", uri=str(FIX / "tone_a.mp4"), duration_ms=4000)
    out = make_proxy(asset, tmp_path / "cache")
    with av.open(str(out)) as container:
        stream = container.streams.video[0]
        times = [float(p.pts * stream.time_base) for p in container.demux(stream)
                 if p.is_keyframe and p.pts is not None]
    assert len(times) >= 6
    assert max(b - a for a, b in zip(times, times[1:])) <= 0.51
