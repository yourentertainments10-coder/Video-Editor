import hashlib, av, numpy as np, pytest
from pathlib import Path
from poc.render.transitions import Segment, Join, render_sequence_to_file, build_sequence_graph
FIX = Path(__file__).parent / "fixtures" / "ingest"
W, H, FPS = 640, 360, 30

def _seg(name, a=1000, b=3000):
    return Segment(path=FIX/name, src_in_ms=a, src_out_ms=b)

def _three():
    return [_seg("solid_red.mp4"), _seg("solid_green.mp4"), _seg("solid_blue.mp4")]

def _render(tmp_path, joins, segs=None, name="o.mp4"):
    out = render_sequence_to_file(segs or _three(), joins, tmp_path/name,
                                  width=W, height=H, fps=FPS)
    with av.open(str(out)) as c:
        v = c.streams.video[0]
        frames = [f.to_ndarray(format="rgb24") for f in c.decode(v)]
        return v.width, v.height, frames

def _rgb(img):
    return img.reshape(-1, 3).mean(axis=0)

def _is(color, img):
    r, g, b = _rgb(img)
    return {"red": r > 200 and g < 60 and b < 60,
            "green": g > 100 and r < 60 and b < 60,
            "blue": b > 200 and r < 60 and g < 60}[color]

def test_hard_cuts_land_on_exact_frames(tmp_path):
    w, h, f = _render(tmp_path, [Join("cut", 0), Join("cut", 0)])
    assert (w, h) == (W, H)
    assert 179 <= len(f) <= 181
    assert _is("red", f[59]) and _is("green", f[60])
    assert _is("green", f[119]) and _is("blue", f[120])

def test_crossfade_keeps_length_and_is_centred_on_cut(tmp_path):
    _, _, f = _render(tmp_path, [Join("crossfade", 500), Join("cut", 0)])
    assert 179 <= len(f) <= 181
    assert _is("red", f[45]) and _is("green", f[75])
    r, g, _ = _rgb(f[60])
    assert 80 < r < 180 and 30 < g < 100
    assert _is("blue", f[120])

def test_fade_black_dips_to_black_at_cut(tmp_path):
    _, _, f = _render(tmp_path, [Join("fade_black", 500), Join("cut", 0)])
    assert 179 <= len(f) <= 181
    assert _rgb(f[60]).max() < 40
    assert _is("red", f[45]) and _is("green", f[75])

def test_wipe_left_splits_frame_mid_transition(tmp_path):
    _, _, f = _render(tmp_path, [Join("wipe_left", 500), Join("cut", 0)])
    left, right = _rgb(f[60][:, :100]), _rgb(f[60][:, -100:])
    assert (left[0] > 200 and right[1] > 100) or (right[0] > 200 and left[1] > 100)

def test_slide_left_renders_and_keeps_length(tmp_path):
    _, _, f = _render(tmp_path, [Join("slide_left", 500), Join("cut", 0)])
    assert 179 <= len(f) <= 181
    assert _is("red", f[45]) and _is("green", f[75])

def test_missing_handle_is_rejected(tmp_path):
    segs = [_seg("solid_red.mp4"), _seg("solid_green.mp4", 0, 2000), _seg("solid_blue.mp4")]
    with pytest.raises(ValueError):
        render_sequence_to_file(segs, [Join("crossfade", 500), Join("cut", 0)],
                                tmp_path/"o.mp4", width=W, height=H, fps=FPS)

@pytest.mark.parametrize("joins", [
    [Join("cut", 0)],
    [Join("spin", 500), Join("cut", 0)],
    [Join("crossfade", 1500), Join("cut", 0)],
    [Join("crossfade", -100), Join("cut", 0)],
])
def test_invalid_joins_raise(tmp_path, joins):
    with pytest.raises(ValueError):
        render_sequence_to_file(_three(), joins, tmp_path/"o.mp4",
                                width=W, height=H, fps=FPS)

def test_mixed_sizes_are_fitted_before_xfade(tmp_path):
    segs = [_seg("solid_red.mp4"),
            Segment(path=FIX/"portrait_1080p.mp4", src_in_ms=500, src_out_ms=2500)]
    w, h, f = _render(tmp_path, [Join("crossfade", 500)], segs=segs)
    assert (w, h) == (W, H)
    assert 119 <= len(f) <= 121
    assert _is("green", f[100])

def test_sequence_render_is_deterministic(tmp_path):
    j = [Join("crossfade", 500), Join("wipe_left", 500)]
    _, _, a = _render(tmp_path, j, name="a.mp4")
    _, _, b = _render(tmp_path, j, name="b.mp4")
    assert len(a) == len(b)
    for i in (0, 60, 120, len(a) - 1):
        assert hashlib.sha256(a[i].tobytes()).digest() == hashlib.sha256(b[i].tobytes()).digest()

def test_graph_is_offline_and_uses_xfade():
    g = build_sequence_graph(_three(), [Join("crossfade", 500), Join("cut", 0)],
                             width=W, height=H, fps=FPS)
    assert "xfade" in g and "://" not in g and "zoompan" not in g
