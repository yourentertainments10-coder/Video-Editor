import dataclasses, json, subprocess
import av, numpy as np, pytest, imageio_ffmpeg
from pathlib import Path
from poc.project_assets import ProjectAssetLibrary
from poc.timeline import (AudioProperties, EditorClip, EditorSettings, EditorTimeline,
                          Layer, TimeBase, Track, TransitionRef)
from poc.execute.editor_render import (EditorRenderError, EditorRenderer,
                                       build_editor_render_plan, render_editor_timeline)
FIX = Path(__file__).parent / "fixtures" / "ingest"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 48000
LAYERS = [Layer(layer_id="v", name="Visual", order=0), Layer(layer_id="a", name="Audio", order=1)]
TRACKS = [Track(track_id="video", kind="video", layer_id="v"),
          Track(track_id="voice", kind="audio", layer_id="a"),
          Track(track_id="music", kind="music", layer_id="a")]

def _wav(path, freq, dur=4.0):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:sample_rate=48000:duration={dur}",
                    "-ac", "2", "-c:a", "pcm_s16le", str(path)], check=True)
    return path

def _lib(tmp_path, *paths):
    lib = ProjectAssetLibrary.create(tmp_path / "project", project_id="p26")
    return lib, [lib.import_asset(p).media_asset for p in paths]

def _tl(assets, clips, duration_ms, fps_num=30, fps_den=1):
    return EditorTimeline(project_id="p26", timeline_id="t", duration_ticks=duration_ms,
                          time_base=TimeBase(ticks_per_second=1000),
                          settings=EditorSettings(fps_num=fps_num, fps_den=fps_den, width=640, height=360),
                          assets=assets, layers=LAYERS, tracks=TRACKS, clips=clips)

def _clip(cid, asset, start, dur, sin, track="video", **kw):
    return EditorClip(clip_id=cid, asset_id=asset.asset_id, track_id=track, start_tick=start,
                      duration_ticks=dur, source_in_ms=sin, source_out_ms=sin + dur, **kw)

def _frames(p):
    with av.open(str(p)) as c:
        return [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]

def _audio(p):
    with av.open(str(p)) as c:
        return np.concatenate([f.to_ndarray() for f in c.decode(audio=0)], axis=1)

