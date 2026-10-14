import React, { useMemo, useState } from 'react';
import { Asset } from '../api';
import { applyBrief, BriefResponse, TransitionPolicy } from './brief';

export type AutoEditRequest = {
  musicAssetId: string; mood: 'energetic' | 'calm'; durationS: number; videoAssetIds: string[];
  transitionPolicy: TransitionPolicy; title: string | null;
  /** footage the edit should prefer / leave out (tags from the Director, e.g. 'close-up', 'dark') */
  shots?: { prefer: string[]; avoid: string[] };
  /** finishing pass: transitions on strong beats, beat accents, camera moves and a colour look */
  style?: EditStyle; look?: string | null; transitionFamily?: string | null;
};
export type EditStyle = 'clean' | 'auto' | 'bold';
export const LOOK_NAMES: Record<string, string> = {
  cinematic: 'Cinematic', vintage: 'Vintage', warm: 'Warm', cool: 'Cool', punchy: 'Punchy', matte: 'Matte', mono: 'Black and white',
};
export const STYLE_NAMES: Record<EditStyle, string> = {
  auto: 'Auto effects (recommended)', bold: 'Bold effects', clean: 'Clean cuts, no effects',
};

type Props = {
  assets: Asset[];
  hasClips: boolean;
  active: boolean;
  progress: number;
  message: string;
  error: string | null;
  onStart: (request: AutoEditRequest) => void;
  onReadBrief: (text: string) => Promise<BriefResponse>;
  onProfile: (profile: string) => void;
  onCancel: () => void;
  initialMood?: 'energetic' | 'calm';
  onClose: () => void;
};

