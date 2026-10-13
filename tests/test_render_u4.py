"""U4 render speed: one video encode, profile encoder settings, parallel-ready mux, timings."""
import subprocess
from pathlib import Path

import av
import imageio_ffmpeg
import numpy as np
import pytest

from poc.project_assets import ProjectAssetLibrary
from poc.render.editor import EditorRenderer, RenderCancelled
from poc.render.export import get_profile
from poc.timeline import (AudioProperties, EditorClip, EditorSettings, EditorTimeline, Layer,
                          MediaAsset, TimeBase, Track, TransitionRef)

FIX = Path(__file__).parent / "fixtures" / "ingest"
MUSIC = Path(__file__).resolve().parent.parent / "experiments" / "beat_flash" / "fixtures" / "synth_128.wav"
LAYERS = [Layer(layer_id="v", name="Visual", order=0), Layer(layer_id="a", name="Audio", order=1)]
TRACKS = [Track(track_id="video", kind="video", layer_id="v"),
          Track(track_id="overlay", kind="overlay", layer_id="v"),
          Track(track_id="voice", kind="audio", layer_id="a"),
          Track(track_id="music", kind="music", layer_id="a")]


@pytest.fixture
def commands(monkeypatch):
    """Record every process the renderer starts (FFmpeg calls go through Popen)."""
    seen: list[list[str]] = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **kw):
            seen.append([str(x) for x in args])
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    return seen


def _x264(commands):
    return [c for c in commands if "libx264" in c]


def _pair(cmd, flag, value):
    return any(cmd[i] == flag and cmd[i + 1] == value for i in range(len(cmd) - 1))


def _timeline(tmp_path, *, text=True, music=True):
    lib = ProjectAssetLibrary.create(tmp_path / "project", project_id="u4")
    paths = [FIX / "tone_a.mp4", FIX / "solid_blue.mp4"] + ([MUSIC] if music else [])
    imported = [lib.import_asset(p).media_asset for p in paths]
    a, blue = imported[0], imported[1]
    assets = list(imported)
    clips = [
        EditorClip(clip_id="a", asset_id=a.asset_id, track_id="video", start_tick=0,
                   duration_ticks=1500, source_in_ms=500, source_out_ms=2000),
        EditorClip(clip_id="b", asset_id=blue.asset_id, track_id="video", start_tick=1500,
                   duration_ticks=1500, source_in_ms=500, source_out_ms=2000,
                   transition=TransitionRef(transition_id="fade", duration_ticks=400)),
    ]
    if music:
        clips.append(EditorClip(clip_id="m", asset_id=imported[2].asset_id, track_id="music",
                                start_tick=0, duration_ticks=3000, source_in_ms=0,
                                source_out_ms=3000, audio=AudioProperties(gain_db=-6)))
    if text:
        assets.append(MediaAsset(asset_id="title", kind="text", text="HELLO"))
        clips.append(EditorClip(clip_id="t", asset_id="title", track_id="overlay",
                                start_tick=1700, duration_ticks=1000))
    tl = EditorTimeline(project_id="u4", timeline_id="t", duration_ticks=3000,
                        time_base=TimeBase(ticks_per_second=1000),
                        settings=EditorSettings(fps_num=30, fps_den=1, width=640, height=360),
                        assets=assets, layers=LAYERS, tracks=TRACKS, clips=clips)
    return lib, tl


def _render(tmp_path, commands, name="o.mp4", **kw):
    lib, tl = _timeline(tmp_path, **{k: kw.pop(k) for k in ("text", "music") if k in kw})
    plan = EditorRenderer().compile(tl, lib)
    commands.clear()
    return EditorRenderer().render(plan, out=tmp_path / "out" / name, **kw)


def _frames(path):
    with av.open(str(path)) as c:
        return [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]


def _white(img):
    return int((img.min(axis=2) > 200).sum())


def test_text_is_burned_in_the_single_video_encode(tmp_path, commands):
    res = _render(tmp_path, commands)
    assert len(_x264(commands)) == 1, [c[-1] for c in _x264(commands)]
    f = _frames(res.out_path)
    assert len(f) == 90
    assert _white(f[30]) == 0          # before the title (title starts at frame 51)
    assert _white(f[66]) > 200         # title visible
    assert _white(f[85]) == 0          # after the title (ends at frame 81)
    assert res.report.ok, res.report.problems


def test_profile_encoder_settings_are_used(tmp_path, commands):
    res = _render(tmp_path, commands, profile=get_profile("preview_540p"))
    (video,) = _x264(commands)
    assert _pair(video, "-preset", "veryfast") and _pair(video, "-crf", "28")
    assert any(_pair(c, "-b:a", "128k") for c in commands)
    assert res.encoder == {"crf": 28, "preset": "veryfast", "audio_bitrate": "128k"}


def test_default_encoder_settings_match_youtube_profile(tmp_path, commands):
    res = _render(tmp_path, commands, text=False)
    (video,) = _x264(commands)
    assert _pair(video, "-preset", "medium") and _pair(video, "-crf", "18")
    assert res.encoder == {"crf": 18, "preset": "medium", "audio_bitrate": "192k"}


def test_video_is_encoded_silently_and_muxed_without_reencoding(tmp_path, commands):
    res = _render(tmp_path, commands)
    (video,) = _x264(commands)
    assert "-an" in video
    assert any(_pair(c, "-c:v", "copy") for c in commands), "final mux must copy the video stream"
    with av.open(str(res.out_path)) as c:
        assert c.streams.audio[0].codec_context.name == "aac"
        assert c.streams.audio[0].codec_context.sample_rate == 48000


def test_loudness_check_does_not_decode_video(tmp_path, commands):
    _render(tmp_path, commands)
    loudness = [c for c in commands if any("ebur128" in part for part in c)]
    assert loudness
    for cmd in loudness:
        assert "-vn" in cmd


def test_render_reports_stage_timings(tmp_path, commands):
    res = _render(tmp_path, commands)
    assert {"audio", "video", "mux", "verify"} <= set(res.timings)
    assert all(isinstance(v, float) and v >= 0 for v in res.timings.values())
    assert res.timings["total"] >= max(v for k, v in res.timings.items() if k != "total")


def test_parallel_render_is_deterministic(tmp_path, commands):
    one = _render(tmp_path / "1", commands)
    two = _render(tmp_path / "2", commands)
    f1, f2 = _frames(one.out_path), _frames(two.out_path)
    assert len(f1) == len(f2)
    assert all(np.array_equal(x, y) for x, y in zip(f1, f2))
    with av.open(str(one.out_path)) as c1, av.open(str(two.out_path)) as c2:
        a1 = np.concatenate([f.to_ndarray() for f in c1.decode(audio=0)], axis=1)
        a2 = np.concatenate([f.to_ndarray() for f in c2.decode(audio=0)], axis=1)
    assert np.array_equal(a1, a2)


def test_cancel_stops_all_work_and_leaves_no_files(tmp_path, commands):
    lib, tl = _timeline(tmp_path)
    plan = EditorRenderer().compile(tl, lib)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    commands.clear()
    with pytest.raises(RenderCancelled):
        EditorRenderer().render(plan, out=out_dir / "o.mp4", cancel=lambda: len(commands) >= 2)
    assert list(out_dir.iterdir()) == []
