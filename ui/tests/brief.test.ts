import { describe, expect, it } from 'vitest';
import { applyBrief, AutoEditForm, BriefResponse } from '../src/timeline/brief';

const form: AutoEditForm = { mood: 'energetic', durationS: 30, videoIds: ['v1', 'v2', 'v3'], transitionPolicy: 'auto', title: '' };
const response = (brief: Partial<BriefResponse['brief']>, extra: Partial<BriefResponse> = {}): BriefResponse => ({
  brief: { duration_s: null, aspect: null, profile: null, mood: null, transition_policy: 'auto', title: null,
           only_asset_ids: [], exclude_asset_ids: [], ...brief },
  understood: [], defaults: [], questions: [], unknown_words: [], video_asset_ids: ['v1', 'v2', 'v3'], ...extra,
});

describe('applyBrief', () => {
  it('fills every field the brief gives and names the export profile', () => {
    const out = applyBrief(form, response(
      { duration_s: 20, mood: 'calm', transition_policy: 'soft', title: 'Goa 2026', profile: 'reels_9x16', exclude_asset_ids: ['v2'] },
      { video_asset_ids: ['v1', 'v3'], understood: ['Length: 20 s'] }));
    expect(out.form).toEqual({ mood: 'calm', durationS: 20, videoIds: ['v1', 'v3'], transitionPolicy: 'soft', title: 'Goa 2026' });
    expect(out.profile).toBe('reels_9x16');
    expect(out.notes).toContain('Length: 20 s');
  });

  it('keeps the user values for anything the brief leaves out', () => {
    const custom = { ...form, mood: 'calm' as const, transitionPolicy: 'cuts_only' as const, title: 'Mine' };
    const out = applyBrief(custom, response({}, { questions: ['How long should it be?'] }));
    expect(out.form).toEqual(custom);
    expect(out.profile).toBeNull();
    expect(out.questions).toEqual(['How long should it be?']);
  });

  it('never empties the video selection', () => {
    const out = applyBrief(form, response({ only_asset_ids: ['x'] }, { video_asset_ids: [] }));
    expect(out.form.videoIds).toEqual(['v1', 'v2', 'v3']);
    expect(out.questions.at(-1)).toMatch(/no videos/);
  });

  it('lists words it did not use', () => {
    const out = applyBrief(form, response({}, { unknown_words: ['vibey', 'goa'] }));
    expect(out.notes).toContain('Not used: vibey, goa');
  });

  it('does not mutate its inputs', () => {
    const before = JSON.stringify(form);
    applyBrief(form, response({ duration_s: 5 }));
    expect(JSON.stringify(form)).toBe(before);
  });
});