export function AutoEditDialog({ assets, hasClips, active, progress, message, error, onStart, onReadBrief, onProfile, onCancel, onClose, initialMood }: Props) {
  const musicAssets = useMemo(() => assets.filter(asset => asset.kind === 'audio' || asset.kind === 'music'), [assets]);
  const videos = useMemo(() => assets.filter(asset => asset.kind === 'video'), [assets]);
  const [musicAssetId, setMusicAssetId] = useState('');
  const [mood, setMood] = useState<'energetic' | 'calm'>(initialMood ?? 'energetic');
  const [durationS, setDurationS] = useState(30);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [selectedVideos, setSelectedVideos] = useState<Set<string>>(() => new Set(videos.map(video => video.asset_id)));
  const [transitionPolicy, setTransitionPolicy] = useState<TransitionPolicy>('auto');
  const [title, setTitle] = useState('');
  const [briefText, setBriefText] = useState('');
  const [briefNotes, setBriefNotes] = useState<string[]>([]);
  const [briefQuestions, setBriefQuestions] = useState<string[]>([]);
  const [readingBrief, setReadingBrief] = useState(false);
  const [shots, setShots] = useState<{ prefer: string[]; avoid: string[] } | null>(null);
  const [style, setStyle] = useState<EditStyle>('auto');
  const [look, setLook] = useState<string>('');
  const [family, setFamily] = useState<string | null>(null);

  const readBrief = async () => {
    if (!briefText.trim()) return;
    setReadingBrief(true);
    try {
      const response = await onReadBrief(briefText);
      const out = applyBrief({ mood, durationS, videoIds: [...selectedVideos], transitionPolicy, title }, response);
      setMood(out.form.mood); setDurationS(out.form.durationS); setSelectedVideos(new Set(out.form.videoIds));
      setTransitionPolicy(out.form.transitionPolicy); setTitle(out.form.title);
      if (out.profile) onProfile(out.profile);
      const prefer = response.brief.prefer_tags ?? [], avoid = response.brief.avoid_tags ?? [];
      setShots(prefer.length || avoid.length ? { prefer, avoid } : null);
      if (response.brief.style) setStyle(response.brief.style);
      if (response.brief.look) setLook(response.brief.look);
      setFamily(response.brief.transition_family ?? null);
      setBriefNotes(out.notes); setBriefQuestions(out.questions);
    } catch (e) {
      setBriefNotes([]); setBriefQuestions([e instanceof Error ? e.message : String(e)]);
    } finally { setReadingBrief(false); }
  };
  const request = (): AutoEditRequest => ({
    musicAssetId, mood, durationS, videoAssetIds: [...selectedVideos], transitionPolicy, title: title.trim() || null,
    ...(shots ? { shots } : {}),
    style, look: look || null, transitionFamily: family,
  });

  return <div className="modal-backdrop" role="presentation">
    <section className="panel auto-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="auto-edit-title">
      <div className="section-head"><div><h2 id="auto-edit-title">Auto-edit</h2><span>Build a beat-synced timeline from this project</span></div>
        {!active && <button className="quiet" aria-label="Close auto-edit" onClick={onClose}>×</button>}
      </div>
      {hasClips && !confirmReplace && <p className="auto-edit-warning">Creating a timeline will replace the current clips.</p>}
      <div className="brief-box">
        <label>Describe the video (optional)<textarea aria-label="Describe the video" rows={2} value={briefText} disabled={active}
          placeholder="e.g. 20 second energetic reel, no transitions, title 'Goa 2026'"
          onChange={event => setBriefText(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) void readBrief(); }}/></label>
        <button className="quiet" disabled={active || readingBrief || !briefText.trim()} onClick={() => void readBrief()}>
          {readingBrief ? 'Reading…' : 'Fill in from description'}</button>
        {briefNotes.length > 0 && <ul className="brief-notes">{briefNotes.map(note => <li key={note}>{note}</li>)}</ul>}
        {briefQuestions.length > 0 && <ul className="brief-questions" role="alert">{briefQuestions.map(q => <li key={q}>{q}</li>)}</ul>}
      </div>
      <label>Music<select aria-label="Music" value={musicAssetId} onChange={event => setMusicAssetId(event.target.value)} disabled={active}>
        <option value="">Choose audio…</option>{musicAssets.map(asset => <option key={asset.asset_id} value={asset.asset_id}>{asset.file_name}</option>)}
      </select></label>
      <label>Mood<select aria-label="Mood" value={mood} onChange={event => setMood(event.target.value as 'energetic' | 'calm')} disabled={active}>
        <option value="energetic">Energetic</option><option value="calm">Calm</option>
      </select></label>
      <label>Length in seconds<input aria-label="Length in seconds" type="number" min="0.1" step="0.1" value={durationS}
        onChange={event => setDurationS(Number(event.target.value))} disabled={active}/></label>
      <label>Transitions<select aria-label="Transitions" value={transitionPolicy} disabled={active}
        onChange={event => setTransitionPolicy(event.target.value as TransitionPolicy)}>
        <option value="auto">Automatic (cuts)</option><option value="cuts_only">Cuts only</option><option value="soft">Soft fades</option>
      </select></label>
      <label>Style<select aria-label="Style" value={style} disabled={active} onChange={event => setStyle(event.target.value as EditStyle)}>
        {(Object.keys(STYLE_NAMES) as EditStyle[]).map(key => <option key={key} value={key}>{STYLE_NAMES[key]}</option>)}
      </select></label>
      <label>Look<select aria-label="Look" value={look} disabled={active} onChange={event => setLook(event.target.value)}>
        <option value="">{style === 'clean' ? 'None' : 'Automatic for the mood'}</option>
        {Object.entries(LOOK_NAMES).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
      </select></label>
      <label>Title (optional)<input aria-label="Title" maxLength={80} value={title} disabled={active}
        onChange={event => setTitle(event.target.value)}/></label>
      <fieldset disabled={active}><legend>Use videos</legend>{videos.map(video => <label className="check-label" key={video.asset_id}>
        <input type="checkbox" checked={selectedVideos.has(video.asset_id)} onChange={event => setSelectedVideos(old => {
          const next = new Set(old); if (event.target.checked) next.add(video.asset_id); else next.delete(video.asset_id); return next;
        })}/>{video.file_name}
      </label>)}</fieldset>
      {active && <><div className="job-line"><span>{message}</span><strong>{Math.round(progress * 100)}%</strong></div><div className="progress"><i style={{ width: `${progress * 100}%` }}/></div></>}
      {error && <p className="error-text" role="alert">{error}</p>}
      {confirmReplace && !active && <p className="auto-edit-warning" role="alert">Replace current timeline? You can undo this.</p>}
      <div className="auto-edit-actions">{active ? <button className="quiet" onClick={onCancel}>Cancel</button> : confirmReplace ? <>
        <button className="quiet" onClick={() => setConfirmReplace(false)}>Cancel</button><button className="primary" disabled={!musicAssetId || durationS <= 0 || selectedVideos.size === 0}
          onClick={() => onStart(request())}>Replace</button>
      </> : <>
        <button className="quiet" onClick={onClose}>Close</button><button className="primary" disabled={!musicAssetId || durationS <= 0 || selectedVideos.size === 0}
          onClick={() => hasClips ? setConfirmReplace(true) : onStart(request())}>Create timeline</button>
      </>}</div>
    </section>
  </div>;
}
