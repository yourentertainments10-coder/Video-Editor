import React, { useState } from 'react';
import { Timeline } from './ops';
import { planRevision, RevisionAction, RevisionResponse } from './revise';

type Props = {
  timeline: Timeline; disabled: boolean;
  onAsk: (text: string, timeline: Timeline) => Promise<RevisionResponse>;
  onApply: (timeline: Timeline) => void; onUndo: () => void; onRedo: () => void;
  onAutoEdit: (mood: 'energetic' | 'calm') => void;
};

/** "Tell the editor what to change": preview a written change, then Apply or Discard. */
export function ReviseBar({ timeline, disabled, onAsk, onApply, onUndo, onRedo, onAutoEdit }: Props) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState<RevisionAction | null>(null);
  const [base, setBase] = useState<Timeline | null>(null);

  const ask = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const plan = planRevision(await onAsk(text, timeline));
      if (plan.kind === 'undo') { onUndo(); setAction({ kind: 'info', lines: ['Undone.'], isQuestion: false }); setText(''); }
      else if (plan.kind === 'redo') { onRedo(); setAction({ kind: 'info', lines: ['Redone.'], isQuestion: false }); setText(''); }
      else { setAction(plan); setBase(timeline); }
    } catch (e) {
      setAction({ kind: 'info', lines: [e instanceof Error ? e.message : String(e)], isQuestion: true });
    } finally { setBusy(false); }
  };
  const stale = action?.kind === 'preview' && base !== timeline;

  return <div className="revise-bar">
    <div className="revise-input">
      <input aria-label="Tell the editor what to change" value={text} disabled={disabled || busy}
        placeholder="Tell the editor what to change, e.g. remove clip 3 · add fades · make clip 2 brighter · title 'Goa' at the end"
        onChange={event => setText(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); void ask(); } }}/>
      <button className="quiet" disabled={disabled || busy || !text.trim()} onClick={() => void ask()}>{busy ? 'Reading…' : 'Preview'}</button>
    </div>
    {action && action.kind !== 'undo' && action.kind !== 'redo' && <div className={`revise-result ${action.kind === 'info' && action.isQuestion ? 'question' : ''}`} role="status">
      <ul>{action.lines.map(line => <li key={line}>{line}</li>)}</ul>
      {stale && <p className="revise-stale">The timeline changed since this preview; preview again.</p>}
      <div className="revise-actions">
        {action.kind === 'preview' && <button className="primary" disabled={stale}
          onClick={() => { onApply(action.timeline); setAction(null); setText(''); }}>Apply</button>}
        {action.kind === 'autoEdit' && <button className="primary"
          onClick={() => { onAutoEdit(action.mood); setAction(null); setText(''); }}>Open Auto-edit ({action.mood})</button>}
        <button className="quiet" onClick={() => setAction(null)}>{action.kind === 'info' ? 'OK' : 'Discard'}</button>
      </div>
    </div>}
  </div>;
}
