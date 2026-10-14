import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, Asset, json, Project } from '../api';
import { LOOK_NAMES, STYLE_NAMES } from '../timeline/AutoEditDialog';
import type { AutoEditRequest, EditStyle } from '../timeline/AutoEditDialog';
import type { BriefResponse } from '../timeline/brief';
import type { Timeline } from '../timeline/ops';
import { clipNumbers, planRevision, RevisionResponse } from '../timeline/revise';
import {
  briefReady, briefReply, briefRequest, BriefSettings, ChatMessage, clipLinks, directorMessage, editReadyLine,
  forStorage, fromStorage, greeting, revisionReply, route, suggestions, tagNames, userMessage,
} from './chat';
import type { FootageStatus } from '../api';

export type AutoEditStatus = { status: string; progress: number; message: string; error: string | null } | null;

type Props = {
  project: Project;
  assets: Asset[];
  timeline: Timeline;
  autoEdit: AutoEditStatus;
  footage?: FootageStatus | null;
  jobBusy: boolean;
  onRevise: (text: string, timeline: Timeline) => Promise<RevisionResponse>;
  onBrief: (text: string) => Promise<BriefResponse>;
  onApply: (timeline: Timeline) => void;
  onUndo: () => void;
  onRedo: () => void;
  onStartAutoEdit: (request: AutoEditRequest, profile: string | null) => Promise<boolean>;
  onOpenAutoEdit: (mood?: 'energetic' | 'calm') => void;
  onSelectClip: (clipNumber: number) => void;
  onAddMedia: () => void;
};

const MAX_CHARS = 500;
const PROFILE_NAMES: Record<string, string> = {
  reels_9x16: 'Vertical 9:16', youtube_1080p: 'Landscape 16:9', square_1x1: 'Square 1:1', preview_540p: 'Preview 540p',
};
const TRANSITION_NAMES = { auto: 'Automatic', cuts_only: 'Cuts only', soft: 'Soft fades' } as const;

