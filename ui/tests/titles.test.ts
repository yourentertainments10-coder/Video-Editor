import { describe, expect, it } from 'vitest';
import { briefReply, briefRequest } from '../src/director/chat';
import { TITLE_ANIMATION_NAMES } from '../src/timeline/AutoEditDialog';

const assets: any[] = [{ asset_id: 'v1', kind: 'video', file_name: 'v1.mp4' }, { asset_id: 'song', kind: 'audio', file_name: 's.mp3' }];
const brief = (over: any = {}): any => ({
  brief: { duration_s: 20, aspect: '9:16', profile: 'reels_9x16', mood: 'energetic', transition_policy: 'auto',
    title: 'Goa', only_asset_ids: [], exclude_asset_ids: [], ...over },
  understood: [], defaults: [], questions: [], unknown_words: [], video_asset_ids: ['v1'],
});

describe('title animations', () => {
  it('a brief that names one sends it with the auto-edit, and later briefs keep it', () => {
    const m = briefReply(brief({ title_animation: 'typewriter' }), assets);
    expect(m.brief?.titleAnimation).toBe('typewriter');
    expect(briefRequest(m.brief!).titleAnimation).toBe('typewriter');
    expect(briefReply(brief({ title_animation: null }), assets, m.brief).brief?.titleAnimation).toBe('typewriter');
  });
  it('is left to the mood when not asked for, and dropped without a title', () => {
    expect(briefRequest(briefReply(brief(), assets).brief!).titleAnimation).toBeUndefined();
    expect(briefRequest(briefReply(brief({ title: null, title_animation: 'pop' }), assets).brief!).titleAnimation).toBeUndefined();
  });
  it('names every animation the renderer draws', () => {
    expect(Object.keys(TITLE_ANIMATION_NAMES)).toEqual(['fade', 'pop', 'slide_up', 'typewriter', 'word_pop', 'glow', 'lower_third']);
  });
});
