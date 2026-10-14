import React, { useEffect, useRef, useState } from 'react';
import { assetProxyUrl } from '../api';
import { Timeline } from './ops';
import { clipVolume, playbackSource } from './navigation';
import { planPool, poolSize, Slot, SlotLoad } from './playerSlots';

type Props = { timeline: Timeline; projectId: string; playhead: number; playing: boolean; seekSerial: number };

const DRIFT_S = 0.15;           // re-sync a playing clip that is further than this off
const PAUSED_DRIFT_S = 0.02;    // paused: show the frame under the playhead
const MAX_LEAD_S = 0.5;         // most we seek ahead to cover the seek itself
const RESYNC_COOLDOWN_MS = 600; // no second re-sync of the same player within this time

/**
 * Rough timeline preview with a small pool of persistent <video> players (one per source file
 * plus a spare). Players keep their file loaded, so a cut is a visibility swap to a player that
 * was already seeked to the next clip — no reload at the cut, even when every cut changes file.
 */
export function PreviewPlayer({ timeline, projectId, playhead, playing, seekSerial }: Props) {
  const size = poolSize(timeline);
  const players = useRef<(HTMLVideoElement | null)[]>([]);
  const slots = useRef<Slot[]>([]);
  const clock = useRef(0);
  const seekLatency = useRef(0.1);
  const resyncAfter = useRef(new WeakMap<HTMLVideoElement, number>());
  const [active, setActive] = useState(0);
  const [show, setShow] = useState(false);

  while (slots.current.length < size) slots.current.push({ asset_id: null, clip_id: null, used: 0 });
  if (slots.current.length > size) slots.current.length = size;

  const load = (item: SlotLoad) => {
    const el = players.current[item.slot];
    if (!el) return;
    const slot = slots.current[item.slot];
    slot.clip_id = item.clip_id;
    // seek once the file's metadata is in; a later load before then just changes the target
    el.dataset.seekMs = String(Math.max(0, item.source_ms));
    const seek = () => { el.currentTime = Number(el.dataset.seekMs) / 1000; };
    if (slot.asset_id !== item.asset_id) {
      slot.asset_id = item.asset_id;
      el.src = assetProxyUrl(projectId, item.asset_id);
      el.addEventListener('loadedmetadata', seek, { once: true });
    } else if (el.readyState >= 1) {
      seek();
    }
    el.pause();
  };

  // a seek by the user is followed at once
  useEffect(() => { resyncAfter.current = new WeakMap(); }, [seekSerial]);

  // Runs on every playhead change (every animation frame while playing).
  useEffect(() => {
    const plan = planPool(slots.current, timeline, playhead);
    plan.loads.forEach(load);
    clock.current += 1;
    plan.touched.forEach(i => { slots.current[i].used = clock.current; });
    if (plan.active !== active) setActive(plan.active);
    if (plan.show !== show) setShow(plan.show);
    const current = players.current[plan.active];
    players.current.forEach((el, i) => {
      if (!el || i === plan.active) return;
      el.muted = true;
      if (!el.paused) el.pause();
    });
    const source = playbackSource(timeline, 'video', playhead);
    if (!current || !source) { current?.pause(); return; }
    // the clip's own sound plays with the preview (as in the render), at its clip gain
    const volume = clipVolume(timeline, source.clip_id);
    current.volume = volume;
    current.muted = !playing || volume === 0;
    const expected = source.source_ms / 1000;
    const now = performance.now();
    if (current.readyState >= 1 && !current.seeking && (!playing || now >= (resyncAfter.current.get(current) ?? 0))
        && Math.abs(current.currentTime - expected) > (playing ? DRIFT_S : PAUSED_DRIFT_S)) {
      // Land where the playhead will be once the seek is done, and give it time to settle:
      // re-seeking to "now" after every slow seek would leave the picture frozen.
      const lead = playing ? Math.min(seekLatency.current, MAX_LEAD_S) : 0;
      const t0 = now;
      current.addEventListener('seeked', () => {
        const took = (performance.now() - t0) / 1000;
        seekLatency.current = 0.5 * seekLatency.current + 0.5 * took;
      }, { once: true });
      current.currentTime = expected + lead;
      if (playing) resyncAfter.current.set(current, now + RESYNC_COOLDOWN_MS);
    }
    if (playing && current.paused) void current.play().catch(() => undefined);
    if (!playing && !current.paused) current.pause();
  }, [playhead, playing, seekSerial, timeline, projectId]);

  // a different project means different files: forget what the players hold
  useEffect(() => () => { slots.current = []; }, [projectId]);

  return <div className="preview-stack">
    {Array.from({ length: size }, (_, slot) => <video key={slot} ref={el => { players.current[slot] = el; }}
      className={show && slot === active ? 'preview-slot active' : 'preview-slot'}
      preload="auto" muted playsInline aria-hidden={!(show && slot === active)} />)}
    {!show && <div className="preview-black" aria-label="Black gap" />}
  </div>;
}
