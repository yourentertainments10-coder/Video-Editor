/**
 * The Director conversation (pure helpers; all understanding happens offline on the server).
 *
 * A message never carries a timeline: a proposed edit lives only in memory until the user
 * clicks Apply, and is shown as "expired" after a reload.
 */
import type { Asset } from '../api';
import type { AutoEditRequest } from '../timeline/AutoEditDialog';
import type { BriefResponse, Mood, TransitionPolicy } from '../timeline/brief';
import type { Timeline } from '../timeline/ops';
import type { RevisionAction } from '../timeline/revise';

export type ProposalStatus = 'pending' | 'applied' | 'discarded' | 'undone' | 'expired';
export type JobState = 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted';

export type BriefSettings = {
  musicAssetId: string | null; mood: Mood; durationS: number; videoAssetIds: string[];
  transitionPolicy: TransitionPolicy; title: string | null; profile: string | null;
  preferTags?: string[]; avoidTags?: string[];
  style?: 'clean' | 'auto' | 'bold'; look?: string | null; transitionFamily?: string | null;
};

/** Footage words the Director uses (tags from poc/perceive/shots.py). */
export const TAG_NAMES: Record<string, string> = {
  'close-up': 'close-ups', medium: 'medium shots', wide: 'wide shots', faces: 'shots with faces',
  'no-faces': 'shots without faces', group: 'group shots', dark: 'dark shots', bright: 'bright shots',
  shaky: 'shaky shots', steady: 'steady shots', busy: 'lots of movement', still: 'still shots',
};
export const tagNames = (tags: string[] = []) => tags.map(t => TAG_NAMES[t] ?? t).join(', ');

export type ChatMessage = {
  id: string;
  role: 'user' | 'director';
  at: number;
  kind: 'text' | 'question' | 'proposal' | 'brief' | 'job' | 'error' | 'autoEdit';
  text?: string;
  lines?: string[];
  status?: ProposalStatus;
  mood?: Mood;
  brief?: BriefSettings;
  job?: { state: JobState; detail?: string };
  /** The user's words this reply answers (to ask again when the timeline changed meanwhile). */
  ask?: string;
};

let counter = 0;
export function messageId(): string {
  counter += 1;
  return `m_${Date.now().toString(36)}_${counter.toString(36)}`;
}

export function userMessage(text: string, now = Date.now()): ChatMessage {
  return { id: messageId(), role: 'user', at: now, kind: 'text', text };
}

export function directorMessage(fields: Omit<ChatMessage, 'id' | 'role' | 'at'>, now = Date.now()): ChatMessage {
  return { id: messageId(), role: 'director', at: now, ...fields };
}

const videoClipCount = (timeline: Timeline | null) =>
  timeline ? timeline.clips.filter(clip => clip.track_id === 'video').length : 0;

const NEW_EDIT = /\b(?:make|create|build|cut|edit|bana(?:o|do)?)\s+(?:me\s+|us\s+)?(?:a|an|one|new|ek|naya|nayi|fresh)\b/;
const VIDEO_NOUN = /\b(?:reel|reels|video|edit|cut|short|shorts|montage|vlog|teaser|trailer|highlights?|clip)\b/;
const HINGLISH_NEW = /\b(?:naya|nayi|new)\b.*\bbana/;
const START_OVER = /\b(?:start over|from scratch|new edit|re-?edit everything|naya edit)\b/;

/**
 * Which part of the Director reads the message: a brief (build a new timeline) or a revision
 * (change the current one). An empty timeline always means a brief.
 */
export function route(text: string, timeline: Timeline | null): 'brief' | 'revise' {
  if (videoClipCount(timeline) === 0) return 'brief';
  const t = text.toLowerCase();
  if (START_OVER.test(t)) return 'brief';
  if ((NEW_EDIT.test(t) || HINGLISH_NEW.test(t)) && VIDEO_NOUN.test(t)) return 'brief';
  return 'revise';
}

export const DEFAULT_DURATION_S = 30;

