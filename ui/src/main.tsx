import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, Asset, Job, json, mediaBlobUrl, Project } from './api';
import './style.css';

const profiles = ['preview_540p', 'youtube_1080p', 'reels_9x16', 'square_1x1'];

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [preview, setPreview] = useState('');
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [profile, setProfile] = useState('preview_540p');
  const [job, setJob] = useState<Job | null>(null);
  const [name, setName] = useState('My project');
  const [error, setError] = useState('');

  const refreshProjects = async () => setProjects((await api<{ projects: Project[] }>('/api/projects')).projects);
  const openProject = async (value: Project) => {
    setProject(value); setSelected(null); setJob(null); setError('');
    const result = await api<{ assets: Asset[] }>(`/api/projects/${value.project_id}/assets`);
    setAssets(result.assets);
  };
  useEffect(() => { void refreshProjects().catch(e => setError(String(e))); }, []);
  useEffect(() => {
    if (!project || !selected || selected.kind === 'audio' || selected.kind === 'music') { setPreview(''); return; }
    let live = true;
    const url = selected.kind === 'image'
      ? `/api/projects/${project.project_id}/assets/${selected.asset_id}/thumbnails/0`
      : `/api/projects/${project.project_id}/assets/${selected.asset_id}/proxy`;
    void mediaBlobUrl(url).then(blob => { if (live) setPreview(blob); else URL.revokeObjectURL(blob); })
      .catch(e => setError(String(e)));
    return () => { live = false; setPreview(current => { if (current) URL.revokeObjectURL(current); return ''; }); };
  }, [project, selected]);
  useEffect(() => {
    if (!project) return;
    let live = true;
    const load = async () => {
      const pairs = await Promise.all(assets.filter(a => a.thumbnail_count > 0).map(async asset => {
        if (thumbs[asset.asset_id]) return [asset.asset_id, thumbs[asset.asset_id]] as const;
        const url = `/api/projects/${project.project_id}/assets/${asset.asset_id}/thumbnails/0`;
        return [asset.asset_id, await mediaBlobUrl(url)] as const;
      }));
      if (live) setThumbs(current => ({ ...current, ...Object.fromEntries(pairs) }));
      else pairs.forEach(([, url]) => { if (!Object.values(thumbs).includes(url)) URL.revokeObjectURL(url); });
    };
    void load().catch(() => undefined);
    return () => { live = false; };
  }, [project, assets]);
  useEffect(() => {
    if (!job || !project || !['queued', 'running'].includes(job.status)) return;
    const timer = window.setTimeout(() => {
      void api<Job>(`/api/jobs/${job.job_id}`).then(setJob).catch(e => setError(String(e)));
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
  const startRender = async () => {
    if (!project) return;
    try {
      const result = await api<{ job_id: string }>(`/api/projects/${project.project_id}/render`, json('POST', { profile }));
      setJob({ job_id: result.job_id, status: 'queued', progress: 0, message: 'Queued', output: null, report: null, error: null });
    } catch (e) { setError(String(e)); }
  };
  const cancel = async () => { if (job) await api(`/api/jobs/${job.job_id}/cancel`, json('POST', {})); };
  const reveal = async () => { if (job?.output) await api('/api/reveal', json('POST', { path: job.output })); };

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
        {!project ? <div className="empty-state"><div className="empty-icon">▧</div><h1>Your edit starts here</h1><p>Create a project or open one from the list.</p><button onClick={() => void createProject()}>Create project</button></div> : <>
          <div className="page-heading"><div><p className="eyebrow">PROJECT</p><h1>{project.name}</h1></div><button className="primary" onClick={() => void addMedia()}>＋ Add media</button></div>
          <div className="media-section"><div className="section-head"><div><h2>Media bin</h2><span>{assets.length} items</span></div><button className="quiet" onClick={() => void openProject(project)}>↻</button></div>
            <div className="media-grid">{assets.map(asset => <button key={asset.asset_id} className={`media-card ${selected?.asset_id === asset.asset_id ? 'chosen' : ''}`} onClick={() => setSelected(asset)}>
              <div className="thumb">{thumbs[asset.asset_id] ? <img src={thumbs[asset.asset_id]} /> : <span>{asset.kind === 'audio' || asset.kind === 'music' ? '♫' : '▧'}</span>}<b>{asset.kind.toUpperCase()}</b></div>
              <strong title={asset.file_name}>{asset.file_name}</strong><small>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : asset.kind} {asset.fps ? `· ${asset.fps.toFixed(1)} fps` : ''}</small>
              <div className="badges">{asset.hdr && <em>HDR</em>}{asset.rotation ? <em>ROTATED</em> : null}{asset.has_audio && <em>AUDIO</em>}</div>
            </button>)}</div>
            {!assets.length && <div className="drop-hint"><span>＋</span><strong>Your media will appear here</strong><small>Add videos, photos, and music to get started.</small><button className="quiet" onClick={() => void addMedia()}>Browse files</button></div>}
          </div>
          <div className="lower-grid"><section className="panel preview-panel"><div className="section-head"><div><h2>Preview</h2><span>{selected?.file_name ?? 'Select a media item'}</span></div><span className="panel-dot" /></div>
            {preview && selected?.kind === 'video' ? <video key={preview} src={preview} controls /> : preview ? <img className="still-preview" src={preview} /> : <div className="preview-empty"><span>▶</span><p>Select a video to preview</p></div>}
          </section>
          <section className="panel export-panel"><div className="section-head"><div><h2>Export</h2><span>Render your current timeline</span></div><span className="export-symbol">⇧</span></div>
            <label htmlFor="profile">Export profile</label><select id="profile" value={profile} onChange={e => setProfile(e.target.value)}>{profiles.map(p => <option key={p} value={p}>{p.replaceAll('_', ' ')}</option>)}</select>
            {!job || job.status === 'done' || job.status === 'failed' || job.status === 'cancelled' ? <button className="primary render-button" onClick={() => void startRender()} disabled={!assets.length}>Render video</button> : <>
              <div className="job-line"><span>{job.message}</span><strong>{Math.round(job.progress * 100)}%</strong></div><div className="progress"><i style={{ width: `${job.progress * 100}%` }} /></div><button className="quiet cancel" onClick={() => void cancel()}>Cancel render</button>
            </>}
            {job?.status === 'done' && <div className="job-result"><strong className={job.report?.ok ? 'good' : 'bad'}>{job.report?.ok ? '✓ Render verified' : '⚠ Verification found issues'}</strong>{job.report?.problems.map(problem => <small key={problem}>{problem}</small>)}<button className="quiet" onClick={() => void reveal()}>Show in folder ↗</button></div>}
            {job?.error && <p className="error-text">{job.error}</p>}
          </section></div>
        </>}
        {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError('')}>×</button></div>}
      </section>
    </div>
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
