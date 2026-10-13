export type TimelineAsset = {
  asset_id: string; kind: string; duration_ms?: number | null; text?: string;
  [key: string]: unknown;
};
export type TimelineClip = {
  clip_id: string; asset_id: string; track_id: string; start_tick: number;
  duration_ticks: number; source_in_ms: number | null; source_out_ms: number | null;
  transition?: { transition_id: string; duration_ticks: number; [key: string]: unknown } | null;
  effects?: Array<{ effect_id: string; parameters: Record<string, unknown> }>;
  audio?: { gain_db: number; pan: number; muted: boolean; fade_in_ms: number; fade_out_ms: number };
  transform?: Record<string, unknown>;
  [key: string]: unknown;
};
export type Timeline = {
  duration_ticks: number; time_base: { ticks_per_second: number };
  settings: { fps_num: number; fps_den: number; width: number; height: number };
  assets: TimelineAsset[]; clips: TimelineClip[]; [key: string]: unknown;
};

export class OpError extends Error {
  constructor(message: string) { super(message); this.name = 'OpError'; }
}

const clone = <T,>(value: T): T => structuredClone(value);
const frameTicks = (tl: Timeline) => tl.time_base.ticks_per_second * tl.settings.fps_den / tl.settings.fps_num;
const ticksToMs = (tl: Timeline, ticks: number) => ticks * 1000 / tl.time_base.ticks_per_second;
const msToTicks = (tl: Timeline, ms: number) => ms * tl.time_base.ticks_per_second / 1000;

export function snapToFrame(tl: Timeline, tick: number): number {
  const frame = frameTicks(tl);
  return Math.round(Math.round(tick / frame) * frame);
}

function assetFor(tl: Timeline, clip: TimelineClip): TimelineAsset {
  const asset = tl.assets.find(item => item.asset_id === clip.asset_id);
  if (!asset) throw new OpError(`Unknown asset '${clip.asset_id}'`);
  return asset;
}
function clipFor(tl: Timeline, clipId: string): TimelineClip {
  const clip = tl.clips.find(item => item.clip_id === clipId);
  if (!clip) throw new OpError(`Unknown clip '${clipId}'`);
  return clip;
}
function assertNoOverlap(clips: TimelineClip[], candidate: TimelineClip, ignoredId?: string): void {
  const end = candidate.start_tick + candidate.duration_ticks;
  if (clips.some(item => item.clip_id !== ignoredId && item.track_id === candidate.track_id
    && candidate.start_tick < item.start_tick + item.duration_ticks
    && end > item.start_tick)) {
    throw new OpError(`Clip '${candidate.clip_id}' overlaps another clip on track '${candidate.track_id}'`);
  }
}
function withClip(tl: Timeline, changed: TimelineClip): Timeline {
  const clips = tl.clips.map(item => item.clip_id === changed.clip_id ? changed : item);
  return { ...tl, clips, duration_ticks: Math.max(tl.duration_ticks, changed.start_tick + changed.duration_ticks) };
}

export function addClip(tl: Timeline, asset: TimelineAsset, trackId: string, startTick: number, newClipId: string): Timeline {
  if (tl.clips.some(item => item.clip_id === newClipId)) throw new OpError(`Clip id '${newClipId}' already exists`);
  const duration = asset.kind === 'image' || asset.kind === 'text' ? 3000 : Number(asset.duration_ms ?? 0);
  if (!Number.isFinite(duration) || duration <= 0) throw new OpError(`Asset '${asset.asset_id}' has no duration`);
  const start = Math.max(0, snapToFrame(tl, startTick));
  const clip: TimelineClip = {
    clip_id: newClipId, asset_id: asset.asset_id, track_id: trackId, start_tick: start,
    duration_ticks: Math.max(1, snapToFrame(tl, msToTicks(tl, duration))),
    source_in_ms: asset.kind === 'image' || asset.kind === 'text' ? null : 0,
    source_out_ms: asset.kind === 'image' || asset.kind === 'text' ? null : duration,
  };
  assertNoOverlap(tl.clips, clip);
  const assets = tl.assets.some(item => item.asset_id === asset.asset_id) ? tl.assets : [...tl.assets, clone(asset)];
  return { ...tl, assets, clips: [...tl.clips.map(clone), clip], duration_ticks: Math.max(tl.duration_ticks, start + clip.duration_ticks) };
}

