# Local Director: Offline Brief and Revision Engine

Version 1.1, 2026-10-14. Status: **briefs (Section 3) implemented as LD-1;
revisions (Section 4) not yet.** Replaces the
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
