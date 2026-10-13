import hashlib
import av, numpy as np, pytest
from pathlib import Path
from poc.project_assets import ProjectAssetLibrary
from poc.timeline import (EditorClip, EditorSettings, EditorTimeline, EffectRef,
                          Layer, TimeBase, Track, EFFECT_CATALOGUE)
from poc.execute.editor_render import EditorRenderError, render_editor_timeline
FIX = Path(__file__).parent / "fixtures" / "ingest"

def _render(tmp_path, fixture, effects, name="o.mp4"):
    lib = ProjectAssetLibrary.create(tmp_path / f"proj-{name}", project_id="fx")
    a = lib.import_asset(FIX/fixture).media_asset
    tl = EditorTimeline(
        project_id="fx", timeline_id="t", duration_ticks=2000,
        time_base=TimeBase(ticks_per_second=1000),
        settings=EditorSettings(fps_num=30, fps_den=1, width=640, height=360),
        assets=[a], layers=[Layer(layer_id="v", name="V", order=0)],
        tracks=[Track(track_id="video", kind="video", layer_id="v")],
        clips=[EditorClip(clip_id="c", asset_id=a.asset_id, track_id="video",
                          start_tick=0, duration_ticks=2000, source_in_ms=0,
                          source_out_ms=2000,
                          effects=[EffectRef(**e) for e in effects])])
    res = render_editor_timeline(tl, lib, tmp_path/name)
    with av.open(str(res.out_path)) as c:
        frames = [f.to_ndarray(format="rgb24").astype(float) for f in c.decode(video=0)]
    return res, frames

def _white(img):
    return img.min(axis=2) > 200

def test_all_effects_are_renderable_now():
    assert all(e.renderable for e in EFFECT_CATALOGUE)

def test_brightness_raises_mean(tmp_path):
    _, base = _render(tmp_path, "marker_left.mp4", [], "a.mp4")
    _, fx = _render(tmp_path, "marker_left.mp4",
                    [dict(effect_id="brightness", parameters={"amount": 0.3})], "b.mp4")
    assert fx[30].mean() > base[30].mean() + 20

def test_contrast_zero_flattens_image(tmp_path):
    _, base = _render(tmp_path, "marker_left.mp4", [], "a.mp4")
    _, fx = _render(tmp_path, "marker_left.mp4",
                    [dict(effect_id="contrast", parameters={"factor": 0.0})], "b.mp4")
    assert base[30].std() > 10 and fx[30].std() < 3

def test_saturation_zero_is_greyscale(tmp_path):
    _, fx = _render(tmp_path, "solid_red.mp4",
                    [dict(effect_id="saturation", parameters={"factor": 0.0})])
    px = fx[30].reshape(-1, 3).mean(0)
    assert px.max() - px.min() < 10

def test_blur_softens_edges(tmp_path):
    grad = lambda img: np.abs(np.diff(img.mean(axis=2), axis=1)).max()
    _, base = _render(tmp_path, "marker_left.mp4", [], "a.mp4")
    _, fx = _render(tmp_path, "marker_left.mp4",
                    [dict(effect_id="blur", parameters={"radius": 8})], "b.mp4")
    assert grad(fx[30]) < 0.5 * grad(base[30])

def test_fade_in_and_fade_out(tmp_path):
    _, base = _render(tmp_path, "solid_red.mp4", [], "a.mp4")
    _, fin = _render(tmp_path, "solid_red.mp4",
                     [dict(effect_id="fade_in", parameters={"duration_ms": 500})], "b.mp4")
    _, fout = _render(tmp_path, "solid_red.mp4",
                      [dict(effect_id="fade_out", parameters={"duration_ms": 500})], "c.mp4")
    assert fin[0].mean() < 10 and abs(fin[30].mean() - base[30].mean()) < 3
    assert fout[-1].mean() < 15 and abs(fout[15].mean() - base[15].mean()) < 3

def test_zoom_in_enlarges_subject(tmp_path):
    _, fx = _render(tmp_path, "marker_left.mp4", [dict(effect_id="zoom_in", parameters={
        "from_scale": 1.0, "to_scale": 1.2, "duration_ms": 2000})])
    assert _white(fx[-1]).mean() > 1.3 * _white(fx[0]).mean()

def test_pan_directions(tmp_path):
    cx = lambda img: np.nonzero(_white(img))[1].mean()
    cy = lambda img: np.nonzero(_white(img))[0].mean()
    p = {"distance": 0.2, "duration_ms": 2000}
    _, left = _render(tmp_path, "marker_left.mp4", [dict(effect_id="pan_left", parameters=p)], "l.mp4")
    _, right = _render(tmp_path, "marker_left.mp4", [dict(effect_id="pan_right", parameters=p)], "r.mp4")
    _, up = _render(tmp_path, "marker_left.mp4", [dict(effect_id="pan_up", parameters=p)], "u.mp4")
    _, down = _render(tmp_path, "marker_left.mp4", [dict(effect_id="pan_down", parameters=p)], "d.mp4")
    assert cx(left[-1]) < cx(left[0]) - 10
    assert cx(right[-1]) > cx(right[0]) + 10
    assert cy(up[-1]) < cy(up[0]) - 10
    assert cy(down[-1]) > cy(down[0]) + 10

def test_zoom_below_one_is_rejected_clearly(tmp_path):
    with pytest.raises(EditorRenderError, match="zoom_out.*not renderable"):
        _render(tmp_path, "marker_left.mp4", [dict(effect_id="zoom_out", parameters={
            "from_scale": 1.0, "to_scale": 0.5})])

def test_effects_render_deterministically(tmp_path):
    fx = [dict(effect_id="zoom_in", parameters={}),
          dict(effect_id="saturation", parameters={"factor": 1.5})]
    a, _ = _render(tmp_path, "marker_left.mp4", fx, "a.mp4")
    b, _ = _render(tmp_path, "marker_left.mp4", fx, "b.mp4")
    assert a.out_path.read_bytes() == b.out_path.read_bytes()

def test_zoom_speed_follows_timeline_not_source_fps(tmp_path):
    # 60 fps source, 30 fps timeline: at t=1.0 s scale must be ~1.1 (area x1.21),
    # not 1.2 (area x1.44) which is what counting source frames would give.
    _, fx = _render(tmp_path, "marker_left_60.mp4", [dict(effect_id="zoom_in", parameters={
        "from_scale": 1.0, "to_scale": 1.2, "duration_ms": 2000})])
    ratio = _white(fx[30]).mean() / _white(fx[0]).mean()
    assert 1.12 < ratio < 1.32