/** Turn a parsed brief into the settings card the user confirms with one click. */
export function briefReply(response: BriefResponse, assets: Asset[], previous?: BriefSettings): ChatMessage {
  const b = response.brief;
  const songs = assets.filter(asset => asset.kind === 'audio' || asset.kind === 'music');
  const videos = assets.filter(asset => asset.kind === 'video');
  const settings: BriefSettings = {
    musicAssetId: previous?.musicAssetId && songs.some(s => s.asset_id === previous.musicAssetId)
      ? previous.musicAssetId : songs[0]?.asset_id ?? null,
    mood: b.mood ?? previous?.mood ?? 'energetic',
    durationS: b.duration_s ?? previous?.durationS ?? DEFAULT_DURATION_S,
    preferTags: b.prefer_tags?.length ? [...b.prefer_tags] : previous?.preferTags ?? [],
    style: b.style ?? previous?.style ?? 'auto',
    look: b.look ?? previous?.look ?? null,
    transitionFamily: b.transition_family ?? previous?.transitionFamily ?? null,
    avoidTags: b.avoid_tags?.length ? [...b.avoid_tags] : previous?.avoidTags ?? [],
    videoAssetIds: response.video_asset_ids.length ? [...response.video_asset_ids] : videos.map(v => v.asset_id),
    transitionPolicy: b.transition_policy !== 'auto' ? b.transition_policy : previous?.transitionPolicy ?? 'auto',
    title: b.title ?? previous?.title ?? null,
    profile: b.profile ?? previous?.profile ?? null,
  };
  // what was understood is shown as the settings table; the text only adds what is not in it
  const lines = [...response.defaults];
  if (response.unknown_words.length) lines.push(`I did not use: ${response.unknown_words.join(', ')}.`);
  // a value kept from the earlier brief is no longer an open question
  const questions = response.questions.filter(q =>
    !(b.duration_s == null && previous?.durationS && /how long/i.test(q))
    && !(b.profile == null && previous?.profile && /which format/i.test(q)));
  if (!videos.length) questions.push('Add some videos first (＋ Add media), then ask me again.');
  else if (!songs.length) questions.push('Add a song first (＋ Add media): I cut the video to the music\'s beat.');
  if (!response.video_asset_ids.length && (b.only_asset_ids.length || b.exclude_asset_ids.length)) {
    questions.push('That would leave no videos, so I kept all of them.');
  }
  return directorMessage({ kind: 'brief', lines, brief: settings, status: 'pending',
    text: questions.length ? questions.join(' ') : undefined });
}

/** True when the card has everything the auto-edit needs. */
export function briefReady(settings: BriefSettings): boolean {
  return !!settings.musicAssetId && settings.durationS > 0 && settings.videoAssetIds.length > 0;
}

export function briefRequest(settings: BriefSettings): AutoEditRequest {
  if (!settings.musicAssetId) throw new Error('No song chosen');
  return {
    musicAssetId: settings.musicAssetId, mood: settings.mood, durationS: settings.durationS,
    videoAssetIds: [...settings.videoAssetIds], transitionPolicy: settings.transitionPolicy, title: settings.title,
    style: settings.style ?? 'auto', look: settings.look ?? null, transitionFamily: settings.transitionFamily ?? null,
    ...(settings.preferTags?.length || settings.avoidTags?.length
      ? { shots: { prefer: [...(settings.preferTags ?? [])], avoid: [...(settings.avoidTags ?? [])] } } : {}),
  };
}

/** The Director's reply to a revision, and the proposed timeline (kept outside the message). */
export function revisionReply(action: RevisionAction): { message: ChatMessage | null; timeline: Timeline | null } {
  switch (action.kind) {
    case 'preview':
      return { message: directorMessage({ kind: 'proposal', lines: action.lines, status: 'pending' }), timeline: action.timeline };
    case 'autoEdit':
      return { message: directorMessage({ kind: 'autoEdit', lines: action.lines, mood: action.mood, status: 'pending' }), timeline: null };
    case 'info':
      return { message: directorMessage({ kind: action.isQuestion ? 'question' : 'text', lines: action.lines }), timeline: null };
    case 'undo':
      return { message: directorMessage({ kind: 'text', lines: ['Undone.'] }), timeline: null };
    case 'redo':
      return { message: directorMessage({ kind: 'text', lines: ['Redone.'] }), timeline: null };
  }
}

