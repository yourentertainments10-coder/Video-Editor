import { Timeline, TimelineClip } from './ops';

export function editPoints(tl: Timeline): number[] {
  return [...new Set([0, tl.duration_ticks,
    ...tl.clips.flatMap(clip => [clip.start_tick, clip.start_tick + clip.duration_ticks]),
  ])].sort((a, b) => a - b);
}

export function nextEditPoint(tl: Timeline, tick: number, direction: number): number {
  const points = editPoints(tl);
  if (direction > 0) return points.find(point => point > tick) ?? tl.duration_ticks;
  if (direction < 0) return [...points].reverse().find(point => point < tick) ?? 0;
  return tick;
}

export function stepFrames(tl: Timeline, tick: number, frames: number): number {
  const ticksPerFrame = tl.time_base.ticks_per_second * tl.settings.fps_den / tl.settings.fps_num;
  return Math.max(0, Math.min(tl.duration_ticks, Math.round(tick + frames * ticksPerFrame)));
}

export function formatTimecode(tl: Timeline, tick: number): string {
  const fps = tl.settings.fps_num / tl.settings.fps_den;
  const totalFrames = Math.max(0, Math.round(tick * fps / tl.time_base.ticks_per_second));
  const framesPerMinute = Math.round(fps * 60);
  const minutes = Math.floor(totalFrames / framesPerMinute);
  const withinMinute = totalFrames - minutes * framesPerMinute;
  const seconds = Math.floor(withinMinute / fps);
  const frames = withinMinute - Math.floor(seconds * fps);
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}:${String(frames).padStart(2, '0')}`;
}

export function playbackSource(tl: Timeline, trackId: string, tick: number):
  { clip_id: string; asset_id: string; source_ms: number; clip_end_tick: number } | null {
  const clip: TimelineClip | undefined = tl.clips.find(item => item.track_id === trackId
    && item.start_tick <= tick && tick < item.start_tick + item.duration_ticks);
  if (!clip) return null;
  const elapsed = (tick - clip.start_tick) * 1000 / tl.time_base.ticks_per_second;
  return {
    clip_id: clip.clip_id,
    asset_id: clip.asset_id,
    source_ms: (clip.source_in_ms ?? 0) + elapsed,
    clip_end_tick: Math.min(tl.duration_ticks, clip.start_tick + clip.duration_ticks),
  };
}

/** Preview volume (0..1) for a clip: 0 when muted, else its gain in dB as a linear factor (max 1). */
export function clipVolume(tl: Timeline, clipId: string): number {
  const clip = tl.clips.find(item => item.clip_id === clipId);
  if (!clip) return 0;
  const audio = clip.audio as { muted?: boolean; gain_db?: number } | undefined | null;
  if (audio?.muted) return 0;
  const gain = Number(audio?.gain_db ?? 0);
  return Math.max(0, Math.min(1, Math.pow(10, gain / 20)));
}

/** Asset of the clip on `track` under or after `tick` (the one playback reaches next), if any. */
export function upcomingAsset(tl: Timeline, track: string, tick: number): string | null {
  const next = tl.clips
    .filter(c => c.track_id === track && c.start_tick + c.duration_ticks > tick)
    .sort((a, b) => a.start_tick - b.start_tick)[0];
  return next ? next.asset_id : null;
}
