import { describe, expect, it } from 'vitest';
import { editPoints, formatTimecode, nextEditPoint, playbackSource, stepFrames } from '../src/timeline/navigation';

const tl = (): any => ({
  duration_ticks: 6000,
  time_base: { ticks_per_second: 1000 },
  settings: { fps_num: 30, fps_den: 1, width: 1920, height: 1080 },
  assets: [{ asset_id: 'v1', kind: 'video', duration_ms: 9000 }, { asset_id: 'm', kind: 'audio', duration_ms: 60000 }],
  markers: [],
  clips: [
    { clip_id: 'a', asset_id: 'v1', track_id: 'video', start_tick: 0, duration_ticks: 2000, source_in_ms: 1000, source_out_ms: 3000 },
    { clip_id: 'b', asset_id: 'v1', track_id: 'video', start_tick: 3000, duration_ticks: 2000, source_in_ms: 5000, source_out_ms: 7000 },
    { clip_id: 'm', asset_id: 'm', track_id: 'music', start_tick: 0, duration_ticks: 6000, source_in_ms: 0, source_out_ms: 6000 },
  ],
});

describe('edit points', () => {
  it('lists 0, every clip edge and the end, sorted and unique', () => {
    expect(editPoints(tl())).toEqual([0, 2000, 3000, 5000, 6000]);
  });

  it('jumps to the next and previous edit point', () => {
    expect(nextEditPoint(tl(), 2500, 1)).toBe(3000);
    expect(nextEditPoint(tl(), 3000, 1)).toBe(5000);
    expect(nextEditPoint(tl(), 3000, -1)).toBe(2000);
    expect(nextEditPoint(tl(), 6000, 1)).toBe(6000);
    expect(nextEditPoint(tl(), 0, -1)).toBe(0);
  });
});

describe('stepFrames', () => {
  it('moves on the frame grid and stays inside the timeline', () => {
    expect(stepFrames(tl(), 1000, 1)).toBe(1033);
    expect(stepFrames(tl(), 1000, -30)).toBe(0);
    expect(stepFrames(tl(), 1000, -31)).toBe(0);
    expect(stepFrames(tl(), 5990, 5)).toBe(6000);
  });
});

describe('formatTimecode', () => {
  it('formats MM:SS:FF at the timeline frame rate', () => {
    expect(formatTimecode(tl(), 0)).toBe('00:00:00');
    expect(formatTimecode(tl(), 1500)).toBe('00:01:15');
    expect(formatTimecode(tl(), 61033)).toBe('01:01:01');
    expect(formatTimecode(tl(), 4500000)).toBe('75:00:00');
  });
});

describe('playbackSource', () => {
  it('finds the video clip and source time under the playhead', () => {
    expect(playbackSource(tl(), 'video', 500)).toEqual({ clip_id: 'a', asset_id: 'v1', source_ms: 1500, clip_end_tick: 2000 });
    expect(playbackSource(tl(), 'video', 3000)).toEqual({ clip_id: 'b', asset_id: 'v1', source_ms: 5000, clip_end_tick: 5000 });
    expect(playbackSource(tl(), 'music', 4000)).toEqual({ clip_id: 'm', asset_id: 'm', source_ms: 4000, clip_end_tick: 6000 });
  });

  it('returns null in a gap and at the clip end', () => {
    expect(playbackSource(tl(), 'video', 2500)).toBeNull();
    expect(playbackSource(tl(), 'video', 2000)).toBeNull();
  });
});
