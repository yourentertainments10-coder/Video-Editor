import React, { useMemo, useState } from 'react';
import { Asset } from '../api';

type Props = {
  assets: Asset[];
  hasClips: boolean;
  active: boolean;
  progress: number;
  message: string;
  error: string | null;
  onStart: (request: { musicAssetId: string; mood: 'energetic' | 'calm'; durationS: number; videoAssetIds: string[] }) => void;
  onCancel: () => void;
  onClose: () => void;
};

export function AutoEditDialog({ assets, hasClips, active, progress, message, error, onStart, onCancel, onClose }: Props) {
  const musicAssets = useMemo(() => assets.filter(asset => asset.kind === 'audio' || asset.kind === 'music'), [assets]);
  const videos = useMemo(() => assets.filter(asset => asset.kind === 'video'), [assets]);
  const [musicAssetId, setMusicAssetId] = useState('');
  const [mood, setMood] = useState<'energetic' | 'calm'>('energetic');
  const [durationS, setDurationS] = useState(30);
  const [selectedVideos, setSelectedVideos] = useState<Set<string>>(() => new Set(videos.map(video => video.asset_id)));

  return <div className="modal-backdrop" role="presentation">
    <section className="panel auto-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="auto-edit-title">
      <div className="section-head"><div><h2 id="auto-edit-title">Auto-edit</h2><span>Build a beat-synced timeline from this project</span></div>
        {!active && <button className="quiet" aria-label="Close auto-edit" onClick={onClose}>×</button>}
      </div>
      {hasClips && <p className="auto-edit-warning">Replace current timeline? You can undo this.</p>}
      <label>Music<select aria-label="Music" value={musicAssetId} onChange={event => setMusicAssetId(event.target.value)} disabled={active}>
        <option value="">Choose audio…</option>{musicAssets.map(asset => <option key={asset.asset_id} value={asset.asset_id}>{asset.file_name}</option>)}
      </select></label>
      <label>Mood<select aria-label="Mood" value={mood} onChange={event => setMood(event.target.value as 'energetic' | 'calm')} disabled={active}>
        <option value="energetic">Energetic</option><option value="calm">Calm</option>
      </select></label>
      <label>Length in seconds<input aria-label="Length in seconds" type="number" min="0.1" step="0.1" value={durationS}
        onChange={event => setDurationS(Number(event.target.value))} disabled={active}/></label>
      <fieldset disabled={active}><legend>Use videos</legend>{videos.map(video => <label className="check-label" key={video.asset_id}>
        <input type="checkbox" checked={selectedVideos.has(video.asset_id)} onChange={event => setSelectedVideos(old => {
          const next = new Set(old); if (event.target.checked) next.add(video.asset_id); else next.delete(video.asset_id); return next;
        })}/>{video.file_name}
      </label>)}</fieldset>
      {active && <><div className="job-line"><span>{message}</span><strong>{Math.round(progress * 100)}%</strong></div><div className="progress"><i style={{ width: `${progress * 100}%` }}/></div></>}
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="auto-edit-actions">{active ? <button className="quiet" onClick={onCancel}>Cancel</button> : <>
        <button className="quiet" onClick={onClose}>Close</button><button className="primary" disabled={!musicAssetId || durationS <= 0 || selectedVideos.size === 0}
          onClick={() => onStart({ musicAssetId, mood, durationS, videoAssetIds: [...selectedVideos] })}>Create timeline</button>
      </>}</div>
    </section>
  </div>;
}
