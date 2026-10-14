# Local Director: Offline Brief and Revision Engine

Version 1.2, 2026-10-14. Status: **briefs (Section 3) implemented as LD-1;
revisions (Section 4) implemented as LD-2.** Replaces the
cloud "Director" and "Reviser" model roles in `ARCHITECTURE.md` Sections 8,
11 and 14, in line with `OFFLINE_POLICY.md`. Implementation belongs to P4 in
`todo.md`.

## 1. Why this works without an LLM

The architecture already limits the Director to emitting structured data
(`CutterParams`, `EditPlan`, typed operations). The language space for short
editing instructions is small: a few dozen verbs ("faster", "shorten",
"remove", "swap", "softer"), a few reference types (time, clip number,
section, marker), and a few dozen style words. A deterministic parser with a
data-driven lexicon covers that space, is reproducible, costs nothing, and
can be tested exhaustively. Anything it cannot parse is answered with a
clear "I don't understand X; try one of: …" instead of a guess.

## Implementation status (LD-1)

- `poc/director/brief.py` (`parse_brief`) and `poc/director/lexicon.json` (the lexicon is
  JSON, not YAML, so no extra dependency is needed). `poc/director/apply.py` applies the
  brief's transitions and title to the auto-edit result, because the frozen v0 cutter only
  takes a mood and a length.
- Understood: length (digits, words, Hinglish numbers, `m:ss`, ranges become questions),
  format (reel/story/shorts/youtube/square/ratios), mood (with negation), transitions
  (cuts only / soft fades), title, and only/skip for videos named by file name. Unknown
  words and unsupported wishes (slow motion, colour looks, subtitles, voice-over) are
  reported back instead of being ignored.
- `POST /api/projects/{pid}/brief` returns the parsed brief and changes nothing; the
  auto-edit dialog fills its fields from it. `POST .../auto-edit` accepts
  `transition_policy` and `title`.
- Tests: `tests/director/brief_cases.json` (118 golden sentences) and
  `tests/director/test_*.py`.

## Implementation status (LD-2)

- `poc/director/revise.py`: `parse_revision(text, timeline)` -> intent + typed operations +
  plain summary; `apply_operations` returns a NEW timeline (ripple edits, beat-snapped trims,
  music and overlay fitted to the new length, fades that no longer fit are dropped).
- Intents: remove, trim, length, transition (all / one boundary), look (brightness,
  contrast, saturation, black and white, blur, clear), motion (zoom/pan), title (add,
  change, remove), retime ("faster"/"calmer" -> offer a new auto-edit), explain ("why clip
  3?" from the cutter's rationale), undo/redo. Speed changes are refused clearly (not
  renderable yet).
- References: clip numbers (shown as #1, #2 … on the timeline), ranges and lists,
  ordinals (also Hinglish), last / second-to-last, intro / ending, "the clip at 0:12",
  everything. Out-of-range or empty references become questions.
- `POST /api/projects/{pid}/revise {text, timeline}` returns a proposed timeline and
  saves nothing; the UI shows the summary and applies it through the normal edit path
  (autosave + undo).
- Tests: `tests/director/revision_cases.json` (113 golden instructions), each applied
  change validated, plus server and UI tests.

## Implementation status (chat)

- The Director is a conversation panel beside the editor (`ui/src/director/ChatPanel.tsx`,
  pure logic in `ui/src/director/chat.ts`). With an empty timeline, or a message like
  "make a 20 second reel", the message is read as a brief and answered with a settings card
  (song, length, mood, videos, transitions, title, format) and one "Create edit" button.
  Otherwise it is read as a revision: a proposed change with Apply / Discard, then Undo.
- Nothing changes the edit without a click. Proposed timelines live only in memory; after a
  reload an unapplied proposal shows as expired with "Ask again".
- "clip 3" in a reply is a link that selects the clip and moves the playhead to it.
- The conversation is saved per project in `chat.json` (`GET/PUT /api/projects/{pid}/chat`,
  last 300 messages). Narrow windows show Director / Editor tabs.

## Implementation status (footage, phase 2a)

- `poc/perceive/shots.py` looks at every video once, offline, with what ships inside OpenCV 4.x
  (no model download): faces (frontal + profile Haar cascades, 1 Hz on a 360-px frame), shot
  size from the biggest face (close-up >= 3.5 % of the frame, medium >= 0.6 %, else wide /
  no faces), brightness, camera shake (jitter of the phase-correlation shift, 4 Hz) and
  movement. Cached by file content in `analysis-cache/shots`. A 140 s phone video takes
  roughly 5-15 s; the server looks at two files at a time in the background after import
  (`GET /api/projects/{pid}/footage` reports progress and a few words per file).
- Director (`poc/director/footage.py`): "which clips are dark?", "what's in clip 3?",
  "remove the dark clips", "only close-ups" / "sirf close-ups rakho", "no shaky shots",
  "replace clip 4 with a close-up". Swaps keep each clip's place and length (so cuts stay on
  the beat) and never reuse footage already on the timeline. "why clip 3?" also says what the
  clip shows.
- Briefs: "mostly close-ups", "no dark shots", "action shots", "video without faces" ->
  `prefer_tags` / `avoid_tags`; the auto-edit swaps clips that do not fit after cutting.
