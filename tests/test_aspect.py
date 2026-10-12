import av, numpy as np, pytest
from pathlib import Path
from poc.timeline import MediaAsset
from poc.render.aspect import fit_to_frame_to_file, safe_area_warnings, build_fit_filter
FIX = Path(__file__).parent / "fixtures" / "ingest"

def _asset(name):
    return MediaAsset(asset_id=name, kind="video", uri=str(FIX/name), duration_ms=2000)

def _mid_frame(p):
    with av.open(str(p)) as c:
        v = c.streams.video[0]
        frames = [f.to_ndarray(format="rgb24") for f in c.decode(v)]
        return v.width, v.height, v.sample_aspect_ratio, frames[len(frames)//2], len(frames)

def _white_fraction(img):
    return (img.min(axis=2) > 240).mean()

@pytest.mark.parametrize("mode", ["cover", "contain_blur", "subject_crop"])
def test_output_is_vertical_1080x1920(tmp_path, mode):
    w, h, sar, _, n = _mid_frame(fit_to_frame_to_file(
        _asset("marker_left.mp4"), tmp_path/"o.mp4", mode=mode))
    assert (w, h) == (1080, 1920)
    assert sar in (None, 0) or float(sar) == 1.0
    assert 55 <= n <= 65

def test_cover_center_crops_away_left_subject(tmp_path):
    *_, img, _ = _mid_frame(fit_to_frame_to_file(
        _asset("marker_left.mp4"), tmp_path/"o.mp4", mode="cover"))
    assert _white_fraction(img) < 0.001

def test_subject_crop_keeps_left_subject(tmp_path):
    *_, img, _ = _mid_frame(fit_to_frame_to_file(
        _asset("marker_left.mp4"), tmp_path/"o.mp4",
        mode="subject_crop", focus=(300/1920, 540/1080)))
    assert _white_fraction(img) > 0.03

def test_subject_crop_clamps_at_edge(tmp_path):
    *_, img, _ = _mid_frame(fit_to_frame_to_file(
        _asset("marker_left.mp4"), tmp_path/"o.mp4",
        mode="subject_crop", focus=(0.0, 0.5)))
    assert _white_fraction(img) > 0.03

def test_contain_blur_shows_whole_frame_and_blurred_bands(tmp_path):
    *_, img, _ = _mid_frame(fit_to_frame_to_file(
        _asset("marker_left.mp4"), tmp_path/"o.mp4", mode="contain_blur"))
    assert _white_fraction(img) > 0.003
    top = img[:300].astype(int)
    assert top.mean() > 20
    mid = img[700:1200].astype(int)
    grad = lambda a: np.abs(np.diff(a, axis=1)).mean()
    assert grad(top) < grad(mid)

def test_invalid_mode_and_focus_raise(tmp_path):
    with pytest.raises(ValueError):
        fit_to_frame_to_file(_asset("marker_left.mp4"), tmp_path/"o.mp4", mode="stretch")
    with pytest.raises(ValueError):
        fit_to_frame_to_file(_asset("marker_left.mp4"), tmp_path/"o.mp4",
                             mode="subject_crop", focus=(1.5, 0.5))

def test_filter_has_no_zoompan_or_network():
    f = build_fit_filter(1920, 1080, 1080, 1920, "contain_blur", (0.5, 0.5))
    assert "zoompan" not in f and "://" not in f
    assert "boxblur" in f and "overlay" in f

def test_safe_area_warnings():
    assert safe_area_warnings([(100, 600, 400, 200)]) == []
    assert len(safe_area_warnings([(100, 50, 400, 100)])) == 1
    assert len(safe_area_warnings([(100, 1700, 400, 100)])) == 1
    assert len(safe_area_warnings([(1000, 800, 70, 100)])) == 1
    with pytest.raises(ValueError):
        safe_area_warnings([], profile="nope")

from poc.render.aspect import fit_graph_for

def test_fit_graph_runs_normalize_chain_first():
    hdr = MediaAsset(asset_id="h", kind="video", uri=str(FIX/"hdr_10bit.mp4"), duration_ms=2000)
    il = MediaAsset(asset_id="i", kind="video", uri=str(FIX/"interlaced.mp4"), duration_ms=2000)
    g1 = fit_graph_for(hdr, 1080, 1920, "cover", (0.5, 0.5))
    g2 = fit_graph_for(il, 1080, 1920, "cover", (0.5, 0.5))
    assert "tonemap" in g1 and g1.index("tonemap") < g1.index("crop")
    assert "bwdif" in g2 and g2.index("bwdif") < g2.index("crop")
