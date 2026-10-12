import av
from pathlib import Path
from poc.timeline import MediaAsset
from poc.ingest.normalize import normalize_asset, normalize_to_file
FIX = Path(__file__).parent / "fixtures" / "ingest"

def _asset():
    return MediaAsset(asset_id="ex", kind="image", uri=str(FIX/"exif_rot6.jpg"), duration_ms=1000)

def test_exif_orientation_reported_as_display_size():
    n = normalize_asset(_asset())
    assert (n.width, n.height) == (480, 640)

def test_exif_orientation_applied_when_encoding(tmp_path):
    out = normalize_to_file(_asset(), tmp_path/"e.mp4")
    with av.open(str(out)) as c:
        img = next(c.decode(c.streams.video[0])).to_ndarray(format="rgb24")
    assert img.shape[:2] == (640, 480)
    top = img[:200].reshape(-1, 3).mean(0)
    bottom = img[-200:].reshape(-1, 3).mean(0)
    assert top[0] > 200 and top[2] < 60
    assert bottom[2] > 200 and bottom[0] < 60