- Limits: faces must be roughly 40 px or larger at 360 px wide (about 1/9 of the frame
  width); tiny faces in wide crowd shots count as "no faces". OpenCV 5 moved the cascades
  out, so the project pins `opencv-python-headless>=4.9,<5`; the self-check reports it.

## 2. Components

```
user text ─► Normalizer ─► Tokenizer ─► Intent matcher ─► Slot extractor
                                                   │             │
                                       style lexicon (YAML)   reference resolver
                                                   ▼             ▼
                                 CreativeBrief / List[Operation] (Pydantic, strict)
                                                   ▼
                              Validator ─► dry-run apply ─► diff text ─► user
```

| Component | Job | Implementation |
|---|---|---|
| Normalizer | Lowercase, unify numbers ("five" → 5, "0:23" → 23 s), strip filler | Pure Python |
| Intent matcher | Map the sentence to one intent from a closed set | Ordered regex/keyword rules from `director/intents.yaml`; first match wins; ties → ask |
| Slot extractor | Pull durations, times, clip indices, moods, aspect, transition names | Regex + lexicon lookups |
| Style lexicon | Map words like "cinematic", "energetic", "calm", "punchy", "dreamy" to numeric levers | `director/style_lexicon.yaml` (data, not code; review item 24) |
| Reference resolver | Turn "first five seconds", "third clip", "the ending", "at the drop" into IDs/ranges | Code over the current timeline and markers; never invents IDs |
| Explainer | Produce a plain diff ("Clips 1–3 now 2 beats each instead of 4") | Template strings from the applied operations |

## 3. Brief → CreativeBrief (generation)

Inputs required up front (asked only if missing; review item 20):
duration and aspect ratio. Everything else has a default that is stated in
the editor's notes.

| Brief text example | Extracted |
|---|---|
| "20 second energetic reel" | `duration_s=20`, `aspect=9:16` (reel), `mood=energetic` |
| "calm 30s landscape video, no transitions" | `duration_s=30`, `aspect=16:9`, `mood=calm`, `transition_policy=cuts_only` |
| "cinematic, slow start then fast" | lexicon `cinematic` levers + `intensity_arc=slow_build_then_fast` |
| "include IMG_0412, skip the blurry ones" | `must_include=[asset of IMG_0412]`, `min_sharpness_pct` raised |

Output feeds the existing v0 `CutterParams` unchanged (MVP.md Section 8), so
the cutter does not change when the Director arrives.

### Style lexicon example (`director/style_lexicon.yaml`)

```yaml
energetic: {beats_per_cut_high: 1, beats_per_cut_low: 2, motion_base: 0.5, motion_scale: 0.5}
calm:      {beats_per_cut_high: 2, beats_per_cut_low: 4, motion_base: 0.1, motion_scale: 0.3}
cinematic: {beats_per_cut_delta: +1, prefer_transition: dissolve, grade: warm_soft, letterbox: true}
punchy:    {beats_per_cut_delta: -1, transition_policy: cuts_only}
dreamy:    {prefer_transition: dissolve, speed: 0.85, grade: soft}
```

Unknown adjectives are listed back to the user ("I don't have a setting for
'vibey' yet; used 'energetic' defaults").

## 4. Revision → operations

Closed intent set, each mapped to the operation grammar (review item 9):

| Intent | Trigger examples | Operation(s) |
|---|---|---|
| `retime` | "faster", "slower", "more cuts in the chorus" | `retime_section` |
| `trim` | "shorten the intro", "make the end 2 seconds shorter" | `trim_clip` on resolved clips |
| `replace` | "swap the third clip", "use a sharper shot at 0:12" | `replace_clip` with constraints |
| `transition` | "softer transition", "remove the fade at the end" | `set_transition` / `remove_transition` |
| `speed` | "slow motion on clip 4" | `set_speed` (constant) |
| `grade` | "warmer", "more contrast" | `set_grade` / effect param change |
| `text` | "add title 'Goa 2026' at start" | `add_caption` |
| `lock` | "keep the opening", "I like clip 2" | `lock` |
| `undo` | "undo", "go back" | revert to parent version |
| `question` | "why this shot?" | read `rationale`, no change |

Rules: operations outside the resolved scope are rejected (ARCHITECTURE.md
14.2); if a reference matches zero or several targets, the engine shows the
candidates (thumbnails in the UI) instead of choosing; every applied change
is a new timeline version with undo.

## 5. Testing

- `tests/director/cases.yaml`: at least 100 brief and 100 revision sentences
  with the exact expected JSON. Golden tests; any lexicon edit must keep
  them passing or update them deliberately.
- Property tests: every produced operation validates; unknown IDs never
  appear; parsing is deterministic (same text → same output).
- Acceptance (from todo P4): "make the dashboard bigger", "shorten the
  intro", "use a softer transition" produce valid, reviewable, undoable
  changes.

## 6. Optional local LLM (later, off by default)

If golden tests show a class of phrasing that rules cannot cover, add a
`LocalLLMDirector` that runs a GGUF model through llama.cpp **in-process**,
constrained by a JSON grammar to the same schemas. It must:

- load only a registered, checksummed model file (OFFLINE_POLICY Section 4);
- fall back to the rule engine when absent, slow, or invalid;
- pass the same golden test file as the rule engine.

The rule engine stays the default and the reference behaviour.
