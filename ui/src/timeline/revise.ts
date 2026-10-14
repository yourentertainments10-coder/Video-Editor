/** Local Director revisions in the UI (pure helpers; parsing happens offline on the server). */
import { Timeline } from './ops';

export type RevisionResponse = {
  intent: string; summary: string[]; questions: string[]; notes: string[];
  answer: string | null; mood: 'energetic' | 'calm' | null; timeline: Timeline | null;
  operations: Array<Record<string, unknown>>;
};

export type RevisionAction =
  | { kind: 'preview'; timeline: Timeline; lines: string[] }
  | { kind: 'undo' } | { kind: 'redo' }
  | { kind: 'autoEdit'; mood: 'energetic' | 'calm'; lines: string[] }
  | { kind: 'info'; lines: string[]; isQuestion: boolean };

/** Decide what the UI does with a server response. Nothing is applied without a click. */
export function planRevision(response: RevisionResponse): RevisionAction {
  if (response.intent === 'undo' && !response.questions.length) return { kind: 'undo' };
  if (response.intent === 'redo' && !response.questions.length) return { kind: 'redo' };
  if (response.intent === 'retime' && response.mood) return { kind: 'autoEdit', mood: response.mood, lines: response.summary };
  if (response.timeline && !response.questions.length) {
    return { kind: 'preview', timeline: response.timeline, lines: [...response.summary, ...response.notes] };
  }
  if (response.answer) return { kind: 'info', lines: [response.answer], isQuestion: false };
  return { kind: 'info', lines: response.questions.length ? response.questions : response.summary, isQuestion: true };
}

/** Clip numbers as the Director uses them: video-track clips in time order, from 1. */
export function clipNumbers(timeline: Timeline): Map<string, number> {
  const video = timeline.clips.filter(clip => clip.track_id === 'video').sort((a, b) => a.start_tick - b.start_tick);
  return new Map(video.map((clip, index) => [clip.clip_id, index + 1]));
}
