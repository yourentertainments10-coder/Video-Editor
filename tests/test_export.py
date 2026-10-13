import re, subprocess, hashlib
import av, pytest
from pathlib import Path
import imageio_ffmpeg
from poc.render.transitions import Segment, Join
from poc.render.export import EXPORT_PROFILES, get_profile, export_sequence, verify_render
FIX = Path(__file__).parent / "fixtures" / "ingest"

def _segs():
    return [Segment(path=FIX/"tone_a.mp4", src_in_ms=1000, src_out_ms=3000),
            Segment(path=FIX/"tone_b.mp4", src_in_ms=1000, src_out_ms=3000),
            Segment(path=FIX/"solid_blue.mp4", src_in_ms=1000, src_out_ms=3000)]

JOINS = [Join("crossfade", 500), Join("fade_black", 500)]

def _lufs(p):
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostats",
                        "-i", str(p), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])
    tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)[-1])
    return i, tp

def _frame_hashes(p):
    with av.open(str(p)) as c:
        return [hashlib.sha256(f.to_ndarray(format="rgb24").tobytes()).hexdigest()
                for f in c.decode(video=0)]

def test_profiles_are_sane():
    assert {"youtube_1080p", "reels_9x16", "square_1x1", "preview_540p"} <= set(EXPORT_PROFILES)
    for name in EXPORT_PROFILES:
        p = get_profile(name)
        assert p.width % 2 == 0 and p.height % 2 == 0 and p.fps > 0
    r = get_profile("reels_9x16")
    assert (r.width, r.height) == (1080, 1920)
    with pytest.raises(ValueError):
        get_profile("nope")

def test_export_passes_its_own_verification(tmp_path):
    res = export_sequence(_segs(), JOINS, tmp_path/"o.mp4", profile="preview_540p",
                          music_path=FIX/"music_bed.wav")
    assert res.report.ok, res.report.problems
    with av.open(str(res.path)) as c:
        v = c.streams.video[0]
        assert (v.width, v.height) == (960, 540)
        assert len(c.streams.audio) == 1 and c.streams.audio[0].sample_rate == 48000

def test_export_loudness_hits_target(tmp_path):
    res = export_sequence(_segs(), JOINS, tmp_path/"o.mp4", profile="preview_540p",
                          music_path=FIX/"music_bed.wav")
    i, tp = _lufs(res.path)
    assert abs(i + 14.0) <= 1.0 and tp <= -0.5

def test_export_is_faststart(tmp_path):
    res = export_sequence(_segs(), JOINS, tmp_path/"o.mp4", profile="preview_540p")
    data = res.path.read_bytes()
    assert 0 <= data.find(b"moov") < data.find(b"mdat")

def test_vertical_export(tmp_path):
    res = export_sequence(_segs(), JOINS, tmp_path/"r.mp4", profile="reels_9x16")
    assert res.report.ok, res.report.problems
    assert (res.report.measured["width"], res.report.measured["height"]) == (1080, 1920)

def test_silent_export_does_not_crash(tmp_path):
    segs = [Segment(path=FIX/"solid_red.mp4", src_in_ms=1000, src_out_ms=3000),
            Segment(path=FIX/"solid_blue.mp4", src_in_ms=1000, src_out_ms=3000)]
    res = export_sequence(segs, [Join("cut", 0)], tmp_path/"s.mp4", profile="preview_540p")
    assert res.report.ok, res.report.problems

def test_verify_flags_wrong_files():
    r = verify_render(FIX/"solid_red.mp4", expected_width=1920, expected_height=1080,
                      expected_fps=30, expected_duration_ms=2000, expect_audio=True)
    assert not r.ok
    starts = {p.split(":")[0] for p in r.problems}
    assert {"resolution", "audio", "duration"} <= starts

def test_export_is_deterministic(tmp_path):
    a = export_sequence(_segs(), JOINS, tmp_path/"a.mp4", profile="preview_540p").path
    b = export_sequence(_segs(), JOINS, tmp_path/"b.mp4", profile="preview_540p").path
    assert _frame_hashes(a) == _frame_hashes(b)
