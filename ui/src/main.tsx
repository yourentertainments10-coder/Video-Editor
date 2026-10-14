import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, Asset, assetAudioUrl, EditorCatalogue, Job, json, Project, saveTimeline, thumbnailUrl } from './api';
import { Inspector } from './timeline/Inspector';
import { exportAdvice } from './exportAdvice';
import { PreviewPlayer } from './timeline/PreviewPlayer';

// Frame sizes of the backend export profiles (poc/render/export.py), used for export advice.
const EXPORT_SIZES: Record<string, { name: string; width: number; height: number }> = {
  preview_540p: { name: 'preview_540p', width: 960, height: 540 },
  youtube_1080p: { name: 'youtube_1080p', width: 1920, height: 1080 },
  reels_9x16: { name: 'reels_9x16', width: 1080, height: 1920 },
  square_1x1: { name: 'square_1x1', width: 1080, height: 1080 },
};
import { TimelineEditor } from './timeline/TimelineEditor';
import { addTextClip, deleteClip, splitAt, Timeline, TimelineClip } from './timeline/ops';
import { AutoEditDialog } from './timeline/AutoEditDialog';
import { canRedo, canUndo, createHistory, History, push, redo, undo } from './timeline/history';
import { commandForKey } from './timeline/keys';
import { formatTimecode, nextEditPoint, playbackSource, stepFrames } from './timeline/navigation';
import './style.css';

