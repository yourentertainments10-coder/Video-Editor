import re, subprocess, wave
import av, numpy as np, pytest
from pathlib import Path
import imageio_ffmpeg
from poc.render.transitions import Segment, Join, render_sequence_to_file
from poc.render.audio import render_audio_track, mix_with_music, loudness_normalize
FIX = Path(__file__).parent / "fixtures" / "ingest"
SR = 48000

def _segs():
    return [Segment(path=FIX/"tone_a.mp4", src_in_ms=1000, src_out_ms=3000),
            Segment(path=FIX/"tone_b.mp4", src_in_ms=1000, src_out_ms=3000),
            Segment(path=FIX/"solid_blue.mp4", src_in_ms=1000, src_out_ms=3000)]

def _wav(p):
    with wave.open(str(p)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 2 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)

def _mag(x, centre, freq, n=1024):
    seg = x[centre - n//2: centre + n//2, 0].astype(float) * np.hanning(n)
    spec = np.abs(np.fft.rfft(seg))
    b = int(round(freq * n / SR))
    return spec[max(b-2, 0): b+3].max()

def _lufs(p):
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostats",
                        "-i", str(p), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])
    tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)[-1])
    return i, tp

def test_track_length_is_sample_exact(tmp_path):
    x = _wav(render_audio_track(_segs(), [Join("cut", 0), Join("cut", 0)], tmp_path/"a.wav"))
    assert abs(len(x) - 6 * SR) <= SR // 1000

def test_cut_is_click_free(tmp_path):
    x = _wav(render_audio_track(_segs(), [Join("cut", 0), Join("cut", 0)], tmp_path/"a.wav"))
    mono = x[:, 0].astype(float)
    cut = 2 * SR
    jump = np.abs(np.diff(mono[cut-480: cut+480])).max()
    steady = np.abs(np.diff(mono[cut+4800: cut+9600])).max()
    assert jump <= steady * 1.3 + 50

def test_segment_without_audio_is_silent(tmp_path):
    x = _wav(render_audio_track(_segs(), [Join("cut", 0), Join("cut", 0)], tmp_path/"a.wav"))
    assert np.abs(x[int(4.05*SR): int(5.95*SR)]).max() < 5

def test_crossfade_overlaps_both_sources(tmp_path):
    xf = _wav(render_audio_track(_segs(), [Join("crossfade", 500), Join("cut", 0)], tmp_path/"x.wav"))
    ct = _wav(render_audio_track(_segs(), [Join("cut", 0), Join("cut", 0)], tmp_path/"c.wav"))
    c = 2*SR + int(0.150*SR)
    assert _mag(xf, c, 440) > 0.1 * _mag(xf, c, 880)
    assert _mag(ct, c, 440) < 0.03 * _mag(ct, c, 880)

def test_audio_track_is_deterministic(tmp_path):
    j = [Join("crossfade", 500), Join("cut", 0)]
    a = _wav(render_audio_track(_segs(), j, tmp_path/"a.wav"))
    b = _wav(render_audio_track(_segs(), j, tmp_path/"b.wav"))
    assert np.array_equal(a, b)

def test_music_ducks_under_voice(tmp_path):
    on = _wav(mix_with_music(FIX/"voice.wav", FIX/"music_bed.wav", tmp_path/"on.wav", duck=True))
    off = _wav(mix_with_music(FIX/"voice.wav", FIX/"music_bed.wav", tmp_path/"off.wav", duck=False))
    assert abs(len(on) - 6*SR) <= SR // 1000
    assert _mag(on, 3*SR, 220) <= 0.5 * _mag(on, 1*SR, 220)
    assert _mag(off, 3*SR, 220) >= 0.9 * _mag(off, 1*SR, 220)

def test_loudness_normalised_to_target(tmp_path):
    out = loudness_normalize(FIX/"music_bed.wav", tmp_path/"n.wav", target_lufs=-14.0, true_peak_db=-1.0)
    i, tp = _lufs(out)
    assert abs(i - (-14.0)) <= 1.0
    assert tp <= -0.5

def test_sequence_with_audio_stays_in_sync(tmp_path):
    segs, j = _segs(), [Join("crossfade", 500), Join("cut", 0)]
    wav = render_audio_track(segs, j, tmp_path/"a.wav")
    out = render_sequence_to_file(segs, j, tmp_path/"v.mp4", width=640, height=360,
                                  fps=30, audio_path=wav)
    with av.open(str(out)) as c:
        assert len(c.streams.audio) == 1
        v, a = c.streams.video[0], c.streams.audio[0]
        vd, ad = float(v.duration * v.time_base), float(a.duration * a.time_base)
    assert abs(vd - ad) < 0.05