def _mag(mono, centre, freq, n=4096):
    seg = mono[centre - n//2: centre + n//2] * np.hanning(n)
    spec = np.abs(np.fft.rfft(seg))
    b = int(round(freq * n / SR))
    return spec[max(b-2, 0): b+3].max()

def _rms(x):
    return float(np.sqrt((x.astype(float) ** 2).mean()))

def _two(tmp_path, transition):
    lib, (a, b) = _lib(tmp_path, FIX/"tone_a.mp4", FIX/"tone_b.mp4")
    clips = [_clip("a", a, 0, 2000, 1000),
             _clip("b", b, 2000, 2000, 1000, transition=transition)]
    return lib, _tl([a, b], clips, 4000)

def test_fade_transition_is_centred_and_verified(tmp_path):
    lib, tl = _two(tmp_path, TransitionRef(transition_id="fade", duration_ticks=500))
    res = render_editor_timeline(tl, lib, tmp_path/"o.mp4")
    f = _frames(res.out_path)
    assert 119 <= len(f) <= 121
    r, g, _ = f[60].reshape(-1, 3).mean(0)
    assert 80 < r < 180 and 30 < g < 100
    assert res.report.ok, res.report.problems

def test_dip_to_black_is_black_at_the_cut(tmp_path):
    lib, tl = _two(tmp_path, TransitionRef(transition_id="dip_to_black", duration_ticks=500))
    f = _frames(render_editor_timeline(tl, lib, tmp_path/"o.mp4").out_path)
    assert f[60].reshape(-1, 3).mean(0).max() < 40

def test_unsupported_transition_parameters_are_rejected(tmp_path):
    lib, tl = _two(tmp_path, TransitionRef(transition_id="fade", duration_ticks=500,
                                           parameters={"curve": "ease_in"}))
    with pytest.raises(EditorRenderError, match="not renderable"):
        render_editor_timeline(tl, lib, tmp_path/"o.mp4")

def test_clip_and_voice_gains_are_relative(tmp_path):
    voice = _wav(tmp_path/"v880.wav", 880)
    lib, (a, v) = _lib(tmp_path, FIX/"tone_a.mp4", voice)
    clips = [_clip("a", a, 0, 3000, 500),
             _clip("v", v, 0, 3000, 0, track="voice", audio=AudioProperties(gain_db=-12.0))]
    res = render_editor_timeline(_tl([a, v], clips, 3000), lib, tmp_path/"o.mp4")
    assert res.audio_clip_count == 2
    x = _audio(res.out_path)[0]
    ratio = _mag(x, int(1.5*SR), 880) / _mag(x, int(1.5*SR), 440)
    assert 0.18 < ratio < 0.35

def test_muted_clip_audio_is_silent(tmp_path):
    lib, (a,) = _lib(tmp_path, FIX/"tone_a.mp4")
    clips = [_clip("a", a, 0, 2000, 1000, audio=AudioProperties(muted=True))]
    res = render_editor_timeline(_tl([a], clips, 2000), lib, tmp_path/"o.mp4")
    assert np.abs(_audio(res.out_path)).max() < 1e-3

def test_pan_left_silences_right_channel(tmp_path):
    voice = _wav(tmp_path/"v880.wav", 880)
    lib, (r, v) = _lib(tmp_path, FIX/"solid_red.mp4", voice)
    clips = [_clip("r", r, 0, 2000, 1000),
             _clip("v", v, 0, 2000, 0, track="voice", audio=AudioProperties(pan=-1.0))]
    x = _audio(render_editor_timeline(_tl([r, v], clips, 2000), lib, tmp_path/"o.mp4").out_path)
    assert _rms(x[1]) < 0.05 * _rms(x[0])

def test_music_clip_starts_at_its_timeline_position(tmp_path):
    music = _wav(tmp_path/"m550.wav", 550)
    lib, (r, m) = _lib(tmp_path, FIX/"solid_red.mp4", music)
    clips = [_clip("r", r, 0, 3000, 0), _clip("m", m, 1000, 2000, 0, track="music")]
    x = _audio(render_editor_timeline(_tl([r, m], clips, 3000), lib, tmp_path/"o.mp4").out_path)[0]
    assert _rms(x[int(0.1*SR): int(0.9*SR)]) < 0.01 * _rms(x[int(1.5*SR): int(2.5*SR)])

def test_plan_is_plain_data(tmp_path):
    lib, tl = _two(tmp_path, TransitionRef(transition_id="fade", duration_ticks=500))
    plan = build_editor_render_plan(tl, lib)
    assert not any(isinstance(v, ProjectAssetLibrary) for v in vars(plan).values())
    json.dumps(dataclasses.asdict(plan))

def test_non_integer_frame_rate_is_rejected(tmp_path):
    lib, (a,) = _lib(tmp_path, FIX/"tone_a.mp4")
    tl = _tl([a], [_clip("a", a, 0, 2000, 1000)], 2000, fps_num=30000, fps_den=1001)
    with pytest.raises(EditorRenderError, match="frame rate"):
        build_editor_render_plan(tl, lib)

def test_speed_change_is_rejected(tmp_path):
    lib, (a,) = _lib(tmp_path, FIX/"tone_a.mp4")
    clip = EditorClip(clip_id="a", asset_id=a.asset_id, track_id="video", start_tick=0,
                      duration_ticks=2000, source_in_ms=0, source_out_ms=1000)
    with pytest.raises(EditorRenderError, match="speed"):
        build_editor_render_plan(_tl([a], [clip], 2000), lib)

def test_progress_is_monotonic_and_ends_at_one(tmp_path):
    lib, tl = _two(tmp_path, TransitionRef(transition_id="fade", duration_ticks=500))
    seen = []
    r = EditorRenderer()
    r.render(r.compile(tl, lib), out=tmp_path/"o.mp4", progress=lambda f, m: seen.append(f))
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)

def test_cancel_leaves_no_output(tmp_path):
    lib, tl = _two(tmp_path, TransitionRef(transition_id="fade", duration_ticks=500))
    r = EditorRenderer()
    with pytest.raises(EditorRenderError, match="cancel"):
        r.render(r.compile(tl, lib), out=tmp_path/"o.mp4", cancel=lambda: True)
    assert list(tmp_path.glob("o*.mp4")) == []

def test_render_is_byte_identical(tmp_path):
    lib, tl = _two(tmp_path, TransitionRef(transition_id="fade", duration_ticks=500))
    a = render_editor_timeline(tl, lib, tmp_path/"a.mp4").out_path.read_bytes()
    b = render_editor_timeline(tl, lib, tmp_path/"b.mp4").out_path.read_bytes()
    assert a == b

def test_trim_uses_correct_source_time_with_input_seeking(tmp_path):
    lib, (v,) = _lib(tmp_path, FIX/"vfr_clip.mp4")
    clips = [_clip("g", v, 0, 1000, 2500), _clip("b", v, 1000, 1000, 4500)]
    f = _frames(render_editor_timeline(_tl([v], clips, 2000), lib, tmp_path/"o.mp4").out_path)
    g = f[15].reshape(-1, 3).mean(0)
    b = f[45].reshape(-1, 3).mean(0)
    assert g[1] > 100 and g[0] < 60 and g[2] < 60
    assert b[2] > 200 and b[0] < 60 and b[1] < 60
