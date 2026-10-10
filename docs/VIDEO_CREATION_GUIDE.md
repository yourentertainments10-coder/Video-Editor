# Video Creation Guide: What the Editor Needs Before It Starts

Version 1.0, 2026-10-10. Audience: users of the editor and developers
deciding what the input checks and the Local Director must ask for.

## 1. How a good automatic edit is actually made

An AI system "making a video" does not paint pixels from imagination here.
It follows the same loop a human editor does, written as code:

1. **Collect and inspect** every input: durations, resolution, rotation,
   frame rate, audio presence, what is in each shot.
2. **Plan** on paper first: length, aspect ratio, structure (hook → body →
   ending), pacing, where text goes.
3. **Build a timeline** (data), not a video. Every decision is a number that
   can be checked: start frame, source in-point, transition length.
4. **Validate** the timeline against hard rules (no gaps, cuts on beats,
   source ranges exist) before spending time rendering.
5. **Render a cheap preview**, look at it (contact sheet, frame checks), fix,
   and only then render the final.
6. **Verify the output file**: frame count, A/V length, loudness, playback.

This project already follows steps 1, 3, 4, 5 (contact sheet) and 6 (M1) for
the v0 pipeline. The plan step is the style table today and the Local
Director later (`LOCAL_DIRECTOR.md`).

## 2. Inputs to collect before creating a video

| Input | Required? | Why it matters | Default if missing |
|---|---|---|---|
| Footage (video clips) | Yes | The only picture source in v0 | — |
| Target duration | Yes | Sets the number of slots and music window | Ask |
| Aspect ratio / platform | Yes | 9:16 vs 16:9 changes framing and text placement | Ask |
| Music track | Yes (v0) | Defines the cut grid | — |
| Mood / style words | Recommended | Drives pacing and motion targets | `energetic` |
| Must-include / must-skip shots | Optional | Brand or story requirements | None |
| Text (title, CTA, captions) | Optional | Needs safe areas and fonts | None |
| Logo / brand colours / font | Optional | Consistent look | None |
| Screenshots / images | Optional (P2.6+) | Product and app demos | Not supported in v0 |
| Voice-over or script | Optional (P6) | Changes structure: picture follows speech | None |
| Usage rights for music and fonts | Yes before publishing | Avoid takedowns | Warn |

Rule from the architecture review: ask only for duration and aspect when
they are missing; for everything else, proceed with stated defaults and let
revisions do the clarifying.

## 3. What makes an edit easy or hard

| Easier | Harder |
|---|---|
| Many short, varied clips (8–15, 3–10 min total) | One long take, or 3 nearly identical clips |
| Steady, well-lit, sharp footage | Shaky, dark, or out-of-focus footage (scores all look bad) |
| Music with a clear, steady beat (pop, EDM, hip-hop) | Rubato, ambient, live recordings, tempo changes |
| Footage aspect = output aspect | 16:9 footage for a 9:16 reel (needs subject crop) |
| Clear brief: "20 s energetic reel" | Vague brief: "make it nice" |
| Visual subject obvious (one person, one product) | Content references: "the shot where she laughs" (needs semantic vision) |
| SDR, constant frame rate, standard codecs | HDR, VFR, rotated, 10-bit, odd audio layouts |
| No dialogue in footage | Dialogue that must not be cut mid-word (needs STT) |

The hard column is the backlog: subject crop (P2), HDR/VFR normalization
(`RENDER_SPEC.md`), faces (P1 decision rule), STT (P6), semantic vision (P7).

## 4. Pre-flight checklist (what the CLI/UI should check automatically)

- [ ] Every file decodes; video has a video stream; music has an audio stream.
- [ ] Clip count, per-clip length, and total footage meet the MVP limits
      (already enforced in v0).
- [ ] Music is at least duration + 10 s (already enforced).
- [ ] Detected BPM is plausible (60–200); warn about half/double tempo and
      suggest `--bpm-hint` (beat-flash check).
- [ ] Footage that is mostly black, blurred, or static is flagged before it
      wastes slots.
- [ ] Aspect mismatch between footage and target is reported with the fit
      mode that will be used.
- [ ] HDR, VFR, rotation detected and reported (RENDER_SPEC Section 2).
- [ ] Music/font licence noted for anything that will be published.

## 5. Quality bar for "done"

A finished edit should: start on a strong frame (no black, no blur), cut on
beats, not repeat the same shot back to back, keep text inside safe areas,
end on a phrase or a clean fade, play at the right aspect with no
letterbox surprises, and hit about −14 LUFS so it is not quieter than
everything else in the feed. Each of these is either already checked (M1,
contact sheet) or listed in `RENDER_SPEC.md` Section 8 to be checked.
