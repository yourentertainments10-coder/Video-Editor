import { Timeline } from './ops';
import { playbackSource } from './navigation';

export type Slot = { asset_id: string | null; clip_id: string | null; used: number };
export type SlotLoad = { slot: number; clip_id: string; asset_id: string; source_ms: number };
export type SlotPlan = { active: number; show: boolean; loads: SlotLoad[]; touched: number[] };

export const MAX_PLAYERS = 6;

/** How many <video> players the rough preview keeps: one per video asset plus one spare, 2..6. */
export function poolSize(tl: Timeline): number {
  const assets = new Set(tl.clips.filter(c => c.track_id === 'video').map(c => c.asset_id));
  return Math.max(2, Math.min(MAX_PLAYERS, assets.size + 1));
}

/** Least recently used slot, preferring one already holding `asset` (a seek, not a reload). */
function pick(slots: readonly Slot[], asset: string, exclude: number): number {
  let best = -1;
  for (const wantAsset of [true, false]) {
    for (let i = 0; i < slots.length; i++) {
      if (i === exclude || (wantAsset && slots[i].asset_id !== asset)) continue;
      if (best < 0 || slots[i].used < slots[best].used) best = i;
    }
    if (best >= 0) return best;
  }
  return best;
}

/**
 * Player-pool schedule for the rough preview.
 * Each player keeps the file it was given, so cutting back to a file that is already loaded
 * is only a seek. The player showing the clip under the playhead is `active`; the NEXT clip is
 * pre-loaded and pre-seeked in another player, so a cut is just a visibility swap.
 */
export function planPool(slots: readonly Slot[], tl: Timeline, tick: number): SlotPlan {
  const loads: SlotLoad[] = [];
  const touched: number[] = [];
  const cur = playbackSource(tl, 'video', tick);
  let active = -1;
  let next;
  if (cur) {
    active = slots.findIndex(s => s.clip_id === cur.clip_id);
    if (active < 0) {
      active = pick(slots, cur.asset_id, -1);
      loads.push({ slot: active, clip_id: cur.clip_id, asset_id: cur.asset_id, source_ms: cur.source_ms });
    }
    touched.push(active);
    next = playbackSource(tl, 'video', cur.clip_end_tick);
  } else {
    const upcoming = tl.clips
      .filter(c => c.track_id === 'video' && c.start_tick > tick)
      .sort((x, y) => x.start_tick - y.start_tick)[0];
    next = upcoming ? playbackSource(tl, 'video', upcoming.start_tick) : null;
  }
  if (next && next.clip_id !== cur?.clip_id) {
    let slot = slots.findIndex((s, i) => s.clip_id === next.clip_id && i !== active);
    if (slot < 0) {
      slot = pick(slots, next.asset_id, active);
      if (slot >= 0) loads.push({ slot, clip_id: next.clip_id, asset_id: next.asset_id, source_ms: next.source_ms });
    }
    if (slot >= 0) touched.push(slot);
  }
  return { active: Math.max(active, 0), show: !!cur, loads, touched };
}
