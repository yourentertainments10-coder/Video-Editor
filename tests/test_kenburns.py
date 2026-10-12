import hashlib, av, numpy as np
from pathlib import Path
from poc.timeline import MediaAsset
from poc.ingest.normalize import ken_burns_to_file
FIX = Path(__file__).parent / "fixtures" / "ingest"

def _frames(p):
    with av.open(str(p)) as c:
        v = c.streams.video[0]
        frames = [f.to_ndarray(format="rgb24") for f in c.decode(v)]
        return v.width, v.height, frames

def _asset():
    return MediaAsset(asset_id="kb", kind="image", uri=str(FIX/"pattern.png"), duration_ms=4000)

def test_ken_burns_size_and_length(tmp_path):
    w, h, frames = _frames(ken_burns_to_file(_asset(), tmp_path/"kb.mp4", duration_ms=4000))
    assert (w, h) == (1920, 1080)
    assert 115 <= len(frames) <= 125

def test_ken_burns_actually_moves(tmp_path):
    _, _, frames = _frames(ken_burns_to_file(_asset(), tmp_path/"kb.mp4", duration_ms=4000))
    diff = np.abs(frames[0].astype(int) - frames[-1].astype(int)).mean()
    assert diff > 5

def test_no_zoom_matches_static_image(tmp_path):
    _, _, frames = _frames(ken_burns_to_file(_asset(), tmp_path/"kb.mp4",
                           duration_ms=2000, start_zoom=1.0, end_zoom=1.0))
    diff = np.abs(frames[0].astype(int) - frames[-1].astype(int)).mean()
    assert diff < 1.0

def test_ken_burns_is_deterministic(tmp_path):
    a = _frames(ken_burns_to_file(_asset(), tmp_path/"a.mp4", duration_ms=2000))[2]
    b = _frames(ken_burns_to_file(_asset(), tmp_path/"b.mp4", duration_ms=2000))[2]
    assert len(a) == len(b)
    for i in (0, len(a)//2, len(a)-1):
        assert hashlib.sha256(a[i].tobytes()).hexdigest() == hashlib.sha256(b[i].tobytes()).hexdigest()

def test_ken_burns_is_smooth(tmp_path):
    _, _, frames = _frames(ken_burns_to_file(_asset(), tmp_path/"kb.mp4", duration_ms=4000))
    steps = [np.abs(frames[i+1].astype(int) - frames[i].astype(int)).mean()
             for i in range(len(frames)-1)]
    assert max(steps) < 4 * (sum(steps) / len(steps)) + 1
