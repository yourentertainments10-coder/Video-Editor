import hashlib, json, shutil
import av, numpy as np, pytest
from pathlib import Path
from poc.render.text import TextOverlay, build_ass, burn_text_to_file, text_boxes
from poc.render.aspect import safe_area_warnings
FIX = Path(__file__).parent / "fixtures" / "ingest"
FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

def _frames(p):
    with av.open(str(p)) as c:
        return [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]

def _white(img):
    return (img.min(axis=2) > 200)

def test_font_bundle_is_registered_and_intact():
    entries = json.loads((FONTS / "fonts.json").read_text(encoding="utf-8"))
    families = {e["family"] for e in entries}
    assert {"Noto Sans", "Noto Sans Devanagari"} <= families
    assert (FONTS / "OFL.txt").is_file()
    for e in entries:
        data = (FONTS / e["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == e["sha256"]

def test_text_appears_only_inside_its_time_window(tmp_path):
    ov = [TextOverlay(text="HELLO", start_ms=500, end_ms=2500, fade_ms=0)]
    f = _frames(burn_text_to_file(FIX/"solid_blue.mp4", ov, tmp_path/"t.mp4"))
    assert _white(f[14]).sum() == 0
    assert _white(f[15]).sum() > 200
    assert _white(f[74]).sum() > 200
    assert _white(f[76]).sum() == 0

def test_vertical_position_is_respected(tmp_path):
    top = _frames(burn_text_to_file(FIX/"solid_blue.mp4",
          [TextOverlay(text="TOP", start_ms=0, end_ms=3000, y=0.2, fade_ms=0)], tmp_path/"a.mp4"))[30]
    bot = _frames(burn_text_to_file(FIX/"solid_blue.mp4",
          [TextOverlay(text="BOT", start_ms=0, end_ms=3000, y=0.8, fade_ms=0)], tmp_path/"b.mp4"))[30]
    h = top.shape[0]
    assert np.nonzero(_white(top))[0].mean() < h / 2
    assert np.nonzero(_white(bot))[0].mean() > h / 2

def test_user_text_cannot_inject_ass_tags(tmp_path):
    ov = [TextOverlay(text="{\\c&H0000FF&}AB", start_ms=0, end_ms=3000, fade_ms=0)]
    img = _frames(burn_text_to_file(FIX/"solid_blue.mp4", ov, tmp_path/"i.mp4"))[30].astype(int)
    red = (img[..., 0] > 180) & (img[..., 1] < 80) & (img[..., 2] < 80)
    assert red.sum() == 0
    assert _white(img.astype(np.uint8)).sum() > 100

def test_multiline_text_is_taller(tmp_path):
    one = text_boxes([TextOverlay(text="LINE", start_ms=0, end_ms=1000)], 1080, 1920)[0]
    two = text_boxes([TextOverlay(text="LINE\nLINE", start_ms=0, end_ms=1000)], 1080, 1920)[0]
    assert two[3] > 1.6 * one[3]

def test_hindi_text_renders(tmp_path):
    box = text_boxes([TextOverlay(text="नमस्ते", start_ms=0, end_ms=1000,
                                  font="Noto Sans Devanagari")], 1080, 1920)[0]
    assert box[2] > 50 and box[3] > 20

def test_safe_area_check_uses_real_text_boxes():
    low = text_boxes([TextOverlay(text="SUBSCRIBE", start_ms=0, end_ms=1000, y=0.95)], 1080, 1920)
    mid = text_boxes([TextOverlay(text="SUBSCRIBE", start_ms=0, end_ms=1000, y=0.5)], 1080, 1920)
    assert len(safe_area_warnings(low)) == 1
    assert safe_area_warnings(mid) == []

def test_paths_with_spaces_and_audio_kept(tmp_path):
    d = tmp_path / "my folder's renders"
    d.mkdir()
    src = d / "clip with space.mp4"
    shutil.copyfile(FIX/"tone_a.mp4", src)
    out = burn_text_to_file(src, [TextOverlay(text="OK", start_ms=0, end_ms=1000)], d/"out file.mp4")
    with av.open(str(out)) as c:
        assert len(c.streams.audio) == 1

def test_burn_is_deterministic(tmp_path):
    ov = [TextOverlay(text="SAME", start_ms=200, end_ms=2000)]
    a = _frames(burn_text_to_file(FIX/"solid_blue.mp4", ov, tmp_path/"a.mp4"))
    b = _frames(burn_text_to_file(FIX/"solid_blue.mp4", ov, tmp_path/"b.mp4"))
    assert [hashlib.sha256(x.tobytes()).digest() for x in a] == \
           [hashlib.sha256(x.tobytes()).digest() for x in b]

@pytest.mark.parametrize("bad", [
    dict(text="x", start_ms=1000, end_ms=1000),
    dict(text="x", start_ms=-1, end_ms=1000),
    dict(text="x", start_ms=0, end_ms=1000, y=1.5),
    dict(text="x", start_ms=0, end_ms=1000, color="red"),
    dict(text="x", start_ms=0, end_ms=1000, font="Comic Sans MS"),
])
def test_invalid_overlays_raise(bad):
    with pytest.raises(ValueError):
        build_ass([TextOverlay(**bad)], 640, 360)