/** A summary of the timeline after an auto-edit finished. */
export function editReadyLine(timeline: Timeline): string {
  const clips = videoClipCount(timeline);
  const seconds = timeline.duration_ticks / timeline.time_base.ticks_per_second;
  return `Your edit is ready: ${clips} clip${clips === 1 ? '' : 's'}, ${seconds.toFixed(1)} s. ` +
    'Play it, then tell me what to change — or edit it yourself in the editor.';
}

/** What is saved: pending proposals and running jobs cannot survive a reload. */
export function forStorage(messages: ChatMessage[]): ChatMessage[] {
  return messages.map(message => {
    if (message.status === 'pending' && (message.kind === 'proposal' || message.kind === 'autoEdit')) {
      return { ...message, status: 'expired' };
    }
    if (message.kind === 'job' && message.job?.state === 'running') {
      return { ...message, job: { state: 'interrupted' } };
    }
    return message;
  });
}

/** Only well-formed messages are read back from disk. */
export function fromStorage(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  return forStorage(raw.filter((m): m is ChatMessage => !!m && typeof m === 'object'
    && typeof (m as ChatMessage).id === 'string'
    && ((m as ChatMessage).role === 'user' || (m as ChatMessage).role === 'director')
    && typeof (m as ChatMessage).kind === 'string'));
}

export type Segment = { text: string } | { clip: number; text: string };

/** Split text so "clip 3" / "clips 2, 4" become links that select the clip on the timeline. */
export function clipLinks(text: string, clipCount: number): Segment[] {
  const out: Segment[] = [];
  const push = (segment: Segment) => {
    const prev = out[out.length - 1];
    if (!('clip' in segment) && prev && !('clip' in prev)) prev.text += segment.text;
    else out.push(segment);
  };
  const pattern = /\b(clips?\s+|#)(\d+(?:(?:\s*,\s*|\s+and\s+|\s*&\s*|\s+to\s+|\s*-\s*)\d+)*)/gi;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > last) push({ text: text.slice(last, start) });
    push({ text: match[1] });
    let cursor = 0;
    for (const n of match[2].matchAll(/\d+/g)) {
      const at = n.index ?? 0;
      if (at > cursor) push({ text: match[2].slice(cursor, at) });
      const value = Number(n[0]);
      push(value >= 1 && value <= clipCount ? { clip: value, text: n[0] } : { text: n[0] });
      cursor = at + n[0].length;
    }
    last = start + match[0].length;
  }
  if (last < text.length) push({ text: text.slice(last) });
  return out.length ? out : [{ text }];
}

/** Starter ideas shown under the composer, depending on where the project is. */
export function suggestions(timeline: Timeline | null, assets: Asset[]): string[] {
  const hasVideo = assets.some(asset => asset.kind === 'video');
  const hasSong = assets.some(asset => asset.kind === 'audio' || asset.kind === 'music');
  if (!hasVideo || !hasSong) return [];
  const clips = videoClipCount(timeline);
  if (!clips) return ['Make a 30 second energetic reel', 'Calm 20 second video with soft fades', '15 second reel, title "My trip"'];
  return clips >= 3 ? ['Make it look pro', 'Add zoom transitions', 'Cinematic look', 'Which clips are close-ups?']
    : ['Add fades between clips', 'Make it black and white'];
}

/** Shown when a project's conversation is empty (never saved). */
export function greeting(timeline: Timeline | null, assets: Asset[]): string[] {
  const hasVideo = assets.some(asset => asset.kind === 'video');
  const hasSong = assets.some(asset => asset.kind === 'audio' || asset.kind === 'music');
  const lines = ['Hi, I\'m the Director. I work only on this computer — nothing is uploaded.'];
  if (!hasVideo || !hasSong) lines.push('Add your videos and a song with ＋ Add media, then tell me what video you want.');
  else if (!videoClipCount(timeline)) lines.push('Tell me what video you want — length, mood, format, transitions, a title — and I\'ll cut it to the beat of your song.');
  else lines.push('Tell me what to change, ask why I picked a clip, or edit it yourself in the editor. Every change can be undone.');
  return lines;
}
