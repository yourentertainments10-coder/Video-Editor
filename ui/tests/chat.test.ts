import { describe, expect, it } from 'vitest';
import {
  briefReady, briefReply, briefRequest, ChatMessage, clipLinks, editReadyLine, forStorage, fromStorage,
  greeting, revisionReply, route, suggestions,
} from '../src/director/chat';

const asset = (asset_id: string, kind: string): any => ({ asset_id, kind, file_name: `${asset_id}.mp4` });
const assets = [asset('v1', 'video'), asset('v2', 'video'), asset('song', 'audio')];
const clip = (id: string, start: number): any => ({ clip_id: id, asset_id: 'v1', track_id: 'video', start_tick: start, duration_ticks: 1000 });
const tl = (n: number): any => ({
  duration_ticks: n * 1000, time_base: { ticks_per_second: 1000 }, settings: { fps_num: 30, fps_den: 1 },
  clips: Array.from({ length: n }, (_, i) => clip(`c${i}`, i * 1000)), assets: [],
});
const brief = (over: any = {}): any => ({
  brief: { duration_s: 20, aspect: '9:16', profile: 'reels_9x16', mood: 'energetic', transition_policy: 'soft',
    title: 'Goa', only_asset_ids: [], exclude_asset_ids: [], ...over.brief },
  understood: ['20 seconds', 'vertical 9:16'], defaults: [], questions: [], unknown_words: [],
  video_asset_ids: ['v1', 'v2'], ...over,
});

describe('route', () => {
  it('an empty timeline always reads a brief', () => {
    expect(route('add fades', tl(0))).toBe('brief');
    expect(route('anything', null)).toBe('brief');
  });
  it('with clips, changes go to revisions and new edits to briefs', () => {
    expect(route('add fades', tl(3))).toBe('revise');
    expect(route('make it 20 seconds', tl(3))).toBe('revise');
    expect(route('Make a 15 second reel', tl(3))).toBe('brief');
    expect(route('start over, calm and slow', tl(3))).toBe('brief');
    expect(route('ek naya reel banao 20 second', tl(3))).toBe('brief');
  });
});

describe('brief card', () => {
  it('fills every setting and is ready with a song and videos', () => {
    const m = briefReply(brief(), assets);
    expect(m.kind).toBe('brief');
    expect(m.brief).toEqual({ musicAssetId: 'song', mood: 'energetic', durationS: 20, videoAssetIds: ['v1', 'v2'],
      transitionPolicy: 'soft', title: 'Goa', profile: 'reels_9x16' });
    expect(m.lines).toEqual([]);                      // understood items are the settings table
    expect(briefReady(m.brief!)).toBe(true);
    expect(briefRequest(m.brief!)).toEqual({ musicAssetId: 'song', mood: 'energetic', durationS: 20,
      videoAssetIds: ['v1', 'v2'], transitionPolicy: 'soft', title: 'Goa' });
  });
  it('asks for a song when there is none', () => {
    const m = briefReply(brief(), [asset('v1', 'video')]);
    expect(briefReady(m.brief!)).toBe(false);
    expect(m.text).toMatch(/Add a song/);
  });
  it('keeps the earlier choice where the new brief says nothing', () => {
    const first = briefReply(brief(), assets).brief!;
    const next = briefReply(brief({ brief: { duration_s: null, mood: null, transition_policy: 'auto', title: null, profile: null } }),
      assets, { ...first, musicAssetId: 'song' });
    expect(next.brief).toMatchObject({ durationS: 20, mood: 'energetic', transitionPolicy: 'soft', title: 'Goa' });
    const asked = briefReply(brief({ brief: { duration_s: null, profile: null }, questions: ["How long should it be? e.g. '30 seconds'.", 'Which format: YouTube 16:9, Reels 9:16 or Square 1:1?'] }),
      assets, first);
    expect(asked.text).toBeUndefined();                // both answered by the earlier brief
    expect(briefReply(brief({ brief: { duration_s: null }, questions: ['How long should it be?'] }), assets).text).toMatch(/How long/);
  });
});

describe('revision replies', () => {
  it('a preview becomes a pending proposal and keeps its timeline out of the message', () => {
    const out = revisionReply({ kind: 'preview', timeline: tl(2), lines: ['Remove clip 3.'] });
    expect(out.timeline).not.toBeNull();
    expect(out.message).toMatchObject({ role: 'director', kind: 'proposal', status: 'pending', lines: ['Remove clip 3.'] });
    expect(JSON.stringify(out.message)).not.toContain('clips');
  });
  it('questions and answers are plain replies', () => {
    expect(revisionReply({ kind: 'info', lines: ['Which clip?'], isQuestion: true }).message?.kind).toBe('question');
    expect(revisionReply({ kind: 'info', lines: ['Clip 2 was picked…'], isQuestion: false }).message?.kind).toBe('text');
  });
});

describe('storage', () => {
  it('pending proposals expire and running jobs show as interrupted after a reload', () => {
    const messages: ChatMessage[] = [
      { id: 'a', role: 'director', at: 1, kind: 'proposal', status: 'pending' },
      { id: 'b', role: 'director', at: 2, kind: 'proposal', status: 'applied' },
      { id: 'c', role: 'director', at: 3, kind: 'job', job: { state: 'running' } },
    ];
    expect(forStorage(messages).map(m => m.status ?? m.job?.state)).toEqual(['expired', 'applied', 'interrupted']);
  });
  it('ignores anything that is not a message', () => {
    expect(fromStorage([null, 3, { id: 'x' }, { id: 'u', role: 'user', at: 1, kind: 'text', text: 'hi' }]))
      .toEqual([{ id: 'u', role: 'user', at: 1, kind: 'text', text: 'hi' }]);
    expect(fromStorage('nope')).toEqual([]);
  });
});

describe('clipLinks', () => {
  it('links clip numbers that exist and leaves the rest as text', () => {
    expect(clipLinks('Remove clips 2, 3 and 9; keep clip 1.', 5)).toEqual([
      { text: 'Remove clips ' }, { clip: 2, text: '2' }, { text: ', ' }, { clip: 3, text: '3' }, { text: ' and 9; keep clip ' },
      { clip: 1, text: '1' }, { text: '.' },
    ]);
    expect(clipLinks('no numbers here', 5)).toEqual([{ text: 'no numbers here' }]);
    expect(clipLinks('Clip 3 (song.mp3, from 0.93 s)', 5)[1]).toEqual({ clip: 3, text: '3' });
  });
});

describe('suggestions and greeting', () => {
  it('starts with briefs, then offers changes', () => {
    expect(suggestions(tl(0), assets)[0]).toMatch(/reel/);
    expect(suggestions(tl(4), assets)).toContain('Remove clip 3');
    expect(suggestions(tl(4), [asset('v1', 'video')])).toEqual([]);
  });
  it('tells a new user what to do first', () => {
    expect(greeting(tl(0), []).join(' ')).toMatch(/Add media/);
    expect(greeting(tl(0), assets).join(' ')).toMatch(/what video you want/);
    expect(greeting(tl(3), assets).join(' ')).toMatch(/undone/);
  });
  it('summarises a finished edit', () => {
    expect(editReadyLine(tl(3))).toMatch(/^Your edit is ready: 3 clips, 3\.0 s\./);
  });
});
