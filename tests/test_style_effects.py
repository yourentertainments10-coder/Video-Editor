"""Phase 3: new looks, textures, beat accents and transitions render through the real renderer."""
from pathlib import Path

import av
import numpy as np
import pytest

from poc.execute.editor_render import render_editor_timeline
from poc.project_assets import ProjectAssetLibrary
from poc.timeline import (EFFECT_CATALOGUE, TRANSITION_CATALOGUE, EditorClip, EditorSettings, EditorTimeline,
                          EffectRef, Layer, TimeBase, Track, TransitionRef)

FIX = Path(__file__).parent / "fixtures" / "ingest"


def _render(tmp_path, effects, *, fixture="marker_left.mp4", name="o.mp4"):
    lib = ProjectAssetLibrary.create(tmp_path / f"proj-{name}", project_id="fx")
    a = lib.import_asset(FIX / fixture).media_asset
    tl = EditorTimeline(
        project_id="fx", timeline_id="t", duration_ticks=2000, time_base=TimeBase(ticks_per_second=1000),
        settings=EditorSettings(fps_num=30, fps_den=1, width=640, height=360), assets=[a],
        layers=[Layer(layer_id="v", name="V", order=0)], tracks=[Track(track_id="video", kind="video", layer_id="v")],
        clips=[EditorClip(clip_id="c", asset_id=a.asset_id, track_id="video", start_tick=0, duration_ticks=2000,
                          source_in_ms=0, source_out_ms=2000, effects=[EffectRef(**e) for e in effects])])
    res = render_editor_timeline(tl, lib, tmp_path / name)
    with av.open(str(res.out_path)) as c:
        return [f.to_ndarray(format="rgb24").astype(float) for f in c.decode(video=0)]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return _render(tmp_path_factory.mktemp("base"), [])


AMOUNT_EFFECTS = ["look_cinematic", "look_vintage", "look_warm", "look_cool", "look_punchy", "look_mono", "look_matte",
                  "vignette", "grain", "sharpen", "glow", "rgb_split", "stabilize"]


@pytest.mark.parametrize("effect_id", AMOUNT_EFFECTS)
def test_every_new_effect_renders_and_changes_the_picture(tmp_path, base, effect_id):
    frames = _render(tmp_path, [dict(effect_id=effect_id, parameters={"amount": 0.8})])
    assert len(frames) == len(base)
    diff = np.abs(frames[30] - base[30])
    assert diff.mean() > 0.5 or diff.max() > 20, effect_id      # sharpen only touches edges


def test_looks_do_what_they_say(tmp_path, base):
    red = _render(tmp_path, [], fixture="solid_red.mp4", name="red.mp4")[30].reshape(-1, 3).mean(0)
    mono = _render(tmp_path, [dict(effect_id="look_mono", parameters={"amount": 1})], fixture="solid_red.mp4",
                   name="mono.mp4")[30].reshape(-1, 3).mean(0)
    assert mono.max() - mono.min() < 10 and red[0] - red[2] > 100
    warm = _render(tmp_path, [dict(effect_id="look_warm", parameters={"amount": 1})], name="w.mp4")[30]
    cool = _render(tmp_path, [dict(effect_id="look_cool", parameters={"amount": 1})], name="c.mp4")[30]
    balance = lambda img: img[..., 0].mean() - img[..., 2].mean()     # red minus blue
    assert balance(warm) > balance(base[30]) > balance(cool)
    vig = _render(tmp_path, [dict(effect_id="vignette", parameters={"amount": 1})], name="v.mp4")[30]
    corner = lambda img: img[:40, :40].mean()
    assert corner(vig) < corner(base[30]) - 5


def test_flash_brightens_at_its_moment_only(tmp_path, base):
    frames = _render(tmp_path, [dict(effect_id="flash", parameters={"at_ms": 1000, "duration_ms": 300, "amount": 1})])
    assert frames[30].mean() > base[30].mean() + 40          # at 1.0 s
    assert abs(frames[10].mean() - base[10].mean()) < 2 and abs(frames[50].mean() - base[50].mean()) < 2


def test_zoom_punch_and_shake_move_the_picture_only_around_their_moment(tmp_path, base):
    white = lambda img: (img.min(axis=2) > 200).mean()
    punch = _render(tmp_path, [dict(effect_id="zoom_punch", parameters={"at_ms": 1000, "duration_ms": 400,
                                                                        "amount": 1})], name="p.mp4")
    assert white(punch[30]) > 1.15 * white(base[30])
    assert abs(white(punch[50]) - white(base[50])) < 0.01
    shake = _render(tmp_path, [dict(effect_id="shake", parameters={"at_ms": 900, "duration_ms": 400, "amount": 1})],
                    name="s.mp4")
    diffs = [np.abs(shake[i] - shake[i + 1]).mean() for i in range(len(shake) - 1)]
    assert max(diffs[27:39]) > 3 * max(max(diffs[:20]), 0.5)


def test_catalogue_lists_everything_as_renderable():
    assert all(e.renderable for e in EFFECT_CATALOGUE)
    assert all(t.renderable for t in TRANSITION_CATALOGUE)


NEW_TRANSITIONS = [t.transition_id for t in TRANSITION_CATALOGUE if t.transition_id not in ("cut", "fade", "dip_to_black")]


@pytest.mark.parametrize("group", [NEW_TRANSITIONS[i:i + 6] for i in range(0, len(NEW_TRANSITIONS), 6)])
def test_every_transition_renders(tmp_path, group):
    lib = ProjectAssetLibrary.create(tmp_path / "proj", project_id="tr")
    red = lib.import_asset(FIX / "solid_red.mp4").media_asset
    blue = lib.import_asset(FIX / "solid_blue.mp4").media_asset
    clips = []
    for i in range(len(group) + 1):
        a = (red, blue)[i % 2]
        transition = None
        if i:
            tid = group[i - 1]
            extra = {"parameters": {"mask_seed": 1}} if tid == "dissolve" else {}
            transition = TransitionRef(transition_id=tid, duration_ticks=200, **extra)
        clips.append(EditorClip(clip_id=f"c{i}", asset_id=a.asset_id, track_id="video", start_tick=i * 600,
                                duration_ticks=600, source_in_ms=200 + i * 100, source_out_ms=800 + i * 100,
                                transition=transition))
    tl = EditorTimeline(project_id="tr", timeline_id="t", duration_ticks=600 * len(clips),
                        time_base=TimeBase(ticks_per_second=1000),
                        settings=EditorSettings(fps_num=30, fps_den=1, width=320, height=180), assets=[red, blue],
                        layers=[Layer(layer_id="v", name="V", order=0)],
                        tracks=[Track(track_id="video", kind="video", layer_id="v")], clips=clips)
    res = render_editor_timeline(tl, lib, tmp_path / "t.mp4")
    assert res.report.ok, res.report.problems
    with av.open(str(res.out_path)) as c:
        frames = [f.to_ndarray(format="rgb24").astype(float) for f in c.decode(video=0)]
    assert len(frames) == 18 * len(clips)
    pure = lambda img, ch: img[..., ch].mean() > 200 and img[..., 2 - ch].mean() < 40
    for i in range(1, len(clips)):                      # around each cut some frame is neither clip alone
        near = frames[i * 18 - 3:i * 18 + 3]
        assert any(not pure(f, 0) and not pure(f, 2) for f in near), group[i - 1]
    assert frames[9][..., 0].mean() > 180 and frames[18 + 9][..., 2].mean() > 180   # clips themselves intact
