"""Cached preview media must survive concurrent requests (two players / Range requests).

Regression: the music preview (.m4a) and proxies were built to a partial file named only by
process id, so two simultaneous requests wrote the same file. The cache ended up corrupt
(two 'moov' boxes) and Chrome refused it: no music in the timeline preview.
"""
import subprocess
import threading
from pathlib import Path

import av
import imageio_ffmpeg

from poc.ingest.proxy import make_audio_preview, make_proxy, make_thumbnails
from poc.timeline import MediaAsset

FIX = Path(__file__).parent / "fixtures" / "ingest"


def _together(fn, n=4):
    results, errors = [], []

    def run():
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def _song(tmp_path) -> Path:
    path = tmp_path / "song.mp3"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=330:duration=60", "-c:a", "libmp3lame", "-b:a", "192k", str(path)],
                   check=True)
    return path


def test_concurrent_audio_previews_give_one_playable_file(tmp_path):
    song = _song(tmp_path)
    asset = MediaAsset(asset_id="m", kind="audio", uri=str(song), duration_ms=60000)
    results, errors = _together(lambda: make_audio_preview(asset, tmp_path / "cache"))
    assert errors == []
    assert len({str(p) for p in results}) == 1
    out = results[0]
    with av.open(str(out)) as c:
        samples = sum(f.samples for f in c.decode(audio=0))
    assert abs(samples - 60 * 48000) < 48000
    assert [box for box in _boxes(out.read_bytes()) if box == "moov"] == ["moov"]
    assert not list(out.parent.glob("*.partial*"))


def test_concurrent_proxies_give_one_playable_file(tmp_path):
    asset = MediaAsset(asset_id="v", kind="video", uri=str(FIX / "tone_a.mp4"), duration_ms=4000)
    results, errors = _together(lambda: make_proxy(asset, tmp_path / "cache"))
    assert errors == []
    assert len({str(p) for p in results}) == 1
    with av.open(str(results[0])) as c:
        assert sum(1 for _ in c.decode(video=0)) > 0
    assert not list(results[0].parent.glob("*.partial*"))


def test_concurrent_thumbnails_are_all_valid(tmp_path):
    asset = MediaAsset(asset_id="v", kind="video", uri=str(FIX / "tone_a.mp4"), duration_ms=4000)
    results, errors = _together(lambda: make_thumbnails(asset, tmp_path / "cache", count=4))
    assert errors == []
    for thumb in results[0]:
        with av.open(str(thumb)) as c:
            next(c.decode(video=0))


def _boxes(data: bytes) -> list[str]:
    boxes, i = [], 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        boxes.append(data[i + 4:i + 8].decode("latin-1"))
        if size < 8:
            break
        i += size
    return boxes
