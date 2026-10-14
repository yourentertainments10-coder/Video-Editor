import React, { useEffect, useRef, useState } from 'react';
import { assetProxyUrl } from '../api';
import { Timeline } from './ops';
import { playbackSource } from './navigation';
import { planSlots, SlotLoad } from './playerSlots';

type Props = { timeline: Timeline; projectId: string; playhead: number; playing: boolean; seekSerial: number };

const DRIFT_S = 0.15;

/** Rough timeline preview with two persistent <video> players (A/B), swapped at each cut. */
export function PreviewPlayer({ timeline, projectId, playhead, playing, seekSerial }: Props) {
  const players = useRef<[HTMLVideoElement | null, HTMLVideoElement | null]>([null, null]);
  const loaded = useRef<[string | null, string | null]>([null, null]);
  const [active, setActive] = useState<0 | 1>(0);
  const [show, setShow] = useState(false);

  const load = (item: SlotLoad) => {
    const el = players.current[item.slot];
    if (!el) return;
    loaded.current[item.slot] = item.clip_id;
    const url = assetProxyUrl(projectId, item.asset_id);
    const seek = () => { el.currentTime = Math.max(0, item.source_ms / 1000); };
    if (el.dataset.asset !== item.asset_id) {
      el.dataset.asset = item.asset_id;
      el.src = url;
      el.addEventListener('loadedmetadata', seek, { once: true });
    } else {
      seek();
    }
    el.pause();
  };

  // Runs on every playhead change (every animation frame while playing).
  useEffect(() => {
    const plan = planSlots(loaded.current, active, timeline, playhead);
    plan.loads.forEach(load);
    if (plan.active !== active) setActive(plan.active);
    if (plan.show !== show) setShow(plan.show);
    const current = players.current[plan.active];
    const standby = players.current[(1 - plan.active) as 0 | 1];
    if (standby && !standby.paused) standby.pause();
    const source = playbackSource(timeline, 'video', playhead);
    if (!current || !source) { current?.pause(); return; }
    const expected = source.source_ms / 1000;
    if (current.readyState >= 1 && Math.abs(current.currentTime - expected) > DRIFT_S) current.currentTime = expected;
    if (playing && current.paused) void current.play().catch(() => undefined);
    if (!playing && !current.paused) current.pause();
  }, [playhead, playing, seekSerial, timeline, projectId]);

  return <div className="preview-stack">
    {[0, 1].map(slot => <video key={slot} ref={el => { players.current[slot as 0 | 1] = el; }}
      className={show && slot === active ? 'preview-slot active' : 'preview-slot'}
      preload="auto" muted playsInline aria-hidden={!(show && slot === active)} />)}
    {!show && <div className="preview-black" aria-label="Black gap" />}
  </div>;
}
