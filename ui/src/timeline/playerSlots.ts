import { Timeline } from './ops';
import { playbackSource } from './navigation';

export type SlotLoad = { slot: 0 | 1; clip_id: string; asset_id: string; source_ms: number };
export type SlotPlan = { active: 0 | 1; show: boolean; loads: SlotLoad[] };

/**
 * Two-player (A/B) schedule for the rough preview.
 * `slots` holds the clip id each player is loaded with. The active player shows the clip
 * under the playhead; the other one is pre-loaded and pre-seeked to the NEXT clip, so a cut
 * is just a visibility swap — no element is created or reloaded at the cut.
 */
export function planSlots(slots: readonly [string | null, string | null], active: 0 | 1,
                          tl: Timeline, tick: number): SlotPlan {
  const loads: SlotLoad[] = [];
  const cur = playbackSource(tl, 'video', tick);
  let next;
  let a = active;
  if (cur) {
    if (slots[a] !== cur.clip_id) {
      if (slots[1 - a] === cur.clip_id) a = (1 - a) as 0 | 1;
      else loads.push({ slot: a, clip_id: cur.clip_id, asset_id: cur.asset_id, source_ms: cur.source_ms });
    }
    next = playbackSource(tl, 'video', cur.clip_end_tick);
  } else {
    const upcoming = tl.clips
      .filter(c => c.track_id === 'video' && c.start_tick > tick)
      .sort((x, y) => x.start_tick - y.start_tick)[0];
    next = upcoming ? playbackSource(tl, 'video', upcoming.start_tick) : null;
  }
  const standby = (1 - a) as 0 | 1;
  if (next && next.clip_id !== cur?.clip_id && slots[standby] !== next.clip_id) {
    loads.push({ slot: standby, clip_id: next.clip_id, asset_id: next.asset_id, source_ms: next.source_ms });
  }
  return { active: a, show: !!cur, loads };
}
