"""Golden tests for the Local Director brief parser (docs/LOCAL_DIRECTOR.md, Section 5).

tests/director/brief_cases.json lists sentences with the exact expected result. Any change
to poc/director/lexicon.json or brief.py must keep these passing or update them on purpose.
"""
import json
from pathlib import Path

import pytest

from poc.director import AssetRef, CreativeBrief, parse_brief, selected_video_ids
from poc.director.brief import lexicon

DATA = json.loads((Path(__file__).with_name("brief_cases.json")).read_text(encoding="utf-8"))
ASSETS = [AssetRef(asset_id=f"a{i}", file_name=name) for i, name in enumerate(DATA["assets"])]
BY_NAME = {a.file_name: a.asset_id for a in ASSETS}
PROFILES = lexicon()["profiles"]


def test_there_are_at_least_100_golden_briefs():
    assert len(DATA["cases"]) >= 100


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["text"][:48] or "<empty>" for c in DATA["cases"]])
def test_golden_brief(case):
    result = parse_brief(case["text"], ASSETS)
    exp = case["expect"]
    b = result.brief
    expected = CreativeBrief(
        duration_s=exp.get("duration_s"),
        aspect=exp.get("aspect"),
        profile=PROFILES[exp["aspect"]] if exp.get("aspect") else None,
        mood=exp.get("mood"),
        transition_policy=exp.get("transition_policy", "auto"),
        title=exp.get("title"),
        only_asset_ids=[BY_NAME[n] for n in exp.get("only", [])],
        exclude_asset_ids=[BY_NAME[n] for n in exp.get("exclude", [])],
        prefer_tags=exp.get("prefer_tags", []),
        avoid_tags=exp.get("avoid_tags", []),
    )
    assert b == expected, (result.understood, result.questions)
    assert len(result.questions) == case["questions"], result.questions
    assert result.unknown_words == case["unknown_words"]


@pytest.mark.parametrize("case", DATA["cases"][:30])
def test_parsing_is_deterministic(case):
    assert parse_brief(case["text"], ASSETS) == parse_brief(case["text"], ASSETS)


def test_ids_never_invented():
    for case in DATA["cases"]:
        b = parse_brief(case["text"], ASSETS).brief
        assert set(b.only_asset_ids + b.exclude_asset_ids) <= set(BY_NAME.values())


def test_every_understood_value_is_explained():
    result = parse_brief("Make a 20 second energetic reel, no transitions, title 'Goa 2026', skip beach", ASSETS)
    text = " | ".join(result.understood)
    for fragment in ("Length: 20 s", "Format: Reels 9:16", "Mood: energetic", "Transitions: cuts only",
                     'Title: "Goa 2026"', "Leaving out: beach.mp4"):
        assert fragment in text


def test_default_mood_is_reported():
    result = parse_brief("30 second reel", ASSETS)
    assert result.brief.mood == "energetic"
    assert any("Mood not given" in d for d in result.defaults)


def test_selected_videos_follow_only_and_skip():
    only = parse_brief("30s reel, only beach and only drone_01", ASSETS).brief
    assert selected_video_ids(only, ASSETS) == [BY_NAME["beach.mp4"], BY_NAME["drone_01.mp4"]]
    skip = parse_brief("30s reel, skip beach", ASSETS).brief
    assert BY_NAME["beach.mp4"] not in selected_video_ids(skip, ASSETS)
    assert len(selected_video_ids(skip, ASSETS)) == len(ASSETS) - 1
    assert selected_video_ids(CreativeBrief(), ASSETS) == [a.asset_id for a in ASSETS]


def test_lexicon_has_no_word_in_two_moods():
    moods = lexicon()["mood"]
    assert not set(moods["energetic"]) & set(moods["calm"])
