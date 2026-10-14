# Step U4.5 — Fix 3 render bugs (memory, transitions, silent audio) + playback

## U4 review result: accepted

I pulled `b1d14ea` and re-ran it:

- All 4 test files you were given match my versions exactly.
- 79 focused tests pass. The full suite shows 574 passed; the only failures are 7 that
  also fail on the old code here (the review machine has Linux with ffmpeg 7.0).
- Vitest 34/34 pass, and the typecheck is clean.
- The code matches the spec: profile settings, one encode, audio ∥ video, mux, timings,
  the `/audio` endpoint and the keyboard map.
- The real-footage numbers are good: YouTube 75.5 s → ~52 s, preview ~17 s.

While measuring where the remaining ~46 s "video" stage goes, I found **three real
bugs**. All three are reproducible on the current code.

### Bug 1 — memory grows with every clip (crash on long timelines)

The whole timeline is ONE ffmpeg process with one input per clip. Each input queues
1080p frames. I measured the peak memory:

| Clips | Peak memory |
|---|---|
| 10 | 1.6 GB |
| 20 | 2.6 GB |
| 30 | 3.9 GB |

That is about 125 MB per clip. With **55 clips, ffmpeg was killed for running out of
memory (8 GB)**. A 3-minute edit with ~300 clips would need ~35 GB. Your 46 s video
stage is most likely this (memory pressure, 55 decoders at once), not x264.

### Bug 2 — any fade or wipe AFTER an earlier cut fails to render

The `concat` output has a 1/1000000 timebase, and `xfade` refuses it:
`First input link main timebase (1/1000000) do not match ... (1/30)`.

- Timeline A–cut–B–fade–C → render fails.
- Only a transition between the first two clips worked, which is all the old tests
  covered.

### Bug 3 — clips without audio randomly make the audio track too short

The silence for a no-audio clip comes from a separate `-f lavfi anullsrc` input. With a
crossfade after a cut, the track came out 0.19–0.53 s instead of 0.9 s, at random. Verify
then fails with "sync drift". This hits images, screen recordings and silent clips.

**I verified the fixes below in a reference build on my side.** The new tests fail on
`b1d14ea` and pass with these fixes, and the rest of the suite is unchanged (583 passed).
With the fixes, the same 55-clip 1080p render finished at **2.6 GB peak**, verified OK.

### Rules (unchanged)

- Offline only; frozen v0 modules untouched.
- No skip or xfail. Do not edit the tests.
- Never commit while anything fails.
- Always run Python with `.venv\Scripts\python.exe`.
- `-protocol_whitelist file,pipe` on every ffmpeg call. Atomic writes. Deterministic.
- No hardware encoders.

---

## PART A — Render fixes (`transitions.py`, `audio.py`, `text.py`, `editor.py`)

1. **Timebase (bug 2).** In `build_sequence_graph`, end every `concat` with
   `settb=1/{fps}`, for both the cut join and the `fade_black` join. Example:
   `[a][b]concat=n=2:v=1:a=0,settb=1/30[seq3]`.
2. **Silent audio (bug 3).** In `render_audio_track`, delete the extra lavfi input.
   Generate the silence inside the filter graph:
   `anullsrc=r=48000:cl=stereo,atrim=duration=<clip seconds>,<the same filters>[aN]`.
   Fix the input index mapping to match.
3. **Chunked video (bug 1).** In the editor `video_job`:
   - Split the segments into chunks of **at most 8 segments**. Split ONLY at plain
     cuts: never inside a transition. If a run of transitions is longer than 8, that
     chunk may grow.
   - Encode each chunk with `render_sequence_to_file`, using the same crf/preset,
     `-an`, into `chunk_0000.mp4`, `chunk_0001.mp4` and so on.
   - Join the chunks with the concat demuxer and `-c copy`. Use `cwd=temp_dir` and
     relative names. Do not re-encode.
   - Run up to `max(1, min(3, os.cpu_count() // 4))` chunks in parallel. Cancel or
     failure stops all of them. Leave no files behind.
   - **Titles per chunk:** give each chunk its own `.ass` file. Shift the times by the
     chunk's start (in frames → ms) and clip them to the chunk.
