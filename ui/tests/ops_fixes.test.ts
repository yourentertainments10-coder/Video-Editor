import { describe, expect, it } from 'vitest';
import { addClip, splitAt, trimStart } from '../src/timeline/ops';

const tl = (): any => ({
  duration_ticks: 1000,
  time_base: { ticks_per_second: 1000 },
  settings: { fps_num: 30, fps_den: 1, width: 1920, height: 1080 },
  assets: [],
  clips: [],
});
const video: any = { asset_id: 'v1', kind: 'video', duration_ms: 5000 };

describe('U2 fixes', () => {
  it('trimStart never pulls source_in_ms below zero', () => {
    const t = addClip(tl(), video, 'video', 1000, 'c1');
    expect(trimStart(t, 'c1', 500).clips[0]).toMatchObject({
      start_tick: 1000, duration_ticks: 5000, source_in_ms: 0, source_out_ms: 5000,
    });
  });

  it('trimStart can extend left only as far as the unused source', () => {
    const t = trimStart(addClip(tl(), video, 'video', 1000, 'c1'), 'c1', 2000);
    expect(trimStart(t, 'c1', 0).clips[0]).toMatchObject({
      start_tick: 1000, duration_ticks: 5000, source_in_ms: 0, source_out_ms: 5000,
    });
  });

  it('splitAt keeps the fade-in on the left half and the fade-out on the right half', () => {
    const base = addClip(tl(), video, 'video', 0, 'c1');
    const withAudio: any = {
      ...base,
      clips: [{ ...base.clips[0], audio: { gain_db: -3, pan: 0, muted: false, fade_in_ms: 300, fade_out_ms: 400 } }],
    };
    const [a, b] = splitAt(withAudio, 'c1', 2000, 'c2').clips;
    expect(a.audio).toEqual({ gain_db: -3, pan: 0, muted: false, fade_in_ms: 300, fade_out_ms: 0 });
    expect(b.audio).toEqual({ gain_db: -3, pan: 0, muted: false, fade_in_ms: 0, fade_out_ms: 400 });
  });
});
