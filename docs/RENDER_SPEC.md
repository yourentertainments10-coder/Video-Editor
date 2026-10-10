# Render and Media Specification (P2.6 to P2.8)

Version 1.0, 2026-10-10. Status: **specification, not implemented.** Defines
what the mixed-media renderer must do before P2.6 is marked complete. All
processing is local FFmpeg (see `OFFLINE_POLICY.md`). The legacy v0.1
renderer (hard cuts, single track, music mux) remains the evaluation
renderer and must not change behaviour.

## 1. Interface

```python
class Renderer(Protocol):
    def compile(self, tl: EditorTimeline, profile: RenderProfile) -> RenderGraph: ...
    def render(self, graph: RenderGraph, *, out: Path,
               progress: Callable[[float, str], None],
               cancel: CancelToken) -> RenderResult: ...
```

- `compile` is pure: no I/O except reading the asset library. It rejects any
  effect or transition whose catalogue entry is `renderable: false`.
- `render` runs FFmpeg subprocesses only, in a private temp directory that
  is always removed (success, failure, or cancel).
- `RenderResult` carries the output path, ffprobe report, wall time, FFmpeg
  version, and per-segment cache hits.

## 2. Ingest normalization (every source, before analysis or render)

Phone and screen media are messy. Each item below is a real failure source.

