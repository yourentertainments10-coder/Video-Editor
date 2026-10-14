# Projects looked at for phase 3 (October 2026)

What was taken from each, and why. No code was copied from any of them: every transition and
effect in this project is an FFmpeg filter written here, so no third-party licence applies to
the shipped code. Licences were read from each repository's LICENSE file.

| Repository | Licence | Used for |
|---|---|---|
| gl-transitions/gl-transitions | MIT (a few BSD) | The set of transition ideas. The GLSL shaders need a GPU pipeline; FFmpeg's `xfade` already has most of the same families, and `xfade` custom expressions were measured too slow on CPU (a 1 s ripple at 1080x1920 took ~12 s), so none were ported yet. |
| mcw67/openreel | MIT | Its transition preset list (dissolve, dip to black/white, wipes, slides, push, zoom, iris, blur) confirmed which families editors expect; all are covered by `xfade`. |
| S07K/cutaway | MIT | The idea of an effect registry where every effect has a key and a typed parameter schema (this project already had one: `EFFECT_CATALOGUE`). |
| Swaggerx3/EffectCraft | MIT / Apache-2.0 | Rust compositing engine; nothing directly reusable from Python, kept as a reference for looks. |
| u174458/CutWire-Studios-Drift | GPL-3.0 | Ideas only (beat shake, beat drop, flash, cinematic grade, glitch cut). Its code is GPL and was not copied, so this project stays under its own licence. |
| LeonSooLab/openchatcut | AGPL-3.0 | Ideas only (chat-driven editing, asset library). Code not copied (AGPL would apply to this whole project). |
| ElSalvatore-sys/video-editor-plugin | none stated | Not used (no licence means no permission to copy). |
| beast-ofcourse/awesome-motion-graphics | MIT (the list) | A list of external resources; each linked asset has its own licence. None bundled. |

Not used: Remotion (needs a bundled Chromium and Node at render time, and its licence is not free
for every company size); downloaded music/SFX libraries (each asset needs its own licence record).