/** The Director as a conversation: ask, see what would change, then Apply — every change is undoable. */
export function ChatPanel(props: Props) {
  const { project, assets, timeline, autoEdit } = props;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [text, setText] = useState('');
  const [thinking, setThinking] = useState(false);
  const proposals = useRef(new Map<string, { timeline: Timeline; base: Timeline }>());
  const applied = useRef(new Map<string, Timeline>());
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const lastAutoEdit = useRef<string | null>(null);
  const clipCount = useMemo(() => clipNumbers(timeline).size, [timeline]);

  // load / save the conversation of this project
  useEffect(() => {
    let live = true;
    setLoaded(false); setMessages([]); proposals.current.clear(); applied.current.clear();
    api<{ messages: unknown }>(`/api/projects/${project.project_id}/chat`)
      .then(result => { if (live) { setMessages(fromStorage(result.messages)); setLoaded(true); } })
      .catch(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, [project.project_id]);
  useEffect(() => {
    if (!loaded) return;
    const timer = window.setTimeout(() => {
      void api(`/api/projects/${project.project_id}/chat`, json('PUT', { messages: forStorage(messages) })).catch(() => undefined);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [messages, loaded, project.project_id]);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, thinking]);

  const update = (id: string, change: Partial<ChatMessage>) =>
    setMessages(list => list.map(m => m.id === id ? { ...m, ...change } : m));
  const add = (...items: ChatMessage[]) => setMessages(list => [...list, ...items]);

  // follow the auto-edit job started from the chat
  useEffect(() => {
    if (!autoEdit) return;
    const key = `${autoEdit.status}`;
    if (lastAutoEdit.current === key) return;
    lastAutoEdit.current = key;
    if (autoEdit.status === 'queued' || autoEdit.status === 'running') return;
    setMessages(list => {
      const index = [...list].reverse().findIndex(m => m.kind === 'job' && m.job?.state === 'running');
      if (index < 0) return list;
      const at = list.length - 1 - index;
      const next = [...list];
      next[at] = autoEdit.status === 'done'
        ? { ...next[at], job: { state: 'done', detail: editReadyLine(timeline) } }
        : { ...next[at], job: { state: autoEdit.status === 'cancelled' ? 'cancelled' : 'failed', detail: autoEdit.error ?? autoEdit.message } };
      return next;
    });
  }, [autoEdit?.status]);

  const send = async (raw: string) => {
    const words = raw.trim().slice(0, MAX_CHARS);
    if (!words || thinking) return;
    setText('');
    add(userMessage(words));
    setThinking(true);
    try {
      if (route(words, timeline) === 'brief') {
        const previous = [...messages].reverse().find(m => m.kind === 'brief')?.brief;
        const reply = briefReply(await props.onBrief(words), assets, previous);
        add({ ...reply, ask: words });
      } else {
        const action = planRevision(await props.onRevise(words, timeline));
        if (action.kind === 'undo') props.onUndo();
        if (action.kind === 'redo') props.onRedo();
        const { message, timeline: proposed } = revisionReply(action);
        if (message) {
          if (proposed) proposals.current.set(message.id, { timeline: proposed, base: timeline });
          add({ ...message, ask: words });
        }
      }
    } catch (e) {
      add(directorMessage({ kind: 'error', lines: [e instanceof Error ? e.message : String(e)] }));
    } finally {
      setThinking(false);
      composer.current?.focus();
    }
  };

  const apply = (message: ChatMessage) => {
    const proposal = proposals.current.get(message.id);
    if (!proposal) return;
    props.onApply(proposal.timeline);
    applied.current.set(message.id, proposal.timeline);
    proposals.current.delete(message.id);
    update(message.id, { status: 'applied' });
  };
  const undoApplied = (message: ChatMessage) => {
    props.onUndo();
    applied.current.delete(message.id);
    update(message.id, { status: 'undone' });
  };
  const startEdit = async (message: ChatMessage, settings: BriefSettings) => {
    const started = await props.onStartAutoEdit(briefRequest(settings), settings.profile);
    if (!started) return;
    lastAutoEdit.current = null;
    update(message.id, { status: 'applied' });
    add(directorMessage({ kind: 'job', job: { state: 'running' } }));
  };
  const clearChat = () => {
    if (!window.confirm('Clear this conversation? Your edit stays as it is.')) return;
    proposals.current.clear(); applied.current.clear(); setMessages([]);
  };

  const rich = (line: string) => clipLinks(line, clipCount).map((segment, i) => 'clip' in segment
    ? <button key={i} className="clip-ref" title={`Select clip ${segment.clip}`} onClick={() => props.onSelectClip(segment.clip)}>{segment.text}</button>
    : <React.Fragment key={i}>{segment.text}</React.Fragment>);

  const renderDirector = (m: ChatMessage, isLast: boolean) => {
    const lines = m.lines ?? [];
    const body = lines.length > 1
      ? <ul>{lines.map((line, i) => <li key={i}>{rich(line)}</li>)}</ul>
      : lines.length ? <p>{rich(lines[0])}</p> : null;
    if (m.kind === 'proposal') {
      const proposal = proposals.current.get(m.id);
      const stale = !!proposal && proposal.base !== timeline;
      const canUndo = m.status === 'applied' && applied.current.get(m.id) === timeline;
      return <div className="change-card">
        <div className="change-card-head"><span className="change-icon">✦</span>Proposed change</div>
        {body}
        {m.status === 'pending' && proposal && !stale && <div className="card-actions">
          <button className="primary" onClick={() => apply(m)}>Apply</button>
          <button className="quiet" onClick={() => { proposals.current.delete(m.id); update(m.id, { status: 'discarded' }); }}>Discard</button>
        </div>}
        {m.status === 'pending' && (stale || !proposal) && <div className="card-actions">
          <span className="card-note">{stale ? 'The edit changed since I suggested this.' : 'This suggestion expired.'}</span>
          {m.ask && <button className="quiet" disabled={thinking} onClick={() => void send(m.ask!)}>Ask again</button>}
        </div>}
        {m.status === 'expired' && <div className="card-actions"><span className="card-note">Not applied (the editor was reloaded).</span>
          {m.ask && <button className="quiet" disabled={thinking} onClick={() => void send(m.ask!)}>Ask again</button>}</div>}
        {m.status === 'applied' && <div className="card-actions"><span className="card-done">✓ Applied</span>
          {canUndo && <button className="quiet" onClick={() => undoApplied(m)}>Undo</button>}</div>}
        {m.status === 'undone' && <div className="card-actions"><span className="card-note">Undone.</span></div>}
        {m.status === 'discarded' && <div className="card-actions"><span className="card-note">Discarded.</span></div>}
      </div>;
    }
    if (m.kind === 'brief' && m.brief) {
      const settings = m.brief;
      const songs = assets.filter(a => a.kind === 'audio' || a.kind === 'music');
      const ready = briefReady(settings);
      const change = (patch: Partial<BriefSettings>) => update(m.id, { brief: { ...settings, ...patch } });
      return <div className="change-card brief-card">
        <div className="change-card-head"><span className="change-icon">✦</span>New edit</div>
        {body}
        <dl className="brief-settings">
          <dt>Song</dt><dd>{m.status === 'pending' && songs.length > 1
            ? <select aria-label="Song" value={settings.musicAssetId ?? ''} onChange={e => change({ musicAssetId: e.target.value })}>
                {songs.map(s => <option key={s.asset_id} value={s.asset_id}>{s.file_name}</option>)}</select>
            : songs.find(s => s.asset_id === settings.musicAssetId)?.file_name ?? '—'}</dd>
          <dt>Length</dt><dd>{settings.durationS} s</dd>
          <dt>Mood</dt><dd className="cap">{settings.mood}</dd>
          <dt>Videos</dt><dd>{settings.videoAssetIds.length} of {assets.filter(a => a.kind === 'video').length}</dd>
          <dt>Transitions</dt><dd>{TRANSITION_NAMES[settings.transitionPolicy]}</dd>
          <dt>Style</dt><dd>{m.status === 'pending'
            ? <select aria-label="Style" value={settings.style ?? 'auto'} onChange={e => change({ style: e.target.value as EditStyle })}>
                {(Object.keys(STYLE_NAMES) as EditStyle[]).map(key => <option key={key} value={key}>{STYLE_NAMES[key]}</option>)}</select>
            : STYLE_NAMES[settings.style ?? 'auto']}</dd>
          {settings.look && <><dt>Look</dt><dd>{LOOK_NAMES[settings.look] ?? settings.look}</dd></>}
          {settings.transitionFamily && <><dt>Accents</dt><dd className="cap-first">{settings.transitionFamily} transitions</dd></>}
          {!!settings.preferTags?.length && <><dt>Shots</dt><dd className="cap-first">{tagNames(settings.preferTags)}</dd></>}
          {!!settings.avoidTags?.length && <><dt>Leave out</dt><dd className="cap-first">{tagNames(settings.avoidTags)}</dd></>}
          {settings.title && <><dt>Title</dt><dd>“{settings.title}”</dd></>}
          {settings.profile && <><dt>Format</dt><dd>{PROFILE_NAMES[settings.profile] ?? settings.profile}</dd></>}
        </dl>
        {m.text && <p className="card-question">{m.text}</p>}
        {m.status === 'pending' && <div className="card-actions">
          <button className="primary" disabled={!ready || props.jobBusy} onClick={() => void startEdit(m, settings)}>
            {clipCount ? 'Replace current edit' : 'Create edit'}</button>
          {!ready && !assets.some(a => a.kind === 'audio' || a.kind === 'music') && <button className="quiet" onClick={props.onAddMedia}>＋ Add media</button>}
          <button className="quiet" onClick={() => props.onOpenAutoEdit(settings.mood)}>More options…</button>
        </div>}
        {m.status === 'pending' && clipCount > 0 && <p className="card-note">You can undo it afterwards.</p>}
        {m.status === 'applied' && <div className="card-actions"><span className="card-done">✓ Started</span></div>}
        {m.status === 'expired' && <div className="card-actions"><span className="card-note">Not started.</span></div>}
      </div>;
    }
    if (m.kind === 'autoEdit') {
      return <div className="change-card">
        {body}
        {m.status === 'pending' && isLast && <div className="card-actions">
          <button className="primary" onClick={() => { update(m.id, { status: 'applied' }); props.onOpenAutoEdit(m.mood); }}>Open Auto-edit ({m.mood})</button>
        </div>}
      </div>;
    }
    if (m.kind === 'job') {
      const state = m.job?.state ?? 'running';
      return <div className={`job-card ${state}`}>
        {state === 'running' && <>
          <div className="job-line"><span>{autoEdit?.message ?? 'Starting…'}</span><strong>{Math.round((autoEdit?.progress ?? 0) * 100)}%</strong></div>
          <div className="progress"><i style={{ width: `${(autoEdit?.progress ?? 0) * 100}%` }}/></div>
        </>}
        {state === 'done' && <p>{m.job?.detail}</p>}
        {state === 'failed' && <p className="error-text">The edit failed: {m.job?.detail}</p>}
        {state === 'cancelled' && <p className="card-note">Cancelled.</p>}
        {state === 'interrupted' && <p className="card-note">This edit was still running when the editor closed.</p>}
      </div>;
    }
    return <div className={m.kind === 'question' ? 'reply-question' : m.kind === 'error' ? 'reply-error' : ''}>{body}</div>;
  };

  const ideas = suggestions(timeline, assets);
  return <section className="chat-panel" aria-label="Director">
    <header className="chat-head">
      <div><h2>Director</h2><span>{props.footage && props.footage.pending > 0
        ? `Watching your footage · ${props.footage.pending} video${props.footage.pending === 1 ? '' : 's'} to go`
        : 'Runs on this computer · nothing is uploaded'}</span></div>
      {messages.length > 0 && <button className="quiet small" onClick={clearChat}>Clear</button>}
    </header>
    <div className="chat-scroll" ref={scroller}>
      {!messages.length && loaded && <div className="msg director intro">{greeting(timeline, assets).map(line => <p key={line}>{line}</p>)}</div>}
      {messages.map((m, i) => <div key={m.id} className={`msg ${m.role}`}>
        {m.role === 'user' ? <p>{m.text}</p> : renderDirector(m, i === messages.length - 1)}
      </div>)}
      {thinking && <div className="msg director"><span className="thinking" aria-label="Thinking"><i/><i/><i/></span></div>}
    </div>
    <div className="composer">
      {ideas.length > 0 && !thinking && !text && <div className="ideas">{ideas.map(idea =>
        <button key={idea} className="idea" onClick={() => void send(idea)}>{idea}</button>)}</div>}
      <div className="composer-box">
        <textarea ref={composer} aria-label="Message the Director" rows={1} value={text} maxLength={MAX_CHARS}
          placeholder={clipCount ? 'Tell me what to change…' : 'Describe the video you want…'}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(text); } }}/>
        <button className="send" aria-label="Send" disabled={!text.trim() || thinking} onClick={() => void send(text)}>↑</button>
      </div>
      <p className="composer-hint">Enter to send · Shift+Enter for a new line · every change can be undone</p>
    </div>
  </section>;
}
