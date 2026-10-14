import { describe, expect, it } from 'vitest';
import { clipNumbers, planRevision, RevisionResponse } from '../src/timeline/revise';

const tl: any = { duration_ticks: 4000, time_base: { ticks_per_second: 1000 }, settings: { fps_num: 30, fps_den: 1 },
  assets: [], markers: [], clips: [
    { clip_id: 'b', track_id: 'video', start_tick: 2000, duration_ticks: 2000 },
    { clip_id: 't', track_id: 'overlay', start_tick: 0, duration_ticks: 1000 },
    { clip_id: 'a', track_id: 'video', start_tick: 0, duration_ticks: 2000 },
  ] };
const r = (over: Partial<RevisionResponse>): RevisionResponse => ({
  intent: 'remove', summary: [], questions: [], notes: [], answer: null, mood: null, timeline: null, operations: [], ...over });

describe('planRevision', () => {
  it('previews a proposed timeline with its explanation, never applies it', () => {
    const plan = planRevision(r({ timeline: tl, summary: ['Remove clip 2.'], notes: ['Later cuts stay on the beat.'] }));
    expect(plan).toEqual({ kind: 'preview', timeline: tl, lines: ['Remove clip 2.', 'Later cuts stay on the beat.'] });
  });
  it('turns undo and redo into the editor commands', () => {
    expect(planRevision(r({ intent: 'undo' }))).toEqual({ kind: 'undo' });
    expect(planRevision(r({ intent: 'redo' }))).toEqual({ kind: 'redo' });
  });
  it('offers a new auto-edit for pace changes', () => {
    expect(planRevision(r({ intent: 'retime', mood: 'calm', summary: ['Re-run'] }))).toEqual({ kind: 'autoEdit', mood: 'calm', lines: ['Re-run'] });
  });
  it('shows answers and questions instead of changing anything', () => {
    expect(planRevision(r({ intent: 'explain', answer: 'Clip 3 was picked…' }))).toEqual({ kind: 'info', lines: ['Clip 3 was picked…'], isQuestion: false });
    expect(planRevision(r({ intent: 'unknown', questions: ['Which clip?'], timeline: tl }))).toEqual({ kind: 'info', lines: ['Which clip?'], isQuestion: true });
  });
});

describe('clipNumbers', () => {
  it('numbers video clips in time order from 1 and skips other tracks', () => {
    const numbers = clipNumbers(tl);
    expect([...numbers.entries()]).toEqual([['a', 1], ['b', 2]]);
  });
});