export function addTextClip(tl: Timeline, text: string, startTick: number, newAssetId: string, newClipId: string): Timeline {
  if (!text.trim()) throw new OpError('Text must not be empty');
  return addClip(tl, { asset_id: newAssetId, kind: 'text', text }, 'overlay', startTick, newClipId);
}

export function moveClip(tl: Timeline, clipId: string, newStartTick: number, trackId?: string): Timeline {
  const clip = clone(clipFor(tl, clipId));
  clip.start_tick = Math.max(0, snapToFrame(tl, newStartTick));
  if (trackId !== undefined) clip.track_id = trackId;
  assertNoOverlap(tl.clips, clip, clipId);
  return withClip(tl, clip);
}

export function trimStart(tl: Timeline, clipId: string, newStartTick: number): Timeline {
  const clip = clone(clipFor(tl, clipId));
  const oldEnd = clip.start_tick + clip.duration_ticks;
  const start = Math.min(Math.max(0, snapToFrame(tl, newStartTick)), oldEnd - frameTicks(tl));
  const delta = start - clip.start_tick;
  clip.start_tick = start;
  clip.duration_ticks = oldEnd - start;
  if (clip.source_in_ms !== null) clip.source_in_ms = Math.round(clip.source_in_ms + ticksToMs(tl, delta));
  assertNoOverlap(tl.clips, clip, clipId);
  return withClip(tl, clip);
}

export function trimEnd(tl: Timeline, clipId: string, newEndTick: number): Timeline {
  const clip = clone(clipFor(tl, clipId));
  const asset = assetFor(tl, clip);
  const minDuration = frameTicks(tl);
  let duration = Math.max(minDuration, snapToFrame(tl, newEndTick) - clip.start_tick);
  if (clip.source_in_ms !== null && asset.duration_ms != null) {
    const sourceAvailable = asset.duration_ms - clip.source_in_ms;
    duration = Math.min(duration, Math.max(minDuration, snapToFrame(tl, msToTicks(tl, sourceAvailable))));
    clip.source_out_ms = Math.round(Math.min(asset.duration_ms, clip.source_in_ms + ticksToMs(tl, duration)));
    duration = snapToFrame(tl, msToTicks(tl, clip.source_out_ms - clip.source_in_ms));
  }
  clip.duration_ticks = duration;
  assertNoOverlap(tl.clips, clip, clipId);
  const clips = tl.clips.map(item => item.clip_id === clipId ? clip : item);
  return { ...tl, clips, duration_ticks: Math.max(1000, ...clips.map(item => item.start_tick + item.duration_ticks)) };
}

export function splitAt(tl: Timeline, clipId: string, tick: number, newClipId: string): Timeline {
  if (tl.clips.some(item => item.clip_id === newClipId)) throw new OpError(`Clip id '${newClipId}' already exists`);
  const original = clone(clipFor(tl, clipId));
  const split = snapToFrame(tl, tick);
  const end = original.start_tick + original.duration_ticks;
  if (split <= original.start_tick || split >= end) throw new OpError(`Split tick ${tick} is outside clip '${clipId}'`);
  const leftDuration = split - original.start_tick;
  const right = clone(original);
  right.clip_id = newClipId;
  right.start_tick = split;
  right.duration_ticks = end - split;
  right.transition = null;
  if (original.source_in_ms !== null) {
    const splitMs = Math.round(ticksToMs(tl, leftDuration));
    original.source_out_ms = (original.source_in_ms ?? 0) + splitMs;
    right.source_in_ms = original.source_out_ms;
  }
  original.duration_ticks = leftDuration;
  const clips = tl.clips.flatMap(item => item.clip_id === clipId ? [original, right] : [clone(item)]);
  return { ...tl, clips };
}

export function deleteClip(tl: Timeline, clipId: string): Timeline {
  if (!tl.clips.some(item => item.clip_id === clipId)) throw new OpError(`Unknown clip '${clipId}'`);
  const clips = tl.clips.filter(item => item.clip_id !== clipId).map(clone);
  return { ...tl, clips, duration_ticks: Math.max(1000, ...clips.map(item => item.start_tick + item.duration_ticks)) };
}
