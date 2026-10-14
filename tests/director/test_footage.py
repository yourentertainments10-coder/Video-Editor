"""Phase 2: Director requests about what is in the footage."""
import pytest

from poc.director.footage import replace_sources, summarise_footage, tags_in
from poc.director.revise import apply_operations, parse_instructions, video_clips
from poc.perceive.shots import analyze_shots, describe, faces_available
from poc.timeline import (AudioProperties, EditorClip, EditorSettings, EditorTimeline, Layer, MediaAsset, TimeBase,
                          TimelineMarker, Track, validate_editor_timeline)
from tests import shot_fixtures as fx

needs_faces = pytest.mark.skipif(not faces_available(), reason="needs OpenCV 4.x (Haar face cascades)")


def test_words_become_tags():
    assert tags_in("remove the dark clips") == ["dark"]
    assert tags_in("only close-ups") == ["close-up"]
    assert tags_in("sirf closeups rakho") == ["close-up"]
    assert tags_in("which clips are shaky") == ["shaky"]
    assert tags_in("use more wide shots and no shaky clips") == ["wide", "shaky"]
    assert tags_in("shots without people") == ["no-faces"]
    assert tags_in("keep only shots with faces") == ["faces"]
    assert tags_in("andhere wale hatao") == ["dark"]
    # colour and mood words are not footage requests
    for text in ("make it darker", "make clip 2 brighter", "add fades", "calm reel", "smooth transitions",
                 "make it black and white", "remove clip 3"):
        assert tags_in(text) == [], text


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    folder = tmp_path_factory.mktemp("footage")
    path = fx.tour(folder / "tour.mp4")
    shots = analyze_shots(path, cache_dir=folder / "cache")
    asset = MediaAsset(asset_id="v1", kind="video", uri=str(path), duration_ms=20000)
    song = MediaAsset(asset_id="m", kind="audio", uri=str(folder / "song.mp3"), duration_ms=60000)
    clips = [EditorClip(clip_id=f"c{i}", asset_id="v1", track_id="video", start_tick=i * 1000, duration_ticks=1000,
                        source_in_ms=part * 4000 + 1500, source_out_ms=part * 4000 + 2500, score=0.5,
                        rationale={"quality": 0.8, "motion_match": 0.7, "reuse": 0.0, "coverage": 0.0})
             for i, part in enumerate(range(5))]          # one clip from each part of the tour
    clips.append(EditorClip(clip_id="music_clip", asset_id="m", track_id="music", start_tick=0, duration_ticks=5000,
                            source_in_ms=0, source_out_ms=5000, audio=AudioProperties()))
    tl = EditorTimeline(project_id="p", timeline_id="t", duration_ticks=5000, time_base=TimeBase(ticks_per_second=1000),
                        settings=EditorSettings(fps_num=30, fps_den=1, width=1080, height=1920), assets=[asset, song],
                        layers=[Layer(layer_id="visual", name="Visual", order=0), Layer(layer_id="audio", name="Audio", order=1)],
                        tracks=[Track(track_id="video", kind="video", layer_id="visual"),
                                Track(track_id="overlay", kind="overlay", layer_id="visual"),
                                Track(track_id="voice", kind="audio", layer_id="audio"),
                                Track(track_id="music", kind="music", layer_id="audio")],
                        clips=clips, markers=[TimelineMarker(marker_id=f"b{i}", kind="beat", position_tick=i * 500)
                                              for i in range(10)])
    return tl, {"v1": (asset, shots)}


def _ask(text, setup, pending=0):
    tl, footage = setup
    return parse_instructions(text, tl, footage, pending)


@needs_faces
def test_which_and_what(setup):
    assert _ask("which clips are dark?", setup).answer == "Clip 3 is a dark shot."
    assert _ask("which clips are shaky", setup).answer.startswith("Clip 4")
    assert "close-up" in _ask("what's in clip 2?", setup).answer
    assert "no faces" in _ask("clip 5 mein kya hai", setup).answer
    assert "It shows:" in _ask("why clip 1?", setup).answer


@needs_faces
def test_remove_dark_clips(setup):
    tl, footage = setup
    result = _ask("remove the dark clips", setup)
    assert result.operations[0].op == "remove_clips" and result.operations[0].clip_ids == ["c2"]
    new, _ = apply_operations(tl, result.operations, footage)
    assert [c.clip_id for c in video_clips(new)] == ["c0", "c1", "c3", "c4"]


@needs_faces
def test_only_close_ups_swaps_footage_and_keeps_timing(setup):
    tl, footage = setup
    result = _ask("only close-ups", setup)
    op = result.operations[0]
    assert op.op == "replace_source" and op.tags == ["close-up"] and "c1" not in op.clip_ids
    new, notes = apply_operations(tl, result.operations, footage)
    shots = footage["v1"][1]
    before = {c.clip_id: (c.start_tick, c.duration_ticks) for c in video_clips(tl)}
    for clip in video_clips(new):
        assert (clip.start_tick, clip.duration_ticks) == before[clip.clip_id]          # beat timing kept
    changed = [c for c in video_clips(new) if c.clip_id in op.clip_ids and c.source_in_ms != c.source_out_ms]
    swapped = [c for c in changed if describe(shots, c.source_in_ms, c.source_out_ms).scale == "close-up"]
    assert swapped                                    # at least some found close-up footage
    ranges = sorted((c.source_in_ms, c.source_out_ms) for c in video_clips(new))
    assert all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:]))                       # nothing used twice
    assert any("Found matching footage for" in n for n in notes)                       # 4 s of close-up: not all fit
    assert validate_editor_timeline(new) == []


@needs_faces
def test_replace_one_clip_with_a_close_up(setup):
    tl, footage = setup
    result = _ask("replace clip 5 with a close-up", setup)
    assert result.operations[0].clip_ids == ["c4"] and result.operations[0].tags == ["close-up"]
    new, _ = apply_operations(tl, result.operations, footage)
    clip = video_clips(new)[4]
    assert describe(footage["v1"][1], clip.source_in_ms, clip.source_out_ms).scale == "close-up"
    assert clip.rationale == {} and clip.score is None


@needs_faces
def test_no_shaky_shots_replaces_them(setup):
    result = _ask("no shaky shots", setup)
    assert result.operations[0].op == "replace_source" and result.operations[0].avoid == ["shaky"]
    assert result.operations[0].clip_ids == ["c3"]


def test_footage_not_seen_yet_is_a_question(setup):
    tl, _ = setup
    result = parse_instructions("remove the dark clips", tl, {}, pending=2)
    assert not result.operations and "still watching" in result.questions[0]
    assert parse_instructions("make it darker", tl, {}, pending=2).operations     # colour change needs no footage


@needs_faces
def test_file_summary(setup):
    labels = summarise_footage(setup[1]["v1"][1])
    assert labels and all(isinstance(x, str) for x in labels)


@needs_faces
def test_replace_never_uses_footage_twice(setup):
    tl, footage = setup
    new, replaced, missing = replace_sources(tl, ["c0", "c2", "c3", "c4"], ["close-up"], [], footage)
    ranges = sorted((c.source_in_ms, c.source_out_ms) for c in video_clips(new))
    assert all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:]))
    assert replaced + missing == 4 and missing >= 1