| Problem | Detect (ffprobe) | Handle |
|---|---|---|
| Variable frame rate | `r_frame_rate` ≠ `avg_frame_rate`, or frame timestamps uneven | `fps=<timeline fps>` filter on every segment; analysis samples by timestamp (already in v0) |
| Rotation metadata | `side_data_list` display matrix / `rotate` tag | Let FFmpeg autorotate on decode; store the **displayed** width/height in the asset library; test with a 90° phone clip |
| HDR (HLG / PQ), 10-bit | `color_transfer` = `arib-std-b67` or `smpte2084`; `pix_fmt` 10-bit | Tone-map to SDR BT.709 (`zscale`+`tonemap` if the bundled build has zimg; otherwise mark asset "HDR, colours may look flat" and convert with `colorspace`). Check the bundled build's filters at startup |
| Odd dimensions, non-square pixels | width/height odd, `sample_aspect_ratio` ≠ 1:1 | Scale to even sizes, `setsar=1` |
| Interlaced | `field_order` not `progressive` | `bwdif` deinterlace |
| Missing/odd audio | no audio stream, mono, 44.1 kHz, 5.1 | Resample to 48 kHz stereo; treat missing audio as silence |
| Unicode / long Windows paths | path length > 240, non-ASCII | Pass paths as arguments (never through a shell), use `\\?\` prefix on Windows when needed; test both |
| Still images | image asset | `-loop 1 -t <dur> -framerate <fps>`; EXIF rotation applied; alpha PNG kept for overlays |

Proxies: one 720p CFR H.264 proxy per video asset for analysis and preview
(review item 17), cached by content hash.

## 3. Frame geometry and aspect ratio

| Fit mode | Use | FFmpeg |
|---|---|---|
| `cover` (default) | Fill frame, crop overflow | `scale=W:H:force_original_aspect_ratio=increase,crop=W:H:x:y` |
| `contain_blur` | Landscape clip in 9:16 without losing content | Blurred, scaled copy as background + `overlay` of the fitted clip |
| `subject_crop` | 9:16 from 16:9 footage (review item 31) | Static crop centred on YuNet face box, else OpenCV saliency centroid, else centre |

Safe areas for vertical output: keep text out of the top 14% and bottom 20%
(platform UI overlays). The validator warns when a text item falls there.

## 4. Catalogue → FFmpeg mapping

Each mapping becomes `renderable: true` only when its golden test passes
(Section 8).

| Catalogue ID | Implementation |
|---|---|
| `cut` | Concat demuxer, stream copy (no filter) |
| `fade`, `dissolve` | `xfade=transition=fade` / `dissolve` on a pre-rendered two-clip transition segment (review item 6). Verify `dissolve` output is identical across runs with the same `mask_seed`; if not, implement it as a seeded mask blend |
| `dip_to_black`, `dip_to_white` | `xfade=fadeblack` / `fadewhite` |
| `wipe_left/right/up/down` | `xfade=wipeleft` / `wiperight` / `wipeup` / `wipedown` |
| `fade_in`, `fade_out` | `fade=t=in/out:st:d` (plus matching `afade` on clip audio) |
| `zoom_in`, `zoom_out`, `pan_*` | Scale source to 2× output first, then animated `crop` with frame-indexed expressions (avoids `zoompan` jitter) |
| `brightness`, `contrast`, `saturation` | `eq` |
| `blur` | `gblur=sigma` |
| Text | ASS subtitles via `subtitles=` (libass) with bundled OFL fonts; fall back to `drawtext` with an explicit `fontfile` |

Transition timing: a transition of `d` frames overlaps the two clips by
`d`; the validator (P2.5) already caps it at 60% of the shorter clip. Total
timeline duration is unchanged because the overlap is taken from the clips'
handles, not from the timeline.

## 5. Audio

| Requirement | Method |
|---|---|
| No clicks at cuts | 5–10 ms `afade` in/out on every clip audio boundary |
| Music under footage/voice | `sidechaincompress` (ducking) when a voice or kept-audio track exists |
| Loudness for social | Final mix `loudnorm` two-pass to −14 LUFS integrated, −1 dBTP; record measured values in `RenderResult` |
| Exact length | `atrim`/`apad` so audio duration = video duration ± 1 audio frame |
| Music end | Fade over the last 1 s (v0 behaviour) or end on a detected phrase boundary when available |

## 6. Export profiles

| Profile | Size | fps | Video | Audio | Notes |
|---|---|---|---|---|---|
| `preview` | 540p or 720p | timeline | H.264 `ultrafast` CRF 28 | AAC 128k | For the editor player only |
| `vertical_1080` (Reels/Shorts/TikTok) | 1080×1920 | 30 | H.264 High, CRF 18–20, `yuv420p` | AAC 48 kHz 192k | `-movflags +faststart` |
| `landscape_1080` (YouTube) | 1920×1080 | 30 | same | same | |
| `square_1080` | 1080×1080 | 30 | same | same | |
| `master` | source | source | H.264 CRF 16 or ProRes (later) | PCM/AAC 320k | Archive |

Always tag BT.709 (`-colorspace bt709 -color_primaries bt709 -color_trc
bt709`, limited range) so players do not shift colours.

## 7. Jobs, progress, cancellation (P2.7)

- Progress: run FFmpeg with `-progress pipe:1 -nostats`; parse
  `out_time_us` against expected duration; report per stage
  (normalize → segments → transitions → assemble → mux → verify).
- Cancellation: a `CancelToken` checked between stages; on cancel, terminate
  the running process (then kill after 5 s), delete temp files, keep the
  project and timeline untouched.
- Errors: map FFmpeg stderr patterns to actionable messages (missing source,
  unsupported codec, disk full, invalid filter in this FFmpeg build).
- Crash safety: write the output to `name.partial.mp4`, rename only after
  verification passes.

## 8. Verification after every render

| Check | Tool | Fails when |
|---|---|---|
| Streams present | ffprobe | Missing video or (when expected) audio |
| Frame count | `ffprobe -count_frames` | ≠ expected frames |
| A/V length | ffprobe | Differ by > 1 video frame |
| Black/frozen frames at cuts | `blackdetect`, `freezedetect` | Unexpected black or frozen range at a boundary |
| Beat sync (legacy pipeline) | Existing M1 check | Cut > 1 frame from beat |

Golden tests: a small synthetic corpus (colour bars with burned frame
numbers, 1 kHz beeps on beats, a VFR clip, a rotated clip, an HDR clip, a
PNG, a 5.1 audio file) rendered through every renderable catalogue entry.
Compare frame numbers at boundaries, not file bytes (review item 26).

## 9. Determinism and caching

- Same timeline + same source hashes + same FFmpeg version ⇒ identical cut
  points, timing, and frame count. Byte identity is not promised.
- Segment cache key: SHA-256 of the canonical `SegmentSpec` JSON + FFmpeg
  version + profile. Cache lives beside the analysis cache and is evictable.

## 10. Licensing

The FFmpeg binary bundled by `imageio-ffmpeg` should be assumed to be a GPL
build (it includes libx264). That is fine for personal and internal use;
before distributing a desktop installer, record the exact build and its
licence obligations (P8), or switch to an LGPL build with a different H.264
encoder.
