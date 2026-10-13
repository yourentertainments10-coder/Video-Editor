import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, Asset, EditorCatalogue, Job, json, Project, saveTimeline, thumbnailUrl } from './api';
import { Inspector } from './timeline/Inspector';
import { TimelineEditor } from './timeline/TimelineEditor';
import { Timeline, TimelineClip } from './timeline/ops';
import { AutoEditDialog } from './timeline/AutoEditDialog';
import { canRedo, canUndo, createHistory, History, push, redo, undo } from './timeline/history';
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
  const [profile, setProfile] = useState('preview_540p');
  const [job, setJob] = useState<Job | null>(null);
  const [previewRender, setPreviewRender] = useState('');
  const [name, setName] = useState('My project');
  const [error, setError] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [revision, setRevision] = useState(1);
  const [issues, setIssues] = useState<Array<{ code: string; message: string; clip_id?: string }>>([]);
  const [readiness, setReadiness] = useState<{ ok: boolean; error: string | null } | null>(null);
  const [checking, setChecking] = useState(false);
  const [showAutoEdit, setShowAutoEdit] = useState(false);
  const [autoEditError, setAutoEditError] = useState<string | null>(null);
  const [history, setHistory] = useState<History<Timeline> | null>(null);
  const dirty = useRef(false);
  const previewJob = useRef(false);
  const autoEditJob = useRef(false);
  const historyRef = useRef<History<Timeline> | null>(null);

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
    }, 800);
    return () => window.clearTimeout(timer);
  }, [timeline, project, revision]);

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
  const updateTimeline = (next: Timeline) => {
    dirty.current = true;
    const prior = historyRef.current;
    if (prior) { const nextHistory = push(prior, next); historyRef.current = nextHistory; setHistory(nextHistory); }
    setTimeline(next);
  };
  const travelHistory = (move: (value: History<Timeline>) => History<Timeline>) => {
    const prior = historyRef.current;
    if (!prior) return;
    const next = move(prior);
    if (next === prior) return;
    historyRef.current = next; setHistory(next); dirty.current = true; setTimeline(next.present);
  };
  const undoEdit = () => travelHistory(undo);
  const redoEdit = () => travelHistory(redo);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.matches('input,textarea,select,[contenteditable=true]')) return;
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === 'z') {
        event.preventDefault(); event.shiftKey ? redoEdit() : undoEdit();
      } else if (mod && event.key.toLowerCase() === 'y') { event.preventDefault(); redoEdit(); }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  });
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
          <TimelineEditor timeline={timeline} assets={assets} projectId={project.project_id} selectedId={selectedClipId} onSelect={setSelectedClipId} playhead={playhead} onPlayhead={setPlayhead} onChange={updateTimeline} issues={issues} error={error.startsWith('Clip') ? error : ''} onError={setError}/>
          <div className="editor-workbench">
            <section className="panel preview-panel"><div className="section-head"><div><h2>Preview</h2><span>{previewRender ? 'Quick preview render' : selectedAsset?.file_name ?? 'Select a media item'}</span></div><span className="panel-dot"/></div>
              {previewUrl && selectedAsset?.kind !== 'image' ? <video key={previewUrl} src={previewUrl} controls autoPlay={!!previewRender}/> : previewUrl ? <img className="still-preview" src={previewUrl} alt="Selected still"/> : <div className="preview-empty"><span>▶</span><p>Select a video to preview</p></div>}
              <button className="quiet preview-render" disabled={!readiness?.ok || checking || job?.status === 'running'} onClick={() => void startRender(true)}>Quick preview render</button>
            </section>
            <Inspector timeline={timeline} clip={selectedClip} catalogue={catalogue} onChange={updateTimeline}/>
            <section className="panel export-panel"><div className="section-head"><div><h2>Export</h2><span>{checking ? 'Checking timeline…' : 'Ready to export'}</span></div><span className={`readiness-dot ${readiness?.ok ? 'ready' : ''}`}/></div>
              {!readiness?.ok && readiness?.error && <p className="readiness-error">{readiness.error}</p>}
              <p className={`save-state ${saveMessage === 'Saved' ? 'saved' : ''}`}>{saveMessage}</p>
              <label htmlFor="profile">Export profile</label><select id="profile" value={profile} onChange={e => setProfile(e.target.value)}>{profiles.map(p => <option key={p} value={p}>{p.replaceAll('_', ' ')}</option>)}</select>
              {!job || ['done', 'failed', 'cancelled'].includes(job.status) ? <button className="primary render-button" onClick={() => void startRender(false)} disabled={!readiness?.ok || checking}>{readiness?.ok ? 'Render video' : 'Resolve timeline issues'}</button> : <>
                <div className="job-line"><span>{job.message}</span><strong>{Math.round(job.progress * 100)}%</strong></div><div className="progress"><i style={{ width: `${job.progress * 100}%` }}/></div><button className="quiet cancel" onClick={() => void cancel()}>Cancel render</button>
              </>}
              {job?.status === 'done' && <div className="job-result"><strong className={job.report?.ok ? 'good' : 'bad'}>{job.report?.ok ? '✓ Render verified' : '⚠ Verification found issues'}</strong>{job.report?.problems.map(problem => <small key={problem}>{problem}</small>)}<button className="quiet" onClick={() => void reveal()}>Show in folder ↗</button></div>}
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
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