const profiles = ['preview_540p', 'youtube_1080p', 'reels_9x16', 'square_1x1'];

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [catalogue, setCatalogue] = useState<EditorCatalogue | null>(null);
  const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [seekSerial, setSeekSerial] = useState(0);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [profile, setProfile] = useState('preview_540p');
  const [job, setJob] = useState<Job | null>(null);
  const [previewRender, setPreviewRender] = useState('');
  const [name, setName] = useState('My project');
  const [error, setError] = useState('');
  const [timelineError, setTimelineError] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [revision, setRevision] = useState(1);
  const [issues, setIssues] = useState<Array<{ code: string; message: string; clip_id?: string }>>([]);
  const [readiness, setReadiness] = useState<{ ok: boolean; error: string | null } | null>(null);
  const [checking, setChecking] = useState(false);
  const [showAutoEdit, setShowAutoEdit] = useState(false);
  const [autoEditError, setAutoEditError] = useState<string | null>(null);
  const [history, setHistory] = useState<History<Timeline> | null>(null);
  const [saveCycle, setSaveCycle] = useState(0);
  const dirty = useRef(false);
  const saveInFlight = useRef(false);
  const previewJob = useRef(false);
  const autoEditJob = useRef(false);
  const historyRef = useRef<History<Timeline> | null>(null);
  const coalesceRef = useRef<{ key: string; timer: number } | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const playbackClock = useRef<{ wall: number; tick: number }>({ wall: 0, tick: 0 });
  const zoomActions = useRef<{ zoomIn: () => void; zoomOut: () => void; zoomFit: () => void } | null>(null);

  const refreshProjects = async () => setProjects((await api<{ projects: Project[] }>('/api/projects')).projects);
  const runCheck = async (pid: string) => {
    setChecking(true);
    try { setReadiness(await api(`/api/projects/${pid}/check`, json('POST', {}))); }
    catch (e) { setReadiness({ ok: false, error: e instanceof Error ? e.message : String(e) }); }
    finally { setChecking(false); }
  };
  const openProject = async (value: Project) => {
    setProject(value); setSelectedAsset(null); setSelectedClipId(null); setJob(null); setError(''); setPreviewRender(''); setSaveMessage('Loading…');
    dirty.current = false;
    try {
      const [assetResult, saved, catalog] = await Promise.all([
        api<{ assets: Asset[] }>(`/api/projects/${value.project_id}/assets`),
        api<{ revision: number; timeline: Timeline }>(`/api/projects/${value.project_id}/timeline`),
        api<EditorCatalogue>('/api/catalogue'),
      ]);
      setAssets(assetResult.assets); setTimeline(saved.timeline); setRevision(saved.revision); setCatalogue(catalog);
      const initialHistory = createHistory(saved.timeline); historyRef.current = initialHistory; setHistory(initialHistory);
      setIssues([]); setSaveMessage('Saved'); setPlayhead(0);
      setPlaying(false); setSeekSerial(value => value + 1);
      await runCheck(value.project_id);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setSaveMessage(''); }
  };
  useEffect(() => { void refreshProjects().catch(e => setError(String(e))); }, []);

  useEffect(() => {
    if (!project || !timeline || !dirty.current) return;
    setSaveMessage('Unsaved changes');
    const pid = project.project_id;
    const pending = timeline;
    const timer = window.setTimeout(async () => {
      if (saveInFlight.current) return;
      saveInFlight.current = true;
      dirty.current = false;
      setSaveMessage('Saving…');
      try {
        const result = await saveTimeline(pid, revision, pending);
        if (result.conflict) {
          setTimeline(result.saved.timeline); setRevision(result.saved.revision); setIssues([]);
          const reset = createHistory(result.saved.timeline); historyRef.current = reset; setHistory(reset);
          setSaveMessage('Timeline changed elsewhere — reloaded');
          await runCheck(pid);
        } else {
          setRevision(result.revision); setIssues(result.issues); setSaveMessage('Saved');
          await runCheck(pid);
        }
      } catch (e) { setSaveMessage('Save failed'); setError(e instanceof Error ? e.message : String(e)); dirty.current = true; }
      finally { saveInFlight.current = false; setSaveCycle(value => value + 1); }
    }, 800);
    return () => window.clearTimeout(timer);
  }, [timeline, project, revision, saveCycle]);

  useEffect(() => {
    if (!job || !project || !['queued', 'running'].includes(job.status)) return;
    const timer = window.setTimeout(() => {
      void api<Job>(`/api/jobs/${job.job_id}`).then(async next => {
        setJob(next);
        if (next.status === 'done' && autoEditJob.current) {
          autoEditJob.current = false;
          const saved = await api<{ revision: number; timeline: Timeline }>(`/api/projects/${project.project_id}/timeline`);
          const prior = historyRef.current;
          if (prior) {
            const nextHistory = push(prior, saved.timeline);
            historyRef.current = nextHistory; setHistory(nextHistory);
          }
          dirty.current = false;
          setTimeline(saved.timeline); setRevision(saved.revision); setIssues([]); setSaveMessage('Saved');
          setSelectedClipId(null); setPlayhead(0); setAutoEditError(null);
          await runCheck(project.project_id);
        } else if (['failed', 'cancelled'].includes(next.status)) {
          autoEditJob.current = false;
          if (next.status === 'failed' && next.error) setAutoEditError(next.error);
        }
        if (next.status === 'done' && previewJob.current && next.output) {
          const filename = next.output.split(/[\\/]/).pop() ?? '';
          setPreviewRender(`/media/projects/${project.project_id}/exports/${encodeURIComponent(filename)}`);
        }
      }).catch(e => setError(String(e)));
    }, 500);
    return () => window.clearTimeout(timer);
  }, [job, project]);

  const createProject = async () => {
    try {
      const result = await api<Project>('/api/projects', json('POST', { name }));
      await refreshProjects(); await openProject(result);
    } catch (e) { setError(String(e)); }
  };
  const addMedia = async () => {
    if (!project) return;
    try {
      const selection = await api<{ paths: string[] }>('/api/dialog/open-files', json('POST', {}));
      if (!selection.paths.length) return;
      const result = await api<{ assets: Asset[] }>(`/api/projects/${project.project_id}/assets`, json('POST', { paths: selection.paths }));
      setAssets(result.assets); setError('');
    } catch (e) { setError(String(e)); }
  };
  const endCoalesce = (key: string) => {
    if (coalesceRef.current?.key === key) {
      window.clearTimeout(coalesceRef.current.timer);
      coalesceRef.current = null;
    }
  };
  const updateTimeline = (next: Timeline, context?: string) => {
    if (context === 'preview') { setTimeline(next); return; }
    dirty.current = true;
    const prior = historyRef.current;
    if (prior) {
      let nextHistory: History<Timeline>;
      if (context && context !== 'commit' && coalesceRef.current?.key === context) {
        nextHistory = { ...prior, present: next, future: [] };
        window.clearTimeout(coalesceRef.current.timer);
        coalesceRef.current.timer = window.setTimeout(() => endCoalesce(context), 1000);
      } else {
        if (coalesceRef.current) { window.clearTimeout(coalesceRef.current.timer); coalesceRef.current = null; }
        nextHistory = push(prior, next);
        if (context && context !== 'commit') {
          const timer = window.setTimeout(() => endCoalesce(context), 1000);
          coalesceRef.current = { key: context, timer };
        }
      }
      historyRef.current = nextHistory; setHistory(nextHistory);
    }
    setTimeline(next);
  };
  const travelHistory = (move: (value: History<Timeline>) => History<Timeline>) => {
    if (coalesceRef.current) { window.clearTimeout(coalesceRef.current.timer); coalesceRef.current = null; }
    const prior = historyRef.current;
    if (!prior) return;
    const next = move(prior);
    if (next === prior) return;
    historyRef.current = next; setHistory(next); dirty.current = true; setTimeline(next.present);
  };
  const undoEdit = () => travelHistory(undo);
  const redoEdit = () => travelHistory(redo);
  const registerZoomActions = (actions: { zoomIn: () => void; zoomOut: () => void; zoomFit: () => void }) => { zoomActions.current = actions; };
  const seekTo = (tick: number) => {
    const next = Math.max(0, Math.min(timeline?.duration_ticks ?? 0, tick));
    setPlayhead(next);
    setSeekSerial(value => value + 1);
    if (playing && timeline) playbackClock.current = { wall: performance.now(), tick: next };
  };
  const togglePlayback = () => {
    if (playing) { setPlaying(false); return; }
    const start = timeline && playhead >= timeline.duration_ticks ? 0 : playhead;
    playbackClock.current = { wall: performance.now(), tick: start };
    if (start !== playhead) { setPlayhead(start); setSeekSerial(value => value + 1); }
    setPlaying(true);
  };
  const musicSource = timeline ? playbackSource(timeline, 'music', playhead) : null;
  const playbackAudioUrl = project && musicSource ? assetAudioUrl(project.project_id, musicSource.asset_id) : '';

  useEffect(() => {
    if (!playing || !timeline) return;
    let frame = 0;
    const advance = (now: number) => {
      const clock = playbackClock.current;
      const tick = Math.min(timeline.duration_ticks,
        clock.tick + (now - clock.wall) * timeline.time_base.ticks_per_second / 1000);
      setPlayhead(tick);
      const audio = audioRef.current;
      const source = playbackSource(timeline, 'music', tick);
      if (audio && source) {
        const expected = source.source_ms / 1000;
        if (Math.abs(audio.currentTime - expected) > 0.1) audio.currentTime = expected;
        if (audio.paused) void audio.play().catch(() => undefined);
      } else if (audio && !audio.paused) audio.pause();
      if (tick >= timeline.duration_ticks) { setPlaying(false); return; }
      frame = requestAnimationFrame(advance);
    };
    frame = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(frame);
  }, [playing, timeline]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (musicSource) {
      audio.currentTime = Math.max(0, musicSource.source_ms / 1000);
      if (playing) void audio.play().catch(() => undefined);
      else audio.pause();
    } else audio.pause();
  }, [musicSource?.clip_id, playbackAudioUrl, seekSerial, playing]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.matches('input,textarea,select,[contenteditable=true]')) return;
      const command = commandForKey(event);
      if (!command) return;
      event.preventDefault();
      if (!timeline) return;
      const fps = timeline.settings.fps_num / timeline.settings.fps_den;
      switch (command) {
        case 'playPause': togglePlayback(); break;
        case 'prevFrame': seekTo(stepFrames(timeline, playhead, -1)); break;
        case 'nextFrame': seekTo(stepFrames(timeline, playhead, 1)); break;
        case 'back1s': seekTo(stepFrames(timeline, playhead, -Math.round(fps))); break;
        case 'forward1s': seekTo(stepFrames(timeline, playhead, Math.round(fps))); break;
        case 'prevEdit': seekTo(nextEditPoint(timeline, playhead, -1)); break;
        case 'nextEdit': seekTo(nextEditPoint(timeline, playhead, 1)); break;
        case 'home': seekTo(0); break;
        case 'end': seekTo(timeline.duration_ticks); break;
        case 'split': if (selectedClipId) {
          try { updateTimeline(splitAt(timeline, selectedClipId, playhead, `clip_${crypto.randomUUID().slice(0, 8)}`)); setTimelineError(''); }
          catch (e) { setTimelineError(e instanceof Error ? e.message : String(e)); }
        } break;
        case 'addText':
          try { updateTimeline(addTextClip(timeline, 'New title', playhead, `text_${crypto.randomUUID().slice(0, 8)}`, `clip_${crypto.randomUUID().slice(0, 8)}`)); setTimelineError(''); }
          catch (e) { setTimelineError(e instanceof Error ? e.message : String(e)); }
          break;
        case 'delete': if (selectedClipId) {
          try { updateTimeline(deleteClip(timeline, selectedClipId)); setSelectedClipId(null); setTimelineError(''); }
          catch (e) { setTimelineError(e instanceof Error ? e.message : String(e)); }
        } break;
        case 'undo': undoEdit(); break;
        case 'redo': redoEdit(); break;
        case 'zoomIn': zoomActions.current?.zoomIn(); break;
        case 'zoomOut': zoomActions.current?.zoomOut(); break;
        case 'zoomFit': zoomActions.current?.zoomFit(); break;
        case 'saveNow': {
          if (!project || !dirty.current || saveInFlight.current) { setSaveCycle(value => value + 1); break; }
          const pid = project.project_id;
          const pending = timeline;
          saveInFlight.current = true; dirty.current = false; setSaveMessage('Saving…');
          void saveTimeline(pid, revision, pending).then(async result => {
            if (result.conflict) {
              setTimeline(result.saved.timeline); setRevision(result.saved.revision); setIssues([]);
              const reset = createHistory(result.saved.timeline); historyRef.current = reset; setHistory(reset);
              setSaveMessage('Timeline changed elsewhere — reloaded');
            } else { setRevision(result.revision); setIssues(result.issues); setSaveMessage('Saved'); }
            await runCheck(pid);
          }).catch(e => { dirty.current = true; setSaveMessage('Save failed'); setError(e instanceof Error ? e.message : String(e)); })
            .finally(() => { saveInFlight.current = false; setSaveCycle(value => value + 1); });
          break;
        }
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [timeline, playhead, playing, selectedClipId, project, revision]);
  const startAutoEdit = async (request: { musicAssetId: string; mood: 'energetic' | 'calm'; durationS: number; videoAssetIds: string[] }) => {
    if (!project || !timeline) return;
    setAutoEditError(null);
    autoEditJob.current = true;
    try {
      const result = await api<{ job_id: string }>(`/api/projects/${project.project_id}/auto-edit`, json('POST', {
        base_revision: revision, music_asset_id: request.musicAssetId, mood: request.mood,
        duration_s: request.durationS, video_asset_ids: request.videoAssetIds,
      }));
      setJob({ job_id: result.job_id, status: 'queued', progress: 0, message: 'Queued', output: null, report: null, error: null });
    } catch (e) { autoEditJob.current = false; setAutoEditError(e instanceof Error ? e.message : String(e)); }
  };
  const startRender = async (forPreview: boolean) => {
    if (!project || !readiness?.ok) return;
    previewJob.current = forPreview;
    setPreviewRender('');
    try {
      const result = await api<{ job_id: string }>(`/api/projects/${project.project_id}/render`, json('POST', { profile: forPreview ? 'preview_540p' : profile }));
      setJob({ job_id: result.job_id, status: 'queued', progress: 0, message: 'Queued', output: null, report: null, error: null });
    } catch (e) { setError(String(e)); }
  };
  const cancel = async () => { if (job) await api(`/api/jobs/${job.job_id}/cancel`, json('POST', {})); };
  const reveal = async () => { if (job?.output) await api('/api/reveal', json('POST', { path: job.output })); };
  const selectedClip = useMemo<TimelineClip | null>(() => timeline?.clips.find(clip => clip.clip_id === selectedClipId) ?? null, [timeline, selectedClipId]);
  const previewUrl = previewRender || (project && selectedAsset && ['video', 'image'].includes(selectedAsset.kind)
    ? selectedAsset.kind === 'image' ? thumbnailUrl(project.project_id, selectedAsset.asset_id) : `/api/projects/${project.project_id}/assets/${selectedAsset.asset_id}/proxy`
    : '');

  return <main className="app-shell">
    <header className="topbar"><div className="brand-mark">V</div><div><strong>Offline Editor</strong><small>Private workspace · This device</small></div>
      <span className="local-pill"><i /> LOCAL ONLY</span><button className="quiet" onClick={() => void refreshProjects()}>Refresh</button></header>
    <div className="workspace">
      <aside className="sidebar"><p className="eyebrow">WORKSPACE</p><h2>Projects</h2>
        <div className="create-row"><input value={name} onChange={e => setName(e.target.value)} aria-label="Project name"/><button onClick={() => void createProject()}>＋</button></div>
        <nav>{projects.map(item => <button key={item.project_id} className={`project-link ${project?.project_id === item.project_id ? 'active' : ''}`} onClick={() => void openProject(item)}><span className="folder-icon">▰</span>{item.name}</button>)}</nav>
        <div className="sidebar-note"><span>◉</span><p>Files stay on this computer.<br/>No uploads or cloud services.</p></div>
      </aside>
      <section className="main-column">
        {!project || !timeline ? <div className="empty-state"><div className="empty-icon">▧</div><h1>Your edit starts here</h1><p>Create a project or open one from the list.</p><button onClick={() => void createProject()}>Create project</button></div> : <>
          <div className="page-heading"><div><p className="eyebrow">PROJECT</p><h1>{project.name}</h1></div><button className="primary" onClick={() => void addMedia()}>＋ Add media</button></div>
          <div className="media-section"><div className="section-head"><div><h2>Media bin</h2><span>{assets.length} items</span></div><button className="quiet" onClick={() => void openProject(project)}>↻</button></div>
            <div className="media-grid">{assets.map(asset => <button key={asset.asset_id} className={`media-card ${selectedAsset?.asset_id === asset.asset_id ? 'chosen' : ''}`} draggable onDragStart={event => { event.dataTransfer.setData('application/x-editor-asset', asset.asset_id); event.dataTransfer.effectAllowed = 'copy'; }} onClick={() => { setSelectedAsset(asset); setPreviewRender(''); }}>
              <div className="thumb">{asset.thumbnail_count > 0 ? <img src={thumbnailUrl(project.project_id, asset.asset_id)} alt=""/> : <span>♫</span>}<b>{asset.kind.toUpperCase()}</b></div>
              <strong title={asset.file_name}>{asset.file_name}</strong><small>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : asset.kind} {asset.fps ? `· ${asset.fps.toFixed(1)} fps` : ''}</small>
              <div className="badges">{asset.hdr && <em>HDR</em>}{asset.rotation ? <em>ROTATED</em> : null}{asset.has_audio && <em>AUDIO</em>}</div>
            </button>)}</div>
            {!assets.length && <div className="drop-hint"><span>＋</span><strong>Your media will appear here</strong><small>Add videos, photos, and music to get started.</small><button className="quiet" onClick={() => void addMedia()}>Browse files</button></div>}
          </div>
          <div className="timeline-toolbar"><div><button className="quiet" onClick={() => setShowAutoEdit(true)} disabled={saveMessage !== 'Saved' || !!job && ['queued', 'running'].includes(job.status)}>Auto-edit…</button>
            <button className="quiet" onClick={undoEdit} disabled={!history || !canUndo(history)} title="Undo (Ctrl+Z)">Undo</button>
            <button className="quiet" onClick={redoEdit} disabled={!history || !canRedo(history)} title="Redo (Ctrl+Shift+Z / Ctrl+Y)">Redo</button></div>
            {autoEditError && <span className="error-text" role="alert">{autoEditError}</span>}
          </div>
          <TimelineEditor timeline={timeline} assets={assets} projectId={project.project_id} selectedId={selectedClipId} onSelect={setSelectedClipId} playhead={playhead} onPlayhead={seekTo} onChange={updateTimeline} issues={issues} error={timelineError} onError={setTimelineError} playing={playing} onTogglePlay={togglePlayback} timecode={formatTimecode(timeline, playhead)} onShortcuts={() => setShowShortcuts(true)} onRegisterZoomActions={registerZoomActions}/>
          <div className="editor-workbench">
            <section className="panel preview-panel"><div className="section-head"><div><h2>Preview</h2><span>{previewRender ? 'Quick preview render' : 'Rough preview — transitions, effects and titles show in Quick preview render'}</span></div><span className="panel-dot"/></div>
              {previewRender ? <video key={previewUrl} src={previewUrl} controls autoPlay/> : timeline.clips.some(clip => clip.track_id === 'video') ? <PreviewPlayer timeline={timeline} projectId={project.project_id} playhead={playhead} playing={playing} seekSerial={seekSerial}/> : previewUrl && selectedAsset?.kind !== 'image' ? <video key={previewUrl} src={previewUrl} controls/> : previewUrl ? <img className="still-preview" src={previewUrl} alt="Selected still"/> : <div className="preview-empty"><span>▶</span><p>Select a video to preview</p></div>}
              <audio ref={audioRef} src={playbackAudioUrl || undefined} preload="auto" />
              <button className="quiet preview-render" disabled={!readiness?.ok || checking || job?.status === 'running'} onClick={() => void startRender(true)}>Quick preview render</button>
            </section>
            <Inspector timeline={timeline} clip={selectedClip} catalogue={catalogue} onChange={updateTimeline} onCoalesceEnd={endCoalesce}/>
            <section className="panel export-panel"><div className="section-head"><div><h2>Export</h2><span>{checking ? 'Checking timeline…' : readiness?.ok ? 'Ready to export' : 'Resolve timeline issues'}</span></div><span className={`readiness-dot ${readiness?.ok ? 'ready' : ''}`}/></div>
              {!readiness?.ok && readiness?.error && <p className="readiness-error">{readiness.error}</p>}
              <p className={`save-state ${saveMessage === 'Saved' ? 'saved' : ''}`}>{saveMessage}</p>
              <label htmlFor="profile">Export profile</label><select id="profile" value={profile} onChange={e => setProfile(e.target.value)}>{profiles.map(p => <option key={p} value={p}>{p.replaceAll('_', ' ')}</option>)}</select>

              {/* Export advice */}
              {exportAdvice(assets, EXPORT_SIZES[profile] ?? EXPORT_SIZES.youtube_1080p).map((advice, index) => (
                <div key={index} className="export-advice">
                  <strong>{advice.code === 'orientation' ? 'Orientation:' : 'Resolution:'}</strong> {advice.message}
                  {advice.suggest && (
                    <button className="quiet" onClick={() => { if (advice.suggest) setProfile(advice.suggest); }}>
                      Switch to {advice.suggest.replaceAll('_', ' ')}
                    </button>
                  )}
                </div>
              ))}

              {!job || ['done', 'failed', 'cancelled'].includes(job.status) ? <button className="primary render-button" onClick={() => void startRender(false)} disabled={!readiness?.ok || checking}>{readiness?.ok ? 'Render video' : 'Resolve timeline issues'}</button> : <>
                <div className="job-line"><span>{job.message}</span><strong>{Math.round(job.progress * 100)}%</strong></div><div className="progress"><i style={{ width: `${job.progress * 100}%` }}/></div><button className="quiet cancel" onClick={() => void cancel()}>Cancel render</button>
              </>}
              {job?.status === 'done' && <div className="job-result"><strong className={job.report?.ok ? 'good' : 'bad'}>{job.report?.ok ? '✓ Render verified' : '⚠ Verification found issues'}</strong>{job.report?.problems.map(problem => <small key={problem}>{problem}</small>)}{job.timings?.total !== undefined && <small className="render-stats">Render time: {job.timings.total.toFixed(1)} s · preset {job.encoder?.preset ?? 'unknown'}</small>}<button className="quiet" onClick={() => void reveal()}>Show in folder ↗</button></div>}
              {job?.error && <p className="error-text">{job.error}</p>}
            </section>
          </div>
        </>}
        {error && !error.startsWith('Clip') && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError('')}>×</button></div>}
      </section>
    </div>
    {showAutoEdit && project && timeline && <AutoEditDialog assets={assets} hasClips={timeline.clips.length > 0}
      active={!!job && ['queued', 'running'].includes(job.status) && autoEditJob.current}
      progress={job?.progress ?? 0} message={job?.message ?? 'Queued'} error={autoEditError}
      onStart={request => void startAutoEdit(request)}
      onCancel={() => { if (job) void api(`/api/jobs/${job.job_id}/cancel`, json('POST', {})); }}
      onClose={() => { setShowAutoEdit(false); setAutoEditError(null); }}/ >}
    {showShortcuts && <div className="modal-backdrop" role="presentation" onClick={() => setShowShortcuts(false)}><section className="panel shortcuts-dialog" role="dialog" aria-modal="true" aria-labelledby="shortcuts-title" onClick={event => event.stopPropagation()}>
      <div className="section-head"><h2 id="shortcuts-title">Shortcuts</h2><button className="quiet" onClick={() => setShowShortcuts(false)}>Close</button></div>
      <dl>{[['Space', 'Play / pause'], ['← / →', 'Step one frame'], ['Shift + ← / →', 'Back / forward 1 second'], ['↑ / ↓', 'Previous / next edit point'], ['Home / End', 'Timeline start / end'], ['S', 'Split selected clip'], ['T', 'Add text clip'], ['Delete / Backspace', 'Delete selected clip'], ['Ctrl / ⌘ + Z', 'Undo'], ['Ctrl / ⌘ + Shift + Z or Ctrl + Y', 'Redo'], ['= / +', 'Zoom in'], ['-', 'Zoom out'], ['\\', 'Zoom to fit'], ['Ctrl / ⌘ + S', 'Save now']].map(([key, action]) => <React.Fragment key={key}><dt>{key}</dt><dd>{action}</dd></React.Fragment>)}</dl>
    </section></div>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
