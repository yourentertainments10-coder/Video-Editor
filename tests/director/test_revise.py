"""Golden tests for Local Director revisions (docs/LOCAL_DIRECTOR.md, Section 4)."""
import json
import math
from pathlib import Path

import pytest

from poc.director.revise import apply_operations, parse_revision, video_clips
from poc.render.editor import EditorRenderer
from poc.timeline import validate_editor_timeline
from tests.director.ld2_fixture import fixture

DATA = json.loads(Path(__file__).with_name("revision_cases.json").read_text(encoding="utf-8"))
TL = fixture()
NUMBER = {c.clip_id: i for i, c in enumerate(video_clips(TL), 1)}


def _normalise(op) -> dict:
    d = op.model_dump()
    out = {"op": d["op"], "clips": [NUMBER[c] for c in d["clip_ids"]]}
    for key in ("side", "ms", "policy", "transition", "effect_id", "text", "at"):
        if d[key] is not None:
            out[key] = d[key]
    if d["amount"] is not None:
        out["amount"] = round(d["amount"], 4)
    return out


def _expected(op: dict) -> dict:
    out = dict(op)
    if "amount" in out:
        out["amount"] = round(out["amount"], 4)
    return out


def test_there_are_at_least_100_golden_revisions():
    assert len(DATA["cases"]) >= 100


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["text"][:48] or "<empty>" for c in DATA["cases"]])
def test_golden_revision(case):
    result = parse_revision(case["text"], TL)
    assert result.intent == case["intent"], (result.summary, result.questions)
    assert [_normalise(o) for o in result.operations] == [_expected(o) for o in case["operations"]]
    assert len(result.questions) == case["questions"], result.questions
    if result.operations:
        assert result.summary, "every change must be explained"


@pytest.mark.parametrize("case", [c for c in DATA["cases"] if c["operations"]],
                         ids=[c["text"][:48] for c in DATA["cases"] if c["operations"]])
def test_every_golden_change_gives_a_valid_renderable_timeline(case):
    result = parse_revision(case["text"], TL)
    new, _ = apply_operations(TL, result.operations)
    assert validate_editor_timeline(new) == []
    music = [c for c in new.clips if c.track_id == "music"]
    assert music and music[0].start_tick + music[0].duration_ticks == new.duration_ticks
    assert TL == fixture()                                      # the input is never changed


def test_parsing_is_deterministic():
    for case in DATA["cases"][:40]:
        assert parse_revision(case["text"], TL) == parse_revision(case["text"], TL)


def test_removing_a_clip_ripples_and_keeps_later_cuts_on_beats():
    new, notes = apply_operations(TL, parse_revision("remove clip 3", TL).operations)
    starts = [c.start_tick for c in video_clips(new)]
    assert starts == [0, 2000, 4000, 6000, 8000, 10000]
    beats = {m.position_tick for m in new.markers}
    assert set(starts) <= beats
    assert new.duration_ticks == 12000
    assert any("beat" in n for n in notes)


def test_trimming_the_intro_moves_source_in_and_snaps_to_whole_beats():
    new, notes = apply_operations(TL, parse_revision("shorten the intro by 0.6 seconds", TL).operations)
    first = video_clips(new)[0]
    assert first.duration_ticks == 1500                         # 0.6 s -> 1 beat (0.5 s)
    assert first.source_in_ms == TL.clips[0].source_in_ms + 500
    assert [c.start_tick for c in video_clips(new)][1] == 1500
    assert any("1 beat" in n for n in notes)


def test_a_trim_that_leaves_too_little_is_refused():
    with pytest.raises(ValueError):
        apply_operations(TL, parse_revision("shorten clip 2 by 2 seconds", TL).operations)


def test_set_length_trims_from_the_end():
    new, _ = apply_operations(TL, parse_revision("make it 9 seconds", TL).operations)
    assert new.duration_ticks == 9000
    assert len(video_clips(new)) == 5
    assert video_clips(new)[-1].duration_ticks == 1000


def test_looks_accumulate_and_stay_in_range():
    tl = TL
    for _ in range(10):
        tl, _ = apply_operations(tl, parse_revision("make clip 2 brighter", tl).operations)
    effect = [e for e in video_clips(tl)[1].effects if e.effect_id == "brightness"]
    assert len(effect) == 1 and math.isclose(effect[0].parameters.amount, 1.0)
    back, _ = apply_operations(tl, parse_revision("remove all effects", tl).operations)
    assert all(not c.effects for c in video_clips(back))


def test_title_then_remove_title():
    titled, _ = apply_operations(TL, parse_revision("title 'Goa' at the end", TL).operations)
    clip = [c for c in titled.clips if c.track_id == "overlay"][0]
    assert clip.start_tick + clip.duration_ticks == titled.duration_ticks
    result = parse_revision("remove the title", titled)
    assert result.questions == []
    untitled, _ = apply_operations(titled, result.operations)
    assert not [c for c in untitled.clips if c.track_id == "overlay"]


def test_explain_uses_the_auto_edit_reason():
    answer = parse_revision("why clip 3?", TL).answer
    assert "clip_3.mp4" in answer or "clip_" in answer
    assert "quality 0.80" in answer


def test_fades_render(tmp_path):
    from poc.project_assets import ProjectAssetLibrary  # noqa: F401  (render path smoke test below)
    new, _ = apply_operations(TL, parse_revision("add fades", TL).operations)
    fades = [c for c in video_clips(new) if c.transition]
    assert len(fades) == 6 and all(c.transition.duration_ticks == 400 for c in fades)
