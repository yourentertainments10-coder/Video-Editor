# Evaluation Protocol

**Status: FROZEN.** This document fixes the rules of the MVP v0 evaluation
before any footage is collected and before any rating exists. Results are
judged against this document; this document is never adjusted to fit a
result. Any change after real data collection begins is recorded as a
dated addendum in Section 11, never a silent edit to Sections 2-9.

## 0. Frozen state

| | |
|---|---|
| Git commit | `90749aa6ee86a570e70a8f2ccb4d34160e641f9c` ("Step 9: evaluation harness") |
| Date frozen | 2026-09-06 |
| Footage collected | None yet |
| Ratings collected | None yet |

The codebase (`poc/decide`, `poc/perceive`, `poc/execute`, `poc/timeline`,
`poc/pipeline`) must remain at this exact commit for the entire first
evaluation pass. If a permitted iteration (Section 9, item 5) changes it,
the new commit is recorded in Section 11, and the dataset is regenerated
from scratch at that commit -- v0's own record (Section 10) is never
touched.

## 1. Hypothesis

**Primary (M2, the core MVP hypothesis — `docs/MVP.md` Section 10):**

> Human raters will prefer `cutter-v0`'s edit over `baseline-v0`'s edit —
> on identical footage, music, mood, duration, beat grid, candidate
> constraints, and renderer — in at least 70% of blind pairwise
> comparisons.

