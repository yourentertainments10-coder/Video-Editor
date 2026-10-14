"""Phase 3: the finishing pass (transitions, beat accents, camera moves, looks) chosen from the music."""
from pathlib import Path

import pytest

from poc.director.style import (STYLE_EFFECTS, accent_cuts, clear_style, guess_mood, max_transition_ticks,
                                music_cuts, plan_style, set_transitions, video_clips)
from poc.timeline import (AudioProperties, EditorClip, EditorSettings, EditorTimeline, Layer, MediaAsset, TimeBase,
                          TimelineMarker, Track, validate_editor_timeline)

ROOT = Path(__file__).resolve().parents[2]


def timeline(n=16, clip_ms=500, beat_ms=500, energies=None, asset_ms=60000):
    assets = [MediaAsset(asset_id=f"v{i}", kind="video", uri=f"/clips/{i}.mp4", duration_ms=asset_ms) for i in (1, 2)]
    assets.append(MediaAsset(asset_id="m", kind="audio", uri="/song.mp3", duration_ms=60000))
    clips = [EditorClip(clip_id=f"c{k}", asset_id=f"v{k % 2 + 1}", track_id="video", start_tick=k * clip_ms,
                        duration_ticks=clip_ms, source_in_ms=1000 + k * 600, source_out_ms=1000 + k * 600 + clip_ms)
             for k in range(n)]
    clips.append(EditorClip(clip_id="music_clip", asset_id="m", track_id="music", start_tick=0, duration_ticks=n * clip_ms,
                            source_in_ms=0, source_out_ms=n * clip_ms, audio=AudioProperties()))
    beats = n * clip_ms // beat_ms
    energies = energies or [0.3 + 0.6 * (i >= beats // 2) for i in range(beats)]      # a drop half-way
    markers = [TimelineMarker(marker_id=f"b{i}", kind="beat", position_tick=i * beat_ms, value=energies[i],
                              label="downbeat" if i % 4 == 0 else None) for i in range(beats)]
    return EditorTimeline(project_id="p", timeline_id="t", duration_ticks=n * clip_ms,
                          time_base=TimeBase(ticks_per_second=1000),
                          settings=EditorSettings(fps_num=30, fps_den=1, width=1080, height=1920), assets=assets,
                          layers=[Layer(layer_id="visual", name="Visual", order=0), Layer(layer_id="audio", name="Audio", order=1)],
                          tracks=[Track(track_id="video", kind="video", layer_id="visual"),
                                  Track(track_id="overlay", kind="overlay", layer_id="visual"),
                                  Track(track_id="voice", kind="audio", layer_id="audio"),
                                  Track(track_id="music", kind="music", layer_id="audio")],
                          clips=clips, markers=markers)


def test_music_cuts_know_downbeats_and_the_drop():
    cuts = music_cuts(timeline())
    assert [c.index for c in cuts if c.downbeat] == [4, 8, 12]
    drop = next(c for c in cuts if c.index == 8)
    assert drop.strength >= 0.5 and drop.rise == max(c.rise for c in cuts)


def test_accents_are_strong_downbeats_spaced_apart():
    accents = accent_cuts(timeline(n=40), "energetic", "auto")
    assert accents and all(c.downbeat for c in accents)
    gaps = [b.tick - a.tick for a, b in zip(accents, accents[1:])]
    assert all(g >= 3500 for g in gaps)
    assert len(accent_cuts(timeline(n=40), "energetic", "bold")) >= len(accents)


def test_energetic_style_is_mostly_cuts_with_accents_punches_and_a_look():
    tl = timeline(n=40)
    styled, notes = plan_style(tl, mood="energetic", style="auto")
    clips = video_clips(styled)
    with_transition = [c for c in clips if c.transition]
    assert 1 <= len(with_transition) <= len(clips) // 4                      # accents, not every cut
    ids = [c.transition.transition_id for c in with_transition]
    assert all(a != b for a, b in zip(ids, ids[1:]))                         # never twice in a row
    assert all(c.transition.duration_ticks <= 300 for c in with_transition)
    assert any(e.effect_id == "zoom_punch" for c in clips for e in c.effects)
    assert any(e.effect_id == "flash" for c in clips for e in c.effects)
    assert all(any(e.effect_id == "look_punchy" for e in c.effects) for c in clips)
    assert validate_editor_timeline(styled) == []
    assert notes and "transition" in notes[0]
    assert {(c.start_tick, c.duration_ticks) for c in clips} == {(c.start_tick, c.duration_ticks) for c in video_clips(tl)}


def test_calm_style_uses_soft_transitions_and_slow_moves():
    tl = timeline(n=12, clip_ms=2000, beat_ms=500)
    styled, _ = plan_style(tl, mood="calm", style="auto")
    clips = video_clips(styled)
    ids = {c.transition.transition_id for c in clips if c.transition}
    assert ids and ids <= {"dissolve", "fade", "smooth_left", "circle_open", "fade_mono"}
    assert not any(e.effect_id in ("flash", "shake", "zoom_punch") for c in clips for e in c.effects)
    assert sum(any(e.effect_id in ("zoom_in", "pan_left", "pan_right") for e in c.effects) for c in clips) >= 6
    assert validate_editor_timeline(styled) == []


def test_clean_style_and_clearing():
    tl = timeline(n=40)
    styled, _ = plan_style(tl, mood="energetic", style="bold")
    clean = clear_style(styled)
    assert not any(c.transition for c in video_clips(clean))
    assert not any(e.effect_id in STYLE_EFFECTS for c in video_clips(clean) for e in c.effects)
    again, notes = plan_style(styled, mood="energetic", style="clean")
    assert notes == ["Clean edit: hard cuts, no effects."] and not any(c.effects for c in video_clips(again))


def test_restyling_replaces_instead_of_stacking():
    tl = timeline(n=40)
    once, _ = plan_style(tl, mood="energetic", style="auto")
    twice, _ = plan_style(once, mood="energetic", style="auto")
    assert once == twice


def test_transitions_need_long_enough_clips_and_spare_footage():
    tl = timeline(n=4, clip_ms=500)
    clips = video_clips(tl)
    assert max_transition_ticks(tl, clips[0], clips[1], 300) > 0
    tight = tl.model_copy(update={"clips": [c.model_copy(update={"source_in_ms": 0}) if c.clip_id == "c1" else c
                                            for c in tl.clips]})
    assert max_transition_ticks(tight, *video_clips(tight)[:2], 300) == 0     # no footage before clip 2
    styled, placed = set_transitions(tight, [1, 2, 3], ("zoom_through",), 300)
    assert video_clips(styled)[1].transition is None and placed == 2


def test_look_choice_and_mood_guess():
    tl = timeline(n=40)
    styled, notes = plan_style(tl, mood="energetic", style="auto", look="vintage")
    assert all(any(e.effect_id == "look_vintage" for e in c.effects) for c in video_clips(styled))
    assert "Vintage look" in notes[-1]
    assert guess_mood(tl) == "energetic" and guess_mood(timeline(n=6, clip_ms=2000)) == "calm"
    with pytest.raises(ValueError):
        plan_style(tl, mood="energetic", look="sunset")


def _sfx():
    from poc.render.sfx import SOUNDS
    return {name: MediaAsset(asset_id=f"sfx_{name}", kind="audio", uri=f"/sfx/{name}.wav", duration_ms=ms)
            for name, (ms, _) in SOUNDS.items()}


def test_sound_effects_follow_transitions_and_the_drop():
    tl = timeline(n=40)
    styled, notes = plan_style(tl, mood="energetic", style="bold", sfx=_sfx())
    sounds = sorted((c for c in styled.clips if c.clip_id.startswith("sfx_")), key=lambda c: c.start_tick)
    names = [c.clip_id.split("_", 2)[2] for c in sounds]
    assert "whoosh" in names and "impact" in names and "riser" in names
    flash_clip = next(c for c in video_clips(styled) if any(e.effect_id == "flash" for e in c.effects))
    impact = next(c for c in sounds if c.clip_id.endswith("impact"))
    assert impact.start_tick == flash_clip.start_tick
    for a, b in zip(sounds, sounds[1:]):
        assert a.start_tick + a.duration_ticks <= b.start_tick              # never on top of each other
    assert all(c.track_id == "voice" and c.audio.gain_db < 0 for c in sounds)
    assert {f"sfx_{n}" for n in names} <= {a.asset_id for a in styled.assets}
    assert validate_editor_timeline(styled) == []
    assert any("sound effect" in n for n in notes)
    clean = clear_style(styled)
    assert not any(c.clip_id.startswith("sfx_") for c in clean.clips)
    again, _ = plan_style(styled, mood="energetic", style="bold", sfx=_sfx())
    assert again == styled                                                     # re-styling does not stack


def test_sound_effects_leave_the_users_voice_clips_alone():
    tl = timeline(n=40)
    voice = EditorClip(clip_id="my_voice", asset_id="m", track_id="voice", start_tick=0, duration_ticks=20000,
                       source_in_ms=0, source_out_ms=20000)
    tl = tl.model_copy(update={"clips": tl.clips + [voice]})
    styled, _ = plan_style(tl, mood="energetic", style="bold", sfx=_sfx())
    assert not any(c.clip_id.startswith("sfx_") for c in styled.clips)
    assert any(c.clip_id == "my_voice" for c in styled.clips)


def test_calm_style_alternates_soft_transitions_and_light_leaks():
    styled, notes = plan_style(timeline(n=30, clip_ms=2000, beat_ms=500), mood="calm", style="auto")
    clips = video_clips(styled)
    assert any(c.transition for c in clips)
    assert any(e.effect_id == "light_leak" for c in clips for e in c.effects)
    assert any("light leak" in n for n in notes)
