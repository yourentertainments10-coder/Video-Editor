"""Phase 2: the Director can see what is in the footage (faces, shot size, light, shake), offline."""
from pathlib import Path

import pytest

from poc.perceive.shots import analyze_shots, describe, faces_available, find_segments
from tests import shot_fixtures as fx

pytestmark = pytest.mark.skipif(not faces_available(), reason="needs OpenCV 4.x (Haar face cascades)")


@pytest.fixture(scope="module")
def tour(tmp_path_factory):
    folder = tmp_path_factory.mktemp("shots")
    path = fx.tour(folder / "tour.mp4")
    return path, analyze_shots(path, cache_dir=folder / "cache")


def _part(name: str) -> tuple[int, int]:
    i = fx.TOUR.index(name)
    return i * 4000 + 500, i * 4000 + 3500           # stay clear of the joins


def test_each_part_is_described(tour):
    _, shots = tour
    assert shots.faces_measured and shots.duration_ms == 20000
    medium, close, dark, shaky, empty = (describe(shots, *_part(n)) for n in fx.TOUR)
    assert medium.scale == "medium" and medium.faces == 1 and medium.light == "normal" and not medium.shaky
    assert close.scale == "close-up" and close.faces == 1
    assert dark.light == "dark"
    assert shaky.shaky and shaky.faces == 1
    assert empty.faces == 0 and empty.scale is None and empty.has("no-faces") and empty.has("wide")
    assert close.labels() == ["close-up", "1 face", "still"]


def test_short_clips_between_face_samples_still_get_faces(tour):
    _, shots = tour
    start = fx.TOUR.index("close-up") * 4000 + 1300     # 0.4 s, between the 1 Hz face samples
    assert describe(shots, start, start + 400).scale == "close-up"


def test_find_segments_returns_matching_unused_footage(tour):
    _, shots = tour
    lo, hi = fx.TOUR.index("close-up") * 4000, fx.TOUR.index("close-up") * 4000 + 4000
    starts = find_segments(shots, 1000, ["close-up"])
    assert starts and all(lo - 500 <= s and s + 1000 <= hi + 500 for s in starts)   # faces: 1 Hz
    taken = [(lo, hi)]
    assert find_segments(shots, 1000, ["close-up"], taken=taken) == []
    assert all(not describe(shots, s, s + 1000).has("dark") for s in find_segments(shots, 1000, [], avoid=["dark"]))


def test_analysis_is_cached_by_content(tour, tmp_path, monkeypatch):
    path, shots = tour
    import poc.perceive.shots as module
    monkeypatch.setattr(module, "_analyze_uncached", lambda *a, **k: pytest.fail("analysed twice"))
    again = analyze_shots(path, cache_dir=Path(path).parent / "cache")
    assert again == shots


def test_two_files_at_once_give_the_same_answers(tmp_path):
    """The server looks at two videos at a time; shared face detectors gave wrong sizes."""
    from concurrent.futures import ThreadPoolExecutor

    from poc.perceive.shots import _analyze_uncached
    import imageio_ffmpeg

    close = fx.still(tmp_path / "close.mp4", 700, seconds=6)
    medium = fx.still(tmp_path / "medium.mp4", 300, seconds=6)
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    alone = [_analyze_uncached(p, exe) for p in (close, medium)]
    with ThreadPoolExecutor(2) as pool:
        together = list(pool.map(lambda p: _analyze_uncached(p, exe), [close, medium]))
    assert together == alone
    assert describe(together[0], 0, 6000).scale == "close-up" and describe(together[1], 0, 6000).scale == "medium"