Four further claims are tested alongside it (`docs/MVP.md`'s "hypothesis
under test" table). They are secondary: none of them by itself validates
or invalidates M2, but they feed the decision rule in Section 8.

| # | Claim | Question put to raters |
|---|---|---|
| M1 | Output is mechanically sound | (no rating — computed automatically) |
| M3 | Output style is differentiable | "Which one was asked to be energetic?" |
| M4 | Output is usable (the honest gate) | "Would you post this after small manual tweaks?" |
| M5 | Cuts feel synchronized | "Do the cuts feel in time with the music?" (1-5) |

## 2. Dataset

Exactly `docs/MVP.md` Section 10's test set — nothing larger, nothing
smaller:

- **5 footage sets**: real phone footage, 8-15 clips each, 3-10 minutes
  total. Content varied across sets: people, landscape/travel,
  product/object, event, mixed. No stock footage — it is already curated
  and would inflate results.
- **3 music tracks**, different tempo and energy profile, each passing
  the beat-flash check (`experiments/beat_flash`).
- **2 moods**: energetic, calm.
- **5 × 3 × 2 = 30 evaluation pairs = 60 rendered outputs** (30
  `cutter-v0`, 30 `baseline-v0`).
- Duration: one fixed value in the 15-30 second range (`docs/MVP.md`
  Section 1), the same for every pair in the dataset.

Generate the whole dataset with one command, and record the exact
invocation (including both seeds) as part of the frozen record:

```bash
python -m poc eval generate \
  --footage-set set1=./footage/set1 --footage-set set2=./footage/set2 \
  --footage-set set3=./footage/set3 --footage-set set4=./footage/set4 \
  --footage-set set5=./footage/set5 \
  --music track1=./music/track1.mp3 --music track2=./music/track2.mp3 \
  --music track3=./music/track3.mp3 \
  --duration <15-30> --out ./eval_run_v0 --seed 0 --blind-seed 0
```

## 3. Raters

Target: **5 independent human raters** (`docs/MVP.md` Section 1: "a
rater sheet from at least five people over at least 30 outputs"), giving
30 × 5 = 150 total M2 judgments if every rater completes every pair.

Rules:
- No rater is told which system produced which output, or shown this
  document, before rating.
- No rater is a person who tuned the cutter's scoring weights.
- Partial completion is permitted; `eval report`'s per-rater breakdown
  and `validate_pairwise_ratings`'s completeness check both surface any
  gap explicitly rather than silently averaging over it.

## 4. Blinding

- A rater receives only the `dataset/` folder that `eval generate`
  produces: `pairNNN/A.mp4`, `pairNNN/B.mp4`, `instructions.md`, and a
  blank `rating_sheet_template.csv`. Nothing else.
- The A/B assignment is deterministic per `(pair_id, blind seed)`
  (`poc.evaluate.blind.assign_blind_labels`) and stored only in
  `<out>/answer_key.json` — a file that is never distributed to raters.
- No filename, timeline JSON, manifest, or piece of text anywhere under
  `dataset/` contains the word "cutter" or "baseline". This is verified
  by an automated test at the frozen commit
  (`tests/test_evaluate_integration.py::test_dataset_folder_contains_no_trace_of_generator_identity`),
  not asserted by hand each time.
- Analysis (Section 8) resolves each rating's `chosen_video` (A/B) back
  to cutter/baseline only after collection, via the private answer key.

## 5. Primary metric

**M2: pairwise preference rate.**

```
preference_rate = cutter_wins / total_judgments
```

computed by `poc.evaluate.metrics.compute_m2` over every valid,
deduplicated `PairwiseRating` ingested via `python -m poc eval ingest`.

## 6. Secondary metrics

Measured if the corresponding ratings are collected; each keeps
`docs/MVP.md`'s literal threshold, none invented or adjusted:

| Metric | Threshold |
|---|---|
| M1 (mechanical) | 100% of 60 outputs: validator clean, on-beat within 1 frame, render succeeds, wall time under 2 minutes |
| M3 (style differentiation) | ≥ 80% correct mood identification |
| M4 (usability) | "Yes" from ≥ 3 of 5 raters on ≥ 50% of outputs |
| M5 (perceived sync) | Mean rating ≥ 4.0 (of 5) |

M1 requires no separate rating step — `eval report` computes it directly
from the generated manifests.

## 7. Success threshold

**preference_rate ≥ 0.70**, computed once collection is declared final
(21 of 30 judgments per rater-equivalent; 105 of 150 at full 5-rater
scale). This exact number is not adjusted after seeing partial results,
and is not weakened to "close enough."

## 8. Decision rule

Reproduced verbatim from `docs/MVP.md` Section 10 by
`poc.evaluate.metrics.decide`:

- **M1 through M5 all pass** → hypothesis validated. Proceed to v0.2
  (LLM Director, natural-language brief).
- **M2 passes, M4 fails** → selection works but output is not good
  enough to ship. Add faces and downbeat-aligned motion peaks, re-run
  once. If M4 still fails, the product needs semantic understanding
  (VLM) before it is useful — a decision to make explicitly, not by
  default.
- **M2 in the 55-65% range** → the signals are too weak. Add faces and
  re-run once before concluding anything.
- **M2 at or near 50%** → selection adds nothing over random choice on
  this data.
- **M2 fails again after that one iteration with faces** → the
  cheap-signal hypothesis is false for this footage class. Stop and
  rethink perception before writing any more cutter code.

## 9. Analysis rules

1. No changes to `poc/decide`, `poc/perceive`, `poc/execute`,
   `poc/timeline`, or `poc/pipeline` between generating the dataset and
   computing the first M2 result. The codebase stays at the commit
   recorded in Section 0 (or Section 11, for a permitted iteration) for
   the whole pass it is being measured on.
2. Ingest ratings with `python -m poc eval ingest` as they arrive; never
   hand-edit `ratings.jsonl`.
3. The number that decides pass/fail is the one `eval report` computes
   once all 5 raters have rated all 30 pairs, or once collection is
   deliberately stopped and declared final — not an interim check taken
   mid-collection.
4. No submitted rating is discarded, re-rolled, or excluded by judgment.
   The only exclusions are the code-enforced ones: a duplicate
   `(pair_id, rater_id)` keeps the first submission; an unknown
   `pair_id` is rejected.
5. Exactly **two scoring iterations** are allowed after the first full
   evaluation (`docs/MVP.md`'s iteration budget), each being one change
   to signals or weights followed by a full re-run on all 30 pairs. More
   than two iterations without crossing 70% is itself a stop signal, not
   a reason to keep tuning.

## 10. Failure handling

If `preference_rate < 0.70` after the full pass (and after any iteration
permitted by Section 9, item 5):

1. **Record the result exactly as measured** — the full `eval report`
   output, the dataset's manifests, and `answer_key.json` — in a dated,
   committed file (e.g. `docs/results/2026-MM-DD_v0_result.md`). Never
   overwrite this record.
2. **Preserve the evaluated version**: tag the commit
   (`git tag mvp-v0-eval-result-1 <commit>`) so the exact code that was
   measured stays addressable forever, independent of later changes.
3. **Analyze failure patterns before changing anything** — which pairs,
   tracks, or moods lost, whether losses cluster, and what raters'
   optional reasons say — using the per-rater and per-(footage set,
   music, mood) breakdown `eval report` already prints.
4. **Create a new experiment/version** (v0.1, v0.2, ...) for any change
   motivated by that analysis, on a new branch or a clearly
   version-labeled commit — never an in-place edit of the frozen v0
   cutter.
5. **Never overwrite v0's results, dataset, or manifests.** They remain
   the permanent historical record of the first real test of this
   hypothesis, pass or fail.

## 11. Amendments log

### 2026-09-30: P0 evaluation tooling, before media collection

- State at amendment: no evaluation footage or ratings collected.
- The evaluation CLI now measures M1-M5, checks rating completeness and
  duplicates, and prevents development/preliminary runs from issuing the
  official MVP verdict. Official generation requires a clean evaluation
  source/protocol tree.
- M1 verifies generated timelines, source bounds and hashes, rendered
  streams/duration, render-time limits, and missing expected artifacts.
- Analysis caching and contact sheets were added outside the cutter's
  scoring/selection logic. `poc.decide` and the frozen thresholds, dataset
  size, rater count, and decision rules in Sections 2-9 are unchanged.
- Every manifest/report records the source commit, source fingerprint,
  analysis version/parameters, input hashes, and worktree state. The exact
  clean source commit used for the first official dataset must be recorded
  here before ratings begin; generate that dataset from that commit only.
- Preliminary data is diagnostic and does not replace or count as the
  official 5-set, 3-track, 30-pair evaluation.

Sections 0-9 remain unchanged. Append a new dated entry for any later
protocol amendment; never revise this record to fit observed results.
