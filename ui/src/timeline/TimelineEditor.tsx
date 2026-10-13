import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Asset, thumbnailUrl } from '../api';
import { addClip, deleteClip, moveClip, snapToFrame, splitAt, Timeline, trimEnd, trimStart } from './ops';

const tracks = [
  { id: 'video', label: 'Video' }, { id: 'overlay', label: 'Overlay' },
  { id: 'voice', label: 'Voice' }, { id: 'music', label: 'Music' },
];

type Props = {
  timeline: Timeline; assets: Asset[]; projectId: string;
  selectedId: string | null; onSelect: (id: string | null) => void;
  playhead: number; onPlayhead: (tick: number) => void;
  onChange: (timeline: Timeline, mode?: 'preview' | 'commit') => void; issues: Array<{ clip_id?: string; message: string }>;
  error: string; onError: (value: string) => void;
  playing: boolean; onTogglePlay: () => void; timecode: string;
  onShortcuts: () => void; onRegisterZoomActions: (actions: { zoomIn: () => void; zoomOut: () => void; zoomFit: () => void }) => void;
};

export function TimelineEditor({ timeline, assets, projectId, selectedId, onSelect, playhead, onPlayhead, onChange, issues, error, onError, playing, onTogglePlay, timecode, onShortcuts, onRegisterZoomActions }: Props) {
  const [scale, setScale] = useState(0.1);
  const canvas = useRef<HTMLDivElement>(null);
  const contentWidth = Math.max(1000, timeline.duration_ticks * scale + 400);
  const byId = useMemo(() => new Map(assets.map(asset => [asset.asset_id, asset])), [assets]);
  useEffect(() => onRegisterZoomActions({
    zoomIn: () => setScale(value => Math.min(.8, value * 1.15)),
    zoomOut: () => setScale(value => Math.max(.025, value * .87)),
    zoomFit: () => setScale(Math.max(.025, Math.min(.8, ((canvas.current?.clientWidth ?? 1000) - 120) / Math.max(1, timeline.duration_ticks)))),
  }), [timeline.duration_ticks, onRegisterZoomActions]);
  const apply = (fn: () => Timeline) => { try { onChange(fn()); onError(''); } catch (e) { onError(e instanceof Error ? e.message : String(e)); } };

  const snapNear = (tick: number, excluding?: string) => {
    const targets = [playhead,
      ...timeline.markers.filter(marker => marker.kind === 'beat').map(marker => marker.position_tick),
      ...timeline.clips.filter(c => c.clip_id !== excluding).flatMap(c => [c.start_tick, c.start_tick + c.duration_ticks])];
    const close = targets.find(t => Math.abs((t - tick) * scale) <= 8);
    return snapToFrame(timeline, close ?? tick);
  };
  const timeAt = (event: React.DragEvent | React.MouseEvent, element: HTMLElement) => {
    const rect = element.getBoundingClientRect();
    return Math.max(0, (event.clientX - rect.left) / scale);
  };
  const drop = (event: React.DragEvent, trackId: string) => {
    event.preventDefault();
    const region = event.currentTarget as HTMLElement;
    const raw = timeAt(event, region);
    const assetId = event.dataTransfer.getData('application/x-editor-asset');
    const clipId = event.dataTransfer.getData('application/x-editor-clip');
    if (assetId) {
      const asset = byId.get(assetId);
      if (!asset) return;
      const targetTrack = asset.kind === 'video' ? 'video'
        : asset.kind === 'image' || asset.kind === 'text' ? 'overlay'
          : asset.kind === 'audio' || asset.kind === 'music' ? (trackId === 'music' ? 'music' : 'voice') : trackId;
      apply(() => addClip(timeline, asset, targetTrack, snapNear(raw), `clip_${crypto.randomUUID().slice(0, 8)}`));
    } else if (clipId) {
      const clip = timeline.clips.find(item => item.clip_id === clipId);
      const grabOffset = Number(event.dataTransfer.getData('application/x-editor-clip-offset') || 0);
      if (clip) apply(() => moveClip(timeline, clipId, snapNear(raw - grabOffset, clipId), trackId));
    }
  };
  const splitSelected = () => {
    if (!selectedId) return;
    try { const next = splitAt(timeline, selectedId, playhead, `clip_${crypto.randomUUID().slice(0, 8)}`); onChange(next); onError(''); }
    catch (e) { onError(e instanceof Error ? e.message : String(e)); }
  };
  const deleteSelected = () => {
    if (!selectedId) return;
    apply(() => deleteClip(timeline, selectedId)); onSelect(null);
  };
  const beginTrim = (event: React.PointerEvent, clipId: string, side: 'start' | 'end') => {
    event.preventDefault(); event.stopPropagation();
    const initial = timeline;
    let latest: Timeline | null = null;
    const region = (event.currentTarget as HTMLElement).parentElement?.parentElement;
    if (!region) return;
    const rect = region.getBoundingClientRect();
    const onMove = (move: PointerEvent) => {
      const tick = snapNear(Math.max(0, (move.clientX - rect.left) / scale), clipId);
      try {
        latest = side === 'start' ? trimStart(initial, clipId, tick) : trimEnd(initial, clipId, tick);
        onChange(latest, 'preview');
        onError('');
      } catch (e) { onError(e instanceof Error ? e.message : String(e)); }
    };
    const done = () => {
      document.removeEventListener('pointermove', onMove); document.removeEventListener('pointerup', done);
      if (latest) onChange(latest, 'commit');
    };
    document.addEventListener('pointermove', onMove); document.addEventListener('pointerup', done, { once: true });
  };

  const endSec = Math.ceil(timeline.duration_ticks / timeline.time_base.ticks_per_second);
  return <section className="timeline-panel">
    <div className="timeline-heading"><div><h2>Timeline</h2><span>Drag media into a track · S split · Delete remove · T title</span></div><div className="timeline-transport">
      <button className="quiet" aria-label={playing ? 'Pause' : 'Play'} onClick={onTogglePlay}>{playing ? 'Ⅱ' : '▶'}</button>
      <output aria-label="Timecode" className="timecode">{timecode}</output>
      <button className="quiet" onClick={() => setScale(Math.max(.025, Math.min(.8, ((canvas.current?.clientWidth ?? 1000) - 120) / Math.max(1, timeline.duration_ticks))))} title="Zoom to fit">Fit</button>
      <button className="quiet" onClick={onShortcuts}>Shortcuts</button>
      <button className="quiet" onClick={splitSelected} disabled={!selectedId}>Split at playhead</button>
    </div></div>
    <div className="timeline-scroll" ref={canvas} onWheel={event => { if (event.ctrlKey) { event.preventDefault(); setScale(v => Math.max(.025, Math.min(.8, v * (event.deltaY < 0 ? 1.15 : .87)))); } }}>
      <div className="timeline-canvas" style={{ width: contentWidth + 88 }}>
        <div className="timeline-ruler" style={{ width: contentWidth }} onClick={event => onPlayhead(snapToFrame(timeline, timeAt(event, event.currentTarget)))}>
          {Array.from({ length: endSec + 2 }, (_, second) => <span key={second} style={{ left: second * timeline.time_base.ticks_per_second * scale }}>{second}s</span>)}
          {timeline.markers.filter(marker => marker.kind === 'beat').map(marker => <i key={marker.marker_id}
            className={`beat-marker ${marker.label === 'downbeat' ? 'downbeat' : ''}`}
            style={{ left: marker.position_tick * scale }} title={marker.label ?? 'Beat'} />)}
          <i className="playhead" style={{ left: playhead * scale }} />
        </div>
        {tracks.map(track => <div className="track-row" key={track.id}>
          <strong>{track.label}</strong>
          <div className="track-content" style={{ width: contentWidth }} onDragOver={event => event.preventDefault()} onDrop={event => drop(event, track.id)} onClick={event => {
            if ((event.target as HTMLElement).classList.contains('track-content')) onPlayhead(snapToFrame(timeline, timeAt(event, event.currentTarget)));
          }}>
            {timeline.clips.filter(clip => clip.track_id === track.id).map(clip => {
              const asset = byId.get(clip.asset_id) ?? timeline.assets.find(item => item.asset_id === clip.asset_id);
              const issueCount = issues.filter(issue => issue.clip_id === clip.clip_id).length;
              return <div className={`timeline-clip ${selectedId === clip.clip_id ? 'selected' : ''} ${asset?.kind === 'text' ? 'text-clip' : ''}`} key={clip.clip_id}
                draggable onDragStart={event => {
                  const rect = event.currentTarget.getBoundingClientRect();
                  event.dataTransfer.setData('application/x-editor-clip', clip.clip_id);
                  event.dataTransfer.setData('application/x-editor-clip-offset', String((event.clientX - rect.left) / scale));
                  event.dataTransfer.effectAllowed = 'move';
                }}
                onClick={event => { event.stopPropagation(); onSelect(clip.clip_id); }}
                style={{ left: clip.start_tick * scale, width: Math.max(36, clip.duration_ticks * scale) }} title={`${asset?.file_name ?? asset?.text ?? 'Clip'} · ${Math.round(clip.duration_ticks)} ticks`}>
                <span className="trim-handle left" onPointerDown={event => beginTrim(event, clip.clip_id, 'start')} />
                {asset?.kind !== 'text' && Number((asset as any)?.thumbnail_count ?? 10) > 0 && <img src={thumbnailUrl(projectId, clip.asset_id)} alt="" />}
                <span className="clip-name">{String(asset?.kind === 'text' ? asset.text ?? '' : asset?.file_name ?? clip.asset_id)}</span>
                {issueCount > 0 && <b className="issue-badge" title={issues.filter(issue => issue.clip_id === clip.clip_id).map(i => i.message).join('\n')}>{issueCount}</b>}
                <span className="trim-handle right" onPointerDown={event => beginTrim(event, clip.clip_id, 'end')} />
              </div>;
            })}
            <i className="playhead track-playhead" style={{ left: playhead * scale }} />
          </div>
        </div>)}
      </div>
    </div>
    {error && <p className="timeline-error">{error}</p>}
  </section>;
}

