import { describe, expect, it } from 'vitest';
import {
  addClip, addTextClip, deleteClip, moveClip, OpError, snapToFrame, splitAt, trimEnd, trimStart,
} from '../src/timeline/ops';

// Minimal timeline shape used by the pure timeline operations.
const tl = (): any => ({
  duration_ticks: 1000,
  time_base: { ticks_per_second: 1000 },
  settings: { fps_num: 30, fps_den: 1, width: 1920, height: 1080 },
  assets: [],
  clips: [],
});
const video: any = { asset_id: 'v1', kind: 'video', duration_ms: 5000 };
const video2: any = { asset_id: 'v2', kind: 'video', duration_ms: 5000 };
const image: any = { asset_id: 'i1', kind: 'image', duration_ms: null };

describe('snapToFrame', () => {
  it('snaps millisecond ticks to the 30 fps frame grid', () => {
    expect(snapToFrame(tl(), 40)).toBe(33);
    expect(snapToFrame(tl(), 60)).toBe(67);
    expect(snapToFrame(tl(), 1000)).toBe(1000);
  });
});

describe('addClip', () => {
  it('adds a video clip with its full source range and grows the timeline', () => {
    const out = addClip(tl(), video, 'video', 0, 'c1');
    expect(out.clips).toHaveLength(1);
    expect(out.clips[0]).toMatchObject({
      clip_id: 'c1', asset_id: 'v1', track_id: 'video', start_tick: 0,
      duration_ticks: 5000, source_in_ms: 0, source_out_ms: 5000,
    });
    expect(out.duration_ticks).toBe(5000);
    expect(out.assets.map((a: any) => a.asset_id)).toContain('v1');
  });

  it('gives images a 3 s default and no source range', () => {
    const out = addClip(tl(), image, 'overlay', 1000, 'i');
    expect(out.clips[0]).toMatchObject({
      start_tick: 1000, duration_ticks: 3000, source_in_ms: null, source_out_ms: null,
    });
    expect(out.duration_ticks).toBe(4000);
  });

  it('never mutates its input', () => {
    const base = tl();
    addClip(base, video, 'video', 0, 'c1');
    expect(base.clips).toHaveLength(0);
    expect(base.duration_ticks).toBe(1000);
  });

  it('refuses to overlap another clip on the same track', () => {
    const one = addClip(tl(), video, 'video', 0, 'c1');
    expect(() => addClip(one, video2, 'video', 2000, 'c2')).toThrow(OpError);
  });
});

describe('moveClip', () => {
  it('moves on the frame grid and never before zero', () => {
    const t = addClip(tl(), video, 'video', 0, 'c1');
    expect(moveClip(t, 'c1', 1010).clips[0].start_tick).toBe(1000);
    expect(moveClip(t, 'c1', -500).clips[0].start_tick).toBe(0);
  });

  it('refuses a move that overlaps another clip on the same track', () => {
    const t = addClip(addClip(tl(), video, 'video', 0, 'c1'), video2, 'video', 5000, 'c2');
    expect(() => moveClip(t, 'c2', 4000)).toThrow(OpError);
  });
});

describe('trimming', () => {
  it('trimStart moves the start and source_in together', () => {
    const t = addClip(tl(), video, 'video', 0, 'c1');
    expect(trimStart(t, 'c1', 1000).clips[0]).toMatchObject({
      start_tick: 1000, duration_ticks: 4000, source_in_ms: 1000, source_out_ms: 5000,
    });
  });

  it('trimEnd cannot extend past the end of the source', () => {
    const t = trimEnd(addClip(tl(), video, 'video', 0, 'c1'), 'c1', 3000);
    expect(t.clips[0]).toMatchObject({ duration_ticks: 3000, source_out_ms: 3000 });
    expect(trimEnd(t, 'c1', 9000).clips[0]).toMatchObject({ duration_ticks: 5000, source_out_ms: 5000 });
  });

  it('never makes a clip shorter than one frame', () => {
    const t = addClip(tl(), video, 'video', 0, 'c1');
    expect(trimEnd(t, 'c1', 0).clips[0].duration_ticks).toBe(33);
  });
});

describe('splitAt and deleteClip', () => {
  it('splits one clip into two that cover the same source', () => {
    const t = addClip(tl(), video, 'video', 0, 'c1');
    const [a, b] = splitAt(t, 'c1', 2010, 'c2').clips;
    expect(a).toMatchObject({ clip_id: 'c1', start_tick: 0, duration_ticks: 2000, source_in_ms: 0, source_out_ms: 2000 });
    expect(b).toMatchObject({ clip_id: 'c2', start_tick: 2000, duration_ticks: 3000, source_in_ms: 2000, source_out_ms: 5000 });
    expect(b.transition ?? null).toBeNull();
  });

  it('refuses to split outside the clip', () => {
    const t = addClip(tl(), video, 'video', 0, 'c1');
    expect(() => splitAt(t, 'c1', 6000, 'c2')).toThrow(OpError);
  });

  it('deleteClip removes the clip and fits the duration to the last clip', () => {
    const t = addClip(addClip(tl(), video, 'video', 0, 'c1'), video2, 'video', 5000, 'c2');
    const out = deleteClip(t, 'c2');
    expect(out.clips.map((c: any) => c.clip_id)).toEqual(['c1']);
    expect(out.duration_ticks).toBe(5000);
  });
});

describe('addTextClip', () => {
  it('creates a text asset and a 3 s clip on the overlay track', () => {
    const out = addTextClip(tl(), 'नमस्ते', 500, 'txt1', 't1');
    expect(out.assets).toContainEqual(expect.objectContaining({ asset_id: 'txt1', kind: 'text', text: 'नमस्ते' }));
    expect(out.clips[0]).toMatchObject({ clip_id: 't1', asset_id: 'txt1', track_id: 'overlay', start_tick: 500, duration_ticks: 3000 });
  });
});