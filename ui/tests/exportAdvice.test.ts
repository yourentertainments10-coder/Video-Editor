import { describe, expect, it } from 'vitest';
import { exportAdvice } from '../src/exportAdvice';

const v = (width: number, height: number) => ({ kind: 'video', width, height });
const youtube = { name: 'youtube_1080p', width: 1920, height: 1080 };
const reels = { name: 'reels_9x16', width: 1080, height: 1920 };

describe('exportAdvice', () => {
  it('suggests Reels when most clips are vertical and the export is landscape', () => {
    const advice = exportAdvice([v(478, 850), v(478, 850), v(1920, 1080)], youtube);
    expect(advice.map(a => a.code)).toContain('orientation');
    expect(advice.find(a => a.code === 'orientation')?.suggest).toBe('reels_9x16');
  });

  it('suggests YouTube when most clips are horizontal and the export is vertical', () => {
    const advice = exportAdvice([v(1920, 1080), v(1280, 720)], reels);
    expect(advice.find(a => a.code === 'orientation')?.suggest).toBe('youtube_1080p');
  });

  it('warns when the clips are much smaller than the export (upscaling)', () => {
    const advice = exportAdvice([v(478, 850), v(478, 850)], reels);
    const low = advice.find(a => a.code === 'low_resolution');
    expect(low).toBeDefined();
    expect(low?.message).toContain('478');
    expect(low?.message).toContain('1080');
  });

  it('is quiet when clips match the export', () => {
    expect(exportAdvice([v(1080, 1920), v(1080, 1920)], reels)).toEqual([]);
    expect(exportAdvice([v(3840, 2160)], youtube)).toEqual([]);
  });

  it('ignores non-video assets and an empty project', () => {
    expect(exportAdvice([], youtube)).toEqual([]);
    expect(exportAdvice([{ kind: 'audio', width: null, height: null }], youtube)).toEqual([]);
  });
});
