import os, time, av
from pathlib import Path
from poc.ingest.proxy import make_proxy, make_thumbnails
from poc.timeline import MediaAsset
FIX = Path(__file__).parent / "fixtures" / "ingest"

def _probe(p):
    with av.open(str(p)) as c:
        v = c.streams.video[0]
        return v.width, v.height, float(v.average_rate)

def test_landscape_proxy_is_720p(tmp_path):
    a = MediaAsset(asset_id="l", kind="video", uri=str(FIX/"landscape_1080p.mp4"), duration_ms=3000)
    w, h, fps = _probe(make_proxy(a, tmp_path))
    assert (w, h) == (1280, 720) and abs(fps - 30) < 0.5

def test_portrait_proxy_keeps_aspect_and_even_dims(tmp_path):
    a = MediaAsset(asset_id="p", kind="video", uri=str(FIX/"portrait_1080p.mp4"), duration_ms=3000)
    w, h, _ = _probe(make_proxy(a, tmp_path))
    assert h == 720 and w % 2 == 0 and abs(w - 405) <= 1

def test_small_input_is_not_upscaled(tmp_path):
    a = MediaAsset(asset_id="s", kind="video", uri=str(FIX/"vfr_clip.mp4"), duration_ms=6000)
    w, h, _ = _probe(make_proxy(a, tmp_path))
    assert (w, h) == (640, 480)

def test_proxy_is_cached(tmp_path):
    a = MediaAsset(asset_id="l", kind="video", uri=str(FIX/"landscape_1080p.mp4"), duration_ms=3000)
    p1 = make_proxy(a, tmp_path); m1 = os.path.getmtime(p1)
    time.sleep(1.1)
    p2 = make_proxy(a, tmp_path)
    assert p1 == p2 and os.path.getmtime(p2) == m1

def test_broken_partial_proxy_is_not_a_cache_hit(tmp_path):
    a = MediaAsset(asset_id="s", kind="video", uri=str(FIX/"vfr_clip.mp4"), duration_ms=6000)
    cached = make_proxy(a, tmp_path)
    partial = cached.with_suffix(".partial.mp4")
    cached.unlink()
    partial.write_bytes(b"broken leftover")

    out = make_proxy(a, tmp_path)
    assert out == cached and out.is_file()
    assert not partial.exists()
    with av.open(str(out)) as c:
        assert sum(1 for _ in c.decode(c.streams.video[0])) > 0

def test_thumbnails_count_and_size(tmp_path):
    a = MediaAsset(asset_id="l", kind="video", uri=str(FIX/"landscape_1080p.mp4"), duration_ms=3000)
    thumbs = make_thumbnails(a, tmp_path, count=6, width=320)
    assert len(thumbs) == 6
    for t in thumbs:
        with av.open(str(t)) as c:
            v = c.streams.video[0]
            assert v.width == 320 and v.height == 180
