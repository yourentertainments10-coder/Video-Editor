"""Phase 3b: animated titles drawn by libass (pop, slide up, typewriter, words pop in, neon glow,
lower third). Each one is rendered for real and checked on the frames."""
from pathlib import Path

import av
import numpy as np
import pytest

from poc.render.editor import _title_style
from poc.render.text import ANIMATIONS, TextOverlay, build_ass, burn_text_to_file

FIX = Path(__file__).parent / "fixtures" / "ingest"


def _frames(path):
    with av.open(str(path)) as container:
        return [f.to_ndarray(format="rgb24") for f in container.decode(video=0)]


def _white(img):
    return img.min(axis=2) > 200


def _burn(tmp_path, name, **kw):
    overlay = TextOverlay(text=kw.pop("text", "GOA TRIP"), start_ms=0, end_ms=3000, y=0.5, size_pct=0.08, **kw)
    return _frames(burn_text_to_file(FIX / "solid_blue.mp4", [overlay], tmp_path / f"{name}.mp4"))


@pytest.mark.parametrize("animation", ANIMATIONS)
def test_every_animation_renders_the_text(tmp_path, animation):
    frames = _burn(tmp_path, animation, animation=animation)
    assert _white(frames[45]).sum() > 300            # 1.5 s in: the whole title is on screen


def test_typewriter_and_word_pop_reveal_the_text_over_time(tmp_path):
    for animation in ("typewriter", "word_pop"):
        frames = _burn(tmp_path, animation, animation=animation, text="ONE TWO THREE FOUR")
        early, late = _white(frames[3]).sum(), _white(frames[60]).sum()
        assert early < 0.6 * late, animation


def test_pop_starts_small_and_slide_up_rises(tmp_path):
    pop = _burn(tmp_path, "pop", animation="pop")
    width = lambda img: np.ptp(np.nonzero(_white(img))[1]) if _white(img).any() else 0
    assert width(pop[2]) < 0.8 * width(pop[30])
    slide = _burn(tmp_path, "slide", animation="slide_up")
    rows = lambda img: np.nonzero(_white(img))[0].mean()
    assert rows(slide[5]) > rows(slide[30]) + 5


def test_lower_third_sits_bottom_left_on_a_bar(tmp_path):
    frames = _burn(tmp_path, "lt", animation="lower_third")
    img = frames[45]
    h, w = img.shape[:2]
    ys, xs = np.nonzero(_white(img))
    assert xs.mean() < w / 2 and ys.mean() > 0.7 * h
    px = img.astype(int)
    bar = (px[..., 0] > 180) & (px[..., 1] > 40) & (px[..., 1] < 130) & (px[..., 2] < 60)   # deep orange
    assert bar[int(ys.mean()), : w // 2].sum() > 20
    early = _white(frames[4])                          # still sliding in from the left
    assert not early.any() or np.nonzero(early)[1].mean() < xs.mean()


def test_glow_uses_the_accent_colour(tmp_path):
    img = _burn(tmp_path, "glow", animation="glow", accent="#FF8000")[60].astype(int)
    orange = (img[..., 0] > 150) & (img[..., 1] > 50) & (img[..., 1] < 190) & (img[..., 2] < 90)
    assert orange.sum() > 100


def test_revealed_text_cannot_inject_tags(tmp_path):
    for animation in ("typewriter", "word_pop"):
        script = build_ass([TextOverlay(text="{\\c&H0000FF&}AB", start_ms=0, end_ms=2000, animation=animation)], 1080, 1920)
        assert "\\{\\\\c&H0000FF&\\}" in script or "\\{" in script
        img = _burn(tmp_path, f"inj-{animation}", animation=animation, text="{\\c&H0000FF&}AB")[60].astype(int)
        red = (img[..., 0] > 180) & (img[..., 1] < 80) & (img[..., 2] < 80)
        assert red.sum() == 0


def test_a_later_render_chunk_continues_without_a_new_entrance():
    def line(**kw):
        script = build_ass([TextOverlay(text="Goa", start_ms=0, end_ms=2000, **kw)], 1080, 1920)
        return script.strip().splitlines()[-1]
    assert "\\move" in line(animation="lower_third") and "\\move" not in line(animation="lower_third", elapsed_ms=900)
    assert "\\fscx40" in line(animation="pop") and "\\fscx40" not in line(animation="pop", elapsed_ms=900)
    assert "\\move" not in line(animation="slide_up", elapsed_ms=900)
    assert "\\alpha&HFF&" in line(animation="typewriter")
    assert "\\alpha&HFF&" not in line(animation="typewriter", elapsed_ms=1500)   # already typed out


def test_unknown_animation_or_colour_is_rejected():
    with pytest.raises(ValueError):
        build_ass([TextOverlay(text="x", start_ms=0, end_ms=1000, animation="spin")], 100, 100)
    with pytest.raises(ValueError):
        build_ass([TextOverlay(text="x", start_ms=0, end_ms=1000, accent="orange")], 100, 100)
    with pytest.raises(ValueError):
        build_ass([TextOverlay(text="x", start_ms=0, end_ms=1000, elapsed_ms=-5)], 100, 100)


def test_the_editor_reads_the_title_style_and_ignores_broken_values():
    assert _title_style({}) == {}
    assert _title_style({"title_style": {"animation": "pop", "color": "#ffcc00", "size_pct": 0.09, "by": "user"}}) == {
        "animation": "pop", "color": "#ffcc00", "size_pct": 0.09}
    assert _title_style({"title_style": {"animation": "spin", "color": "red", "size_pct": 3}}) == {}
    assert _title_style({"title_style": "pop"}) == {}
