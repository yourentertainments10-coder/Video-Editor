"""U4.5: bounded-memory chunked render, transition timebase fix, exact silent audio."""
import subprocess
from pathlib import Path

import av
import numpy as np
import pytest

from poc.project_assets import ProjectAssetLibrary
from poc.render.editor import EditorRenderer
from poc.timeline import (EditorClip, EditorSettings, EditorTimeline, Layer, MediaAsset,
                          TimeBase, Track, TransitionRef)

FIX = Path(__file__).parent / "fixtures" / "ingest"
LAYERS = [Layer(layer_id="v", name="Visual", order=0), Layer(layer_id="a", name="Audio", order=1)]
TRACKS = [Track(track_id="video", kind="video", layer_id="v"),
          Track(track_id="overlay", kind="overlay", layer_id="v"),
          Track(track_id="voice", kind="audio", layer_id="a"),
          Track(track_id="music", kind="music", layer_id="a")]
COLOURS = ("solid_red.mp4", "solid_green.mp4", "solid_blue.mp4")   # channel 0, 1, 2
CLIP_MS = 300                                                       # 9 frames at 30 fps


@pytest.fixture
def commands(monkeypatch):
    seen: list[list[str]] = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **kw):
            seen.append([str(x) for x in args])
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    return seen


def _timeline(tmp_path, n, *, fade_at=None, title=None):
    lib = ProjectAssetLibrary.create(tmp_path / "project", project_id="chunks")
    media = [lib.import_asset(FIX / name).media_asset for name in COLOURS]
    clips = []
    for k in range(n):
        src_in = 500 + (k * 100) % 2000
        clips.append(EditorClip(
            clip_id=f"c{k:02d}", asset_id=media[k % 3].asset_id, track_id="video",
            start_tick=k * CLIP_MS, duration_ticks=CLIP_MS,
            source_in_ms=src_in, source_out_ms=src_in + CLIP_MS,
            transition=TransitionRef(transition_id="fade", duration_ticks=120) if k == fade_at else None,
        ))
    assets = list(media)
    if title:
        assets.append(MediaAsset(asset_id="title", kind="text", text="CHUNK TITLE"))
        clips.append(EditorClip(clip_id="t", asset_id="title", track_id="overlay",
                                start_tick=title[0], duration_ticks=title[1] - title[0]))
    tl = EditorTimeline(project_id="chunks", timeline_id="t", duration_ticks=n * CLIP_MS,
                        time_base=TimeBase(ticks_per_second=1000),
                        settings=EditorSettings(fps_num=30, fps_den=1, width=640, height=360),
                        assets=assets, layers=LAYERS, tracks=TRACKS, clips=clips)
    return lib, tl


def _render(tmp_path, commands, n, **kw):
    lib, tl = _timeline(tmp_path, n, **kw)
    plan = EditorRenderer().compile(tl, lib)
    commands.clear()
    return EditorRenderer().render(plan, out=tmp_path / "out" / "o.mp4")


def _frames(path):
    with av.open(str(path)) as c:
        return [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]


def _inputs(cmd):
    return sum(1 for part in cmd if part == "-i")


def test_no_ffmpeg_process_opens_more_than_eight_video_inputs(tmp_path, commands):
    res = _render(tmp_path, commands, 20)
    encodes = [c for c in commands if "libx264" in c]
    assert len(encodes) >= 3                      # 20 clips need at least 3 chunks
    assert max(_inputs(c) for c in encodes) <= 8
    assert any(_pair_has(c, "-c", "copy") or _pair_has(c, "-c:v", "copy") for c in commands)
    assert res.report.ok, res.report.problems


def _pair_has(cmd, flag, value):
    return any(cmd[i] == flag and cmd[i + 1] == value for i in range(len(cmd) - 1))


def test_every_frame_shows_the_right_clip_in_order(tmp_path, commands):
    res = _render(tmp_path, commands, 20)
    frames = _frames(res.out_path)
    assert len(frames) == 20 * 9
    for index, frame in enumerate(frames):
        expected = (index // 9) % 3
        assert int(frame.reshape(-1, 3).mean(0).argmax()) == expected, f"frame {index}"


def test_a_transition_is_never_split_between_chunks(tmp_path, commands):
    # A fade into clip 8 sits exactly where a naive 8-clip chunk would end.
    res = _render(tmp_path, commands, 12, fade_at=8)
    frames = _frames(res.out_path)
    assert len(frames) == 12 * 9
    cut = 8 * 9                                    # frame 72: inside the 4-frame fade (frames 70-73)
    mean = frames[cut].reshape(-1, 3).mean(0)
    # clip 7 is green (1), clip 8 is blue (2): the middle frame is a real blend of both
    assert mean[1] > 60 and mean[2] > 60, mean
    assert int(frames[cut - 6].reshape(-1, 3).mean(0).argmax()) == 1
    assert int(frames[cut + 6].reshape(-1, 3).mean(0).argmax()) == 2


def test_title_crossing_a_chunk_boundary_stays_continuous(tmp_path, commands):
    res = _render(tmp_path, commands, 20, title=(2000, 3800))     # frames 60..113
    frames = _frames(res.out_path)
    white = [int((f.min(axis=2) > 200).sum()) for f in frames]
    assert white[50] == 0 and white[125] == 0
    assert all(white[i] > 100 for i in range(64, 110)), [i for i in range(64, 110) if white[i] <= 100]
    # Debug: print commands for video encodes
    encodes = [c for c in commands if "libx264" in c]
    for i, cmd in enumerate(encodes):
        print(f"Encode {i}: {' '.join(cmd)}")


def test_chunked_render_is_deterministic(tmp_path, commands):
    one = _frames(_render(tmp_path / "1", commands, 20, title=(2000, 3800)).out_path)
    two = _frames(_render(tmp_path / "2", commands, 20, title=(2000, 3800)).out_path)
    assert len(one) == len(two)
    assert all(np.array_equal(a, b) for a, b in zip(one, two))


@pytest.mark.parametrize("kind", ["fade", "wipe_left", "dip_to_black"])
def test_transition_after_an_earlier_cut_renders(tmp_path, commands, kind):
    # Regression: concat output has a 1/1000000 timebase and xfade refused it
    # ("First input link main timebase ... do not match"), so any fade/wipe after a cut failed.
    lib, tl = _timeline(tmp_path, 3)
    clips = [c.model_copy(update={"transition": TransitionRef(transition_id=kind, duration_ticks=120)})
             if c.clip_id == "c02" else c for c in tl.clips]
    tl = tl.model_copy(update={"clips": clips})
    res = EditorRenderer().render(EditorRenderer().compile(tl, lib), out=tmp_path / "out" / "o.mp4")
    assert len(_frames(res.out_path)) == 27
    assert res.report.ok, res.report.problems


def test_silent_clip_audio_length_is_exact_every_time(tmp_path):
    # Regression: silence for clips without audio came from a separate `-f lavfi anullsrc`
    # input; with a crossfade after a cut the track was randomly cut short (0.19-0.53 s of 0.9 s).
    import soundfile as sf
    from poc.render.audio import render_audio_track
    from poc.render.transitions import Join, Segment
    segs = [Segment(FIX / "solid_red.mp4", 500, 800), Segment(FIX / "solid_green.mp4", 600, 900),
            Segment(FIX / "solid_blue.mp4", 700, 1000)]
    for run in range(30):
        out = render_audio_track(segs, [Join("cut", 0), Join("crossfade", 133)],
                                 tmp_path / f"a{run}.wav", fps=30)
        data, rate = sf.read(out)
        assert rate == 48000
        assert len(data) == 43200, f"run {run}: {len(data)} samples"