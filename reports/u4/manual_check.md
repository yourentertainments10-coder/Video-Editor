# U4 manual check

Machine: Intel Core i5-12450H; CPU-only H.264 encoding. The five real phone video assets in the cloned local project probe as 478x850 H.264, approximately 30 fps. The energetic timeline is 29.633 seconds with 55 video clips and one music clip. I used a disposable copy of the project under `out/u4_manual`; the original project was left untouched.

## Render timing and verification

Each row is one render. Stage values are seconds (`prepare / audio / video / mux / verify / total`); full precision is in `manual_results.json`.

| Profile / run | Wall time | Stages | Verify |
|---|---:|---|---|
| youtube_1080p 1 | 52.15 s | 0.0004 / 7.373 / 46.417 / 1.143 / 2.375 / 49.967 | OK; 1920x1080 H.264, 30 fps, 48 kHz stereo, faststart; -14.1 LUFS, -1.4 dBTP |
| youtube_1080p 2 | 57.21 s | 0.0007 / 10.674 / 49.295 / 1.411 / 3.059 / 53.808 | OK; same measurements |
| preview_540p 1 | 17.20 s | 0.0003 / 11.872 / 12.262 / 1.038 / 0.637 / 13.949 | OK; 960x540 H.264, 30 fps, 48 kHz stereo, faststart; -14.1 LUFS, -1.1 dBTP |
| preview_540p 2 | 17.76 s | 0.0002 / 12.375 / 12.726 / 1.031 / 0.625 / 14.394 | OK; same measurements |

The YouTube profile used CRF 18 / medium / 192k audio; preview used CRF 28 / veryfast / 128k. Every verify report had `ok: true`, no problems, and no detected black ranges. The reported A/V drift was about -0.33 ms.

## Title and timeline playback

A title was added and rendered in one video encode. Render wall time was 53.93 seconds; verification passed at 1920x1080, and a sampled frame at 6 seconds contained 34,380 white title pixels.

In the local app, I used the mouse to seek and play through 24 sampled points from about 9.8 to 15.8 seconds, crossing multiple cuts and the inserted 10.267–10.767 second gap. Playback remained active at all 24 samples. The measured music drift stayed within 84 ms. I could not make a reliable by-ear assessment through this interface; the reported sync result is based on the logged audio and timeline clocks.

The keyboard shortcuts were exercised and the Shortcuts dialog was opened. A UI export completed and displayed “Render verified”, 30.8 seconds, preset `medium`. Screenshots: `timeline_playback.png`, `shortcuts.png`, and `export_timing.png`.