4. **Title fade at chunk edges.** A title that crosses a chunk edge must NOT fade there.
   Add `fade_in_ms: int | None = None` and `fade_out_ms: int | None = None` to
   `TextOverlay`:
   - `None` means use `fade_ms`, so the existing text tests are unchanged;
   - `build_ass` writes `\fad(in,out)`;
   - the chunker sets the fade to 0 on the side that was cut by the chunk edge.

Copy the attached `test_render_u45.py` to `tests/test_render_u45.py` EXACTLY. It
contains 9 tests:

- at most 8 inputs per encode;
- every frame shows the right clip;
- a transition is never split;
- a title crossing a chunk edge stays continuous;
- determinism;
- transitions after a cut (3 kinds);
- exact silent audio length, 30 runs.

## PART B — UI fixes

1. **Preview is black while playing** (your `timeline_playback.png` shows a black
   preview with the playhead on a clip). The `<video key={clip_id}>` creates a NEW
   element for every clip, about every 0.5 s, so it is always still loading. The
   separate preload element doesn't help, because it is a different element.
   - Keep **one persistent `<video>` per video asset** used on the timeline (hidden,
     muted, `preload="auto"`) and show only the active one.
   - About 300 ms before a cut, seek the next clip's element to its `source_ms`. At the
     cut, switch visibility and `play()`.
   - Check: play across 10 cuts at normal speed. No black frames except real gaps.
2. **Export advice.** Write the pure function `ui/src/exportAdvice.ts` with
   `exportAdvice(assets, profile) -> Advice[]`. Show the results under the profile
   picker:
   - **Orientation:** your test footage is vertical (478×850) but was exported as
     YouTube 16:9. A cover crop keeps only ~32% of each frame. Suggest Reels 9:16, with
     a one-click "Switch" button.
   - **Low resolution:** "Clips are 478 px vs 1080 px export — they will look soft".

   Copy the attached `exportAdvice.test.ts` to `ui/tests/` EXACTLY.
3. **Render time colour.** The Export panel shows the render time in red, because it
   reuses the problem style `.job-result small`. Give it a neutral class.

## PART C — Hygiene

- Use the exact commit message below. The last one was "phase 4 of making ui".
- `reports/u4/` is missing `pytest_step.txt` and `pytest_full.txt`. This time, put ALL
  of these in `reports/u45/`:
  - `pytest_step.txt`
  - `pytest_full.txt`
  - the `npm test` output

## Run

```
cd ui && npm run typecheck && npm test && npm run build && cd ..
.venv\Scripts\python.exe -m pytest tests/test_render_u45.py tests/test_render_u4.py tests/test_text.py tests/test_transitions.py tests/test_audio.py tests/test_editor_render_p26.py tests/test_server_u4.py -v > pytest_step.txt 2>&1
.venv\Scripts\python.exe -m pytest -q --junitxml=pytest_report.xml > pytest_full.txt 2>&1
.venv\Scripts\python.exe tools/check_offline.py
```

## Manual check (real app)

1. **Speed and memory.** Render the same Energetic 30 s timeline (55 clips) as
   `youtube_1080p` twice. Report every stage time and the **peak memory** from Task
   Manager (the sum of the ffmpeg processes). Compare with U4: 52 s.
2. **Long timeline.** Auto-edit a **3-minute** Energetic edit, or loop the clips if the
   song is shorter, and render `preview_540p`. It must finish and verify. Report the
   time and the peak memory.
3. **Transitions.** Build a timeline with cut → fade → cut → wipe_left → dip_to_black.
   Render `youtube_1080p` and confirm it verifies.
4. **Playback.** Play across at least 10 cuts. Is there any black flash? Take a
   screenshot of the preview mid-playback, showing video.
5. **Advice.** Screenshot the Export panel showing the advice for the vertical
   WhatsApp clips.

## Commit

Commit only if everything is green, then push:
`git commit -m "fix(U4.5): chunked render with bounded memory, transition timebase, in-graph silence, persistent playback video"`

## Report

Put these in `reports/u45/`:

- `pytest_step.txt`, `pytest_full.txt`, `npm_test.txt`;
- `manual_results.json`;
- `manual_check.md`;
- the screenshots.

In chat, send only a short summary plus `git status` and `git log --oneline -5`.
