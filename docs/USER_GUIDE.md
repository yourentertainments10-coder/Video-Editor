# User guide

The Offline Video Editor runs only on your Windows computer. Nothing is uploaded, and no
online AI service is used: the Director (the chat), footage analysis, titles, effects and
sound effects are all made on your PC.

## 1. Start the app

```bat
cd "D:\Python\video editor"
.venv\Scripts\python.exe -m poc.server
```

The browser opens by itself. If it doesn't, open the `http://127.0.0.1:...` address printed in
the terminal. Always use the `.venv` Python: the system Python does not have the
packages the editor needs.

After pulling new code, rebuild the screen once:
`cd ui && npm run build`. Then restart the server.

## 2. A first reel in one minute

1. **Create project**, then **＋ Add media**: add your videos and one song.
2. The Director looks at your footage (faces, close-ups, shaky or dark shots). The chat header
   shows *Watching…* while it does this. You can start before it finishes.
3. Type what you want in the chat, for example:
   - `20 second energetic reel, bold effects, title 'Goa 2026' with words popping in`
   - `calm 30 second video with soft fades, cinematic look`
   - `15 second reel, only close-ups, no dark shots, lower third 'Rahul Sharma'`
4. Check the settings card (song, length, mood, style, title), then press **Create edit**.
5. Play it in the preview. Change things in the chat or by hand (below).
6. **Export**: pick a format and render. The editor checks every export (length, sync,
   loudness, colour) and shows **✓ Render verified**, or the problem it found.

## 3. Changing the edit with the Director

Every change is shown first as a proposal. Press **Apply** to use it, or **Discard**. Every
applied change can be undone.

| What you want | Type |
|---|---|
| Length | `make it 15 seconds`, `shorten the intro by 1 second` |
| Clips | `remove clip 3`, `why clip 4?`, `replace clip 2 with a close-up` |
| Footage questions | `which clips have faces?`, `which clips are shaky?` |
| Pace | `faster`, `calmer` (makes a new auto-edit) |
| Transitions | `add zoom transitions`, `glitch transitions on every cut`, `no transitions`, `add fades` |
| Look | `cinematic look`, `vintage look`, `black and white`, `make clip 2 brighter` |
| Effects | `add film grain`, `add a vignette to clip 3`, `add a light leak to clip 2`, `flash on the beat drops`, `stabilize the shaky clips` |
| Style in one go | `make it look pro`, `bold effects`, `clean it up, plain cuts` |
| Title | `title 'Goa 2026'`, `title 'The end' at the end`, `remove the title` |
| Title animation | `make the title pop`, `typewriter title`, `neon title`, `title without animation` |
| Sound effects | `add sound effects`, `remove the sound effects` |
| Undo | `undo`, `redo` |

Hinglish works for common phrases too (`ek naya reel banao 20 second`, `effects lagao`,
`title hatao`).

## 4. Editing by hand

The editor sits next to the chat. Both work on the same timeline.

- **Timeline**: click a clip to select it, drag clips to move them, drop media from the bin.
  Tracks: Video, Overlay (titles), Voice & SFX, Music.
- **Inspector** (right side), for the selected clip:
  - transition into the clip and its length;
  - effects (add, remove, change their numbers);
  - audio gain, pan and fades;
  - for a title: its text, **Animation** (simple fade, pop, slide up, typewriter, words pop in,
    neon glow, lower third), **Colour** and vertical position.
- **Keyboard**

| Key | Does |
|---|---|
| Space | play / pause |
| ← / → | one frame back / forward (Shift: 1 second) |
| ↑ / ↓ | previous / next cut |
| Home / End | start / end |
| S | split the selected clip at the playhead |
| T | add a text title at the playhead |
| Delete | delete the selected clip |
| Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) | undo / redo (also right after picking from a dropdown) |
| + / − / \\ | zoom in / out / fit |
| Ctrl+S | save now (saving is also automatic) |

## 5. What the automatic styles add

- **Clean**: hard cuts on the beat, no effects.
- **Auto** (recommended): transitions on the strongest beats only, zoom punches on strong beats,
  slow camera moves on longer clips, a colour look, and a title animation picked for the mood.
- **Bold**: more of the same, plus a flash and a shake on the biggest drop, and whooshes and an
  impact as sound effects.

Shaky clips are stabilised and dark clips lifted a little, whatever the style. Sound effects are
placed on the Voice & SFX track, never on top of your own voice clips. They don't show in the
media bin.

## 6. Export formats

| Format | Size | Use for |
|---|---|---|
| Reels 9:16 | 1080×1920 | Instagram Reels, YouTube Shorts, WhatsApp status |
| YouTube 16:9 | 1920×1080 | YouTube, landscape |
| Square 1:1 | 1080×1080 | feed posts |
| Preview 540p | small, fast | checking the edit quickly |

Exports aim at −14 LUFS loudness (the level Instagram and YouTube use), with peaks limited to
−1 dBTP. They have a bitrate ceiling, so a 20 s reel is roughly 25–40 MB.

## 7. If something looks wrong

- **"⚠ Verification found issues"** after an export: the render finished, but a check failed.
  The message names the check (length, sync, loudness). Please report it with the project.
- **Phone videos (WhatsApp, etc.)** often skip frames. The editor fills those gaps with the
  last real frame, so cuts never go missing.
- **Tests fail with `No module named 'av'`**: run them with the project Python:
  `.venv\Scripts\python.exe -m pytest -q`.
- **The app looks old after an update**: run `npm run build` in `ui`, then restart the server.
