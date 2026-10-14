import { describe, expect, it } from 'vitest';
import { MAX_PLAYERS, planPool, poolSize, Slot } from '../src/timeline/playerSlots';

const tl = (): any => ({
  duration_ticks: 4000,
  time_base: { ticks_per_second: 1000 },
  settings: { fps_num: 30, fps_den: 1, width: 1920, height: 1080 },
  assets: [], markers: [],
  clips: [
    { clip_id: 'a', asset_id: 'v1', track_id: 'video', start_tick: 0, duration_ticks: 500, source_in_ms: 1000, source_out_ms: 1500 },
    { clip_id: 'b', asset_id: 'v1', track_id: 'video', start_tick: 500, duration_ticks: 500, source_in_ms: 4000, source_out_ms: 4500 },
    { clip_id: 'c', asset_id: 'v2', track_id: 'video', start_tick: 2000, duration_ticks: 1000, source_in_ms: 0, source_out_ms: 1000 },
  ],
});
const empty = (n: number): Slot[] => Array.from({ length: n }, () => ({ asset_id: null, clip_id: null, used: 0 }));
const slot = (asset_id: string | null, clip_id: string | null, used = 1): Slot => ({ asset_id, clip_id, used });

/** Play the plan forward like the player does; count file loads (src changes) and late cuts. */
function simulate(timeline: any, size: number, step = 33) {
  const slots = empty(size);
  let reloads = 0; let clock = 0; let unprepared = 0; let prevActiveClip: string | null = null;
  for (let tick = 0; tick < timeline.duration_ticks; tick += step) {
    const plan = planPool(slots, timeline, tick);
    const activeHeld = slots[plan.active].clip_id;
    const isCut = plan.show && activeHeld !== prevActiveClip;
    for (const load of plan.loads) {
      if (slots[load.slot].asset_id !== load.asset_id) reloads++;
      if (isCut && load.slot === plan.active && tick > 0) unprepared++;
      slots[load.slot].asset_id = load.asset_id; slots[load.slot].clip_id = load.clip_id;
    }
    clock++; plan.touched.forEach(i => { slots[i].used = clock; });
    if (plan.show) prevActiveClip = slots[plan.active].clip_id;
  }
  return { reloads, unprepared };
}

describe('poolSize', () => {
  it('is one player per video file plus a spare, between 2 and the maximum', () => {
    expect(poolSize(tl())).toBe(3);
    expect(poolSize({ ...tl(), clips: [] })).toBe(2);
    const many = { ...tl(), clips: Array.from({ length: 20 }, (_, i) => ({ ...tl().clips[0], clip_id: `x${i}`, asset_id: `v${i}` })) };
    expect(poolSize(many)).toBe(MAX_PLAYERS);
  });
});

describe('planPool', () => {
  it('loads the current clip and pre-loads the next clip in another player', () => {
    const plan = planPool(empty(3), tl(), 100);
    expect(plan.active).toBe(0);
    expect(plan.show).toBe(true);
    expect(plan.loads).toEqual([
      { slot: 0, clip_id: 'a', asset_id: 'v1', source_ms: 1100 },
      { slot: 1, clip_id: 'b', asset_id: 'v1', source_ms: 4000 },
    ]);
  });

  it('a cut switches to the player already holding the clip, with no load', () => {
    const plan = planPool([slot('v1', 'a'), slot('v1', 'b'), slot(null, null, 0)], tl(), 500);
    expect(plan.active).toBe(1);
    expect(plan.loads).toEqual([]);
  });

  it('pre-loads the clip after a gap while the gap shows black, into a free player', () => {
    const plan = planPool([slot('v1', 'a', 1), slot('v1', 'b', 2), slot(null, null, 0)], tl(), 1500);
    expect(plan.show).toBe(false);
    expect(plan.loads).toEqual([{ slot: 2, clip_id: 'c', asset_id: 'v2', source_ms: 0 }]);
  });

  it('re-uses a player that already has the file (a seek, not a reload)', () => {
    const plan = planPool([slot('v2', 'old', 1), slot('v1', 'x', 5), slot('v3', 'y', 0)], tl(), 2500);
    expect(plan.loads).toEqual([{ slot: 0, clip_id: 'c', asset_id: 'v2', source_ms: 500 }]);
  });

  it('never pre-loads into the player that is showing', () => {
    const plan = planPool([slot('v1', 'a', 9), slot('v2', 'c', 1)], tl(), 100);
    expect(plan.active).toBe(0);
    expect(plan.loads).toEqual([{ slot: 1, clip_id: 'b', asset_id: 'v1', source_ms: 4000 }]);
  });

  it('fast cutting between five files: each file is loaded once and every cut was prepared', () => {
    const clips = Array.from({ length: 55 }, (_, i) => ({
      clip_id: `k${i}`, asset_id: `f${(i * 3) % 5}`, track_id: 'video',
      start_tick: i * 400, duration_ticks: 400, source_in_ms: 1000 + i * 700, source_out_ms: 1400 + i * 700,
    }));
    const fast = { ...tl(), clips, duration_ticks: 55 * 400 };
    const pooled = simulate(fast, poolSize(fast));
    expect(pooled.reloads).toBe(5);
    expect(pooled.unprepared).toBe(0);
    // the old two-player scheme had to change file at almost every cut
    expect(simulate(fast, 2).reloads).toBeGreaterThan(40);
  });
});
