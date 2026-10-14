"""Phase 3b: the Director understands title animations (brief, revisions, the style pass) and
which clips show faces."""
import pytest

from poc.director.apply import TITLE_ASSET_ID, apply_style
from poc.director.brief import parse_brief
from poc.director.footage import tags_in
from poc.director.revise import apply_operations, parse_revision
from poc.director.style import (clear_style, find_title_animation, pick_title_animation, plan_style,
                                set_title_animation, title_animation)
from tests.director.test_style import timeline


def _style(tl):
    return next(a for a in tl.assets if a.asset_id == TITLE_ASSET_ID).metadata.get("title_style")


@pytest.mark.parametrize("text, expected", [
    ("title 'goa' with typewriter effect", "typewriter"),
    ("make the title pop", "pop"),
    ("lower third", "lower_third"),
    ("words popping in for the title", "word_pop"),
    ("neon title", "glow"),
    ("title should slide up", "slide_up"),
    ("title without animation", "fade"),
    ("add a pop song", None),
    ("slide transitions and a title", None),
    ("make it pop", None),
    ("glow look. title 'x'", None),
])
def test_title_animation_words(text, expected):
    found = find_title_animation(text)
    assert (found[0] if found else None) == expected


def test_a_brief_asks_for_an_animated_title():
    r = parse_brief("20 second energetic reel, title 'Goa 2026' with typewriter effect", [])
    assert (r.brief.title, r.brief.title_animation, r.unknown_words) == ("Goa 2026", "typewriter", [])
    r = parse_brief("reel with lower third 'Rahul' and zoom transitions", [])
    assert (r.brief.title, r.brief.title_animation, r.brief.transition_family) == ("Rahul", "lower_third", "zoom")
    assert parse_brief("calm video, title 'Goa'", []).brief.title_animation is None


def test_revisions_add_or_change_the_animation():
    plain = timeline()
    titled = apply_style(plain, title="Goa trip")
    r = parse_revision("title 'Goa 2026' with typewriter animation", plain)
    tl, _ = apply_operations(plain, r.operations)
    assert _style(tl) == {"animation": "typewriter", "by": "user"}
    r = parse_revision("make the title pop", titled)
    assert [o.op for o in r.operations] == ["title_style"]
    tl, _ = apply_operations(titled, r.operations)
    assert _style(tl) == {"animation": "pop", "by": "user"}
    # a new text keeps the animation the user chose
    tl, _ = apply_operations(tl, parse_revision("title 'Goa 2027'", tl).operations)
    assert _style(tl)["animation"] == "pop"
    assert parse_revision("make the title pop", plain).questions[0].startswith("There is no title yet")
    assert parse_revision("make it pop", titled).intent == "style"        # the whole edit, not the title


def test_the_style_pass_picks_an_animation_for_the_mood_but_keeps_the_users():
    titled = apply_style(timeline(), title="Goa trip")
    tl, notes = plan_style(titled, mood="energetic", style="auto")
    assert _style(tl) == {"animation": "pop", "by": "auto"} and "Title with a pop." in notes
    tl, _ = plan_style(titled, mood="calm", style="bold")
    assert _style(tl)["animation"] == "glow"
    assert title_animation(clear_style(tl)) == ("fade", "auto")          # the auto choice is undone
    tl, _ = plan_style(titled, mood="calm", style="clean")
    assert title_animation(tl) == ("fade", "auto")
    mine = set_title_animation(titled, "lower_third")
    tl, _ = plan_style(mine, mood="energetic", style="bold")
    assert _style(tl) == {"animation": "lower_third", "by": "user"}
    tl, notes = plan_style(titled, mood="energetic", style="auto", title_animation_id="typewriter")
    assert _style(tl) == {"animation": "typewriter", "by": "user"}
    assert plan_style(timeline(), mood="energetic", style="auto")[0].assets == timeline().assets   # no title: nothing


def test_pick_title_animation():
    assert pick_title_animation("energetic", "auto", "Goa") == "pop"
    assert pick_title_animation("energetic", "auto", "Goa trip with friends") == "word_pop"
    assert pick_title_animation("calm", "auto", "Goa") == "slide_up"
    assert pick_title_animation("calm", "auto", "A long weekend in Goa") == "typewriter"
    assert pick_title_animation("calm", "bold", "Goa") == "glow"


def test_which_clips_have_faces():
    assert tags_in("which clips have faces?") == ["faces"]
    assert tags_in("which shots have no faces") == ["no-faces"]
    assert tags_in("clips that don't have people") == ["no-faces"]
    assert tags_in("which clips show people") == ["faces"]
