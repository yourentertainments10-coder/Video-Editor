import { describe, expect, it } from 'vitest';
import { planSlots } from '../src/timeline/playerSlots';

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

describe('planSlots', () => {
  it('loads the current clip into the active player and the next clip into the standby player', () => {
    const plan = planSlots([null, null], 0, tl(), 100);
    expect(plan).toEqual({ active: 0, show: true, loads: [
      { slot: 0, clip_id: 'a', asset_id: 'v1', source_ms: 1100 },
      { slot: 1, clip_id: 'b', asset_id: 'v1', source_ms: 4000 },
    ] });
  });

  it('a cut swaps players without reloading the clip that is now showing', () => {
    const plan = planSlots(['a', 'b'], 0, tl(), 500);
    expect(plan.active).toBe(1);
    expect(plan.loads).toEqual([]);           // nothing after b until the gap ends: c is preloaded below
  });

  it('preloads the clip after a gap while the gap shows black', () => {
    const plan = planSlots(['a', 'b'], 1, tl(), 1500);
    expect(plan.show).toBe(false);
    expect(plan.loads).toEqual([{ slot: 0, clip_id: 'c', asset_id: 'v2', source_ms: 0 }]);
  });

  it('does nothing when both players already hold the right clips', () => {
    expect(planSlots(['c', 'b'], 0, tl(), 2500)).toEqual({ active: 0, show: true, loads: [] });
  });

  it('a seek to an unloaded clip loads it into the active player', () => {
    const plan = planSlots(['a', 'b'], 0, tl(), 2500);
    expect(plan.active).toBe(0);
    expect(plan.loads[0]).toEqual({ slot: 0, clip_id: 'c', asset_id: 'v2', source_ms: 500 });
  });
});
