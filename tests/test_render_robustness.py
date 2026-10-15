"""Renders found broken on real footage (phase 3c quality pass).

1. Phone videos (WhatsApp) have variable frame rates and can skip frames for a second. A short
   cut inside such a gap decoded no frames, and every transition after it ended the video early
   (a 18.9 s reel came out 16.4 s long).
2. Long timelines are rendered in chunks; a title in a later chunk was placed with the wrong
   offset and disappeared.
3. Film grain made a 19 s reel 220 MB: exports now have a bitrate ceiling.
"""
import subprocess
from pathlib import Path

import av
import imageio_ffmpeg
import numpy as np
import pytest

from poc.execute.editor_render import EditorRenderer, render_editor_timeline
from poc.project_assets import ProjectAssetLibrary
from poc.render.export import EXPORT_PROFILES
from poc.render.transitions import Join, Segment, render_sequence_to_file
from poc.timeline import (EditorClip, EditorSettings, EditorTimeline, EffectRef, Layer, MediaAsset, TimeBase,
                          Track, TransitionRef)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FIX = Path(__file__).parent / "fixtures" / "ingest"


@pytest.fixture(scope="module")
def gappy(tmp_path_factory):
    """8 s of video with NO frames between 3.0 s and 4.2 s (timestamps kept), like a phone recording."""
    path = tmp_path_factory.mktemp("vfr") / "gap.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30",
                    "-t", "8", "-vf", "select='not(between(t,3.0,4.2))'", "-fps_mode", "passthrough",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def _gray(path):
    with av.open(str(path)) as c:
        return [f.to_ndarray(format="gray").astype(int) for f in c.decode(video=0)]


@pytest.mark.parametrize("seeking", [True, False])
def test_a_cut_inside_a_frame_gap_keeps_the_whole_video(tmp_path, gappy, seeking):
    segs = [Segment(path=gappy, src_in_ms=1000, src_out_ms=2000), Segment(path=gappy, src_in_ms=3300, src_out_ms=3800),
            Segment(path=gappy, src_in_ms=5000, src_out_ms=6000), Segment(path=gappy, src_in_ms=6500, src_out_ms=7500)]
    joins = [Join("cut", 0), Join("cut", 0), Join("crossfade", 200)]
    out = render_sequence_to_file(segs, joins, tmp_path / "o.mp4", width=320, height=568, fps=30,
                                  input_seeking=seeking)
    frames = _gray(out)
    assert len(frames) == 30 + 15 + 30 + 30
    source = [(round(f.time * 1000), f.to_ndarray(format="gray").astype(int)) for f in av.open(str(gappy)).decode(video=0)]
    best = lambda img: min(source, key=lambda s: np.abs(img - s[1]).mean())[0]
    assert best(frames[0]) == 1000 and best(frames[29]) == 1967          # right frames, right timing
    assert best(frames[30]) == best(frames[44]) == 2967                   # the gap holds the last real frame
    assert best(frames[45]) == 5000


def test_a_title_in_a_later_render_chunk_is_drawn(tmp_path):
    lib = ProjectAssetLibrary.create(tmp_path / "project", project_id="ch")
    blue, red = (lib.import_asset(FIX / n).media_asset for n in ("solid_blue.mp4", "solid_red.mp4"))
    clips = [EditorClip(clip_id=f"c{k}", asset_id=(blue, red)[k % 2].asset_id, track_id="video", start_tick=k * 400,
                        duration_ticks=400, source_in_ms=200, source_out_ms=600) for k in range(20)]
    title = MediaAsset(asset_id="t", kind="text", text="THE END", metadata={"title_style": {"animation": "pop"}})
    clips.append(EditorClip(clip_id="title", asset_id="t", track_id="overlay", start_tick=6000, duration_ticks=2000))
    tl = EditorTimeline(project_id="ch", timeline_id="t", duration_ticks=8000, time_base=TimeBase(ticks_per_second=1000),
                        settings=EditorSettings(fps_num=30, fps_den=1, width=640, height=360),
                        assets=[blue, red, title],
                        layers=[Layer(layer_id="v", name="Visual", order=0), Layer(layer_id="a", name="Audio", order=1)],
                        tracks=[Track(track_id="video", kind="video", layer_id="v"),
                                Track(track_id="overlay", kind="overlay", layer_id="v")],
                        clips=clips)
    result = render_editor_timeline(tl, lib, tmp_path / "o.mp4")
    assert result.report.ok, result.report.problems
    with av.open(str(result.out_path)) as c:
        frames = [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]
    assert len(frames) == 240
    white = lambda img: int((img.min(axis=2) > 200).sum())
    assert white(frames[150]) == 0                     # before the title
    assert white(frames[200]) > 200 and white(frames[235]) > 200


def test_exports_have_a_bitrate_ceiling(tmp_path):
    assert all(p.max_kbps for p in EXPORT_PROFILES.values())
    lib = ProjectAssetLibrary.create(tmp_path / "project", project_id="br")
    tone = lib.import_asset(FIX / "tone_a.mp4").media_asset
    clip = EditorClip(clip_id="c", asset_id=tone.asset_id, track_id="video", start_tick=0, duration_ticks=3000,
                      source_in_ms=0, source_out_ms=3000, effects=[EffectRef(effect_id="grain", parameters={"amount": 1.0})])
    tl = EditorTimeline(project_id="br", timeline_id="t", duration_ticks=3000, time_base=TimeBase(ticks_per_second=1000),
                        settings=EditorSettings(fps_num=30, fps_den=1, width=640, height=360), assets=[tone],
                        layers=[Layer(layer_id="v", name="Visual", order=0)],
                        tracks=[Track(track_id="video", kind="video", layer_id="v")], clips=[clip])
    renderer = EditorRenderer()
    result = renderer.render(renderer.compile(tl, lib), out=tmp_path / "o.mp4", profile=EXPORT_PROFILES["preview_540p"])
    kbps = result.out_path.stat().st_size * 8 / 1000 / 3.0
    assert kbps < EXPORT_PROFILES["preview_540p"].max_kbps * 1.6, kbps
