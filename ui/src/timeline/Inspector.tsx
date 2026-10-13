import React from 'react';
import { EditorCatalogue } from '../api';
import { Timeline, TimelineClip } from './ops';

type Props = { timeline: Timeline; clip: TimelineClip | null; catalogue: EditorCatalogue | null; onChange: (timeline: Timeline) => void };

export function Inspector({ timeline, clip, catalogue, onChange }: Props) {
  if (!clip) return <aside className="inspector panel"><h2>Inspector</h2><p>Select a clip to edit its settings.</p></aside>;
  const asset = timeline.assets.find(item => item.asset_id === clip.asset_id);
  const updateClip = (patch: Partial<TimelineClip>) => onChange({ ...timeline, clips: timeline.clips.map(item => item.clip_id === clip.clip_id ? { ...item, ...patch } : item) });
  const transition = (clip.transition ?? null) as { transition_id: string; duration_ticks: number; parameters?: Record<string, unknown> } | null;
  const effects = (clip.effects ?? []) as Array<{ effect_id: string; parameters: Record<string, unknown> }>;
  const audio = (clip.audio ?? { gain_db: 0, pan: 0, muted: false, fade_in_ms: 0, fade_out_ms: 0 }) as { gain_db: number; pan: number; muted: boolean; fade_in_ms: number; fade_out_ms: number };
  const transitionList = catalogue?.transitions.filter(item => item.renderable) ?? [];
  const setEffect = (index: number, params: Record<string, unknown>) => updateClip({ effects: effects.map((effect, i) => i === index ? { ...effect, parameters: params } : effect) });
  const addEffect = (effectId: string) => {
    const data = catalogue?.effects.find(item => item.effect_id === effectId);
    if (!data) return;
    const parameters = Object.fromEntries(data.parameter_schema.map(field => [field.name, field.default ?? field.minimum ?? 0]));
    updateClip({ effects: [...effects, { effect_id: effectId, parameters }] });
  };
  const setTransition = (id: string) => {
    if (!id || id === 'cut') { updateClip({ transition: null }); return; }
    const meta = transitionList.find(item => item.transition_id === id);
    if (!meta) return;
    const parameters = Object.fromEntries(meta.parameter_schema.map(field => [field.name, field.default ?? field.minimum ?? 0]));
    updateClip({ transition: { transition_id: id, duration_ticks: meta.duration_default_ticks ?? 500, parameters } });
  };
  const changeAudio = (patch: Partial<typeof audio>) => updateClip({ audio: { ...audio, ...patch } });
  const setText = (text: string) => onChange({ ...timeline, assets: timeline.assets.map(item => item.asset_id === clip.asset_id ? { ...item, text } : item) });
  const setTextY = (position_y: number) => updateClip({ transform: { ...(clip.transform as object ?? {}), position_y } });

  return <aside className="inspector panel">
    <h2>Inspector</h2><p className="inspector-subtitle">{asset?.kind === 'text' ? 'Text title' : asset?.kind ?? 'Clip'} · {clip.clip_id}</p>
    {asset?.kind === 'text' ? <section className="inspector-group"><h3>Text</h3>
      <label>Content<textarea value={String(asset.text ?? '')} onChange={event => setText(event.target.value)} rows={4} /></label>
      <label>Vertical position <input type="number" min="0" max="1" step="0.01" value={Number((clip.transform as any)?.position_y ?? 0)} onChange={event => setTextY(Number(event.target.value))} /></label>
    </section> : null}
    <section className="inspector-group"><h3>Transition in</h3>
      <label>Type<select value={transition?.transition_id ?? 'cut'} onChange={event => setTransition(event.target.value)}>
        {transitionList.map(item => <option key={item.transition_id} value={item.transition_id}>{item.name}</option>)}
      </select></label>
      {transition && transition.transition_id !== 'cut' && <label>Duration (ticks)<input type="number" min="1" max="30000" value={transition.duration_ticks} onChange={event => updateClip({ transition: { ...transition, duration_ticks: Number(event.target.value) } })} /></label>}
    </section>
    <section className="inspector-group"><div className="inspector-title"><h3>Effects</h3><select aria-label="Add effect" value="" onChange={event => addEffect(event.target.value)}><option value="">Add effect…</option>
      {catalogue?.effects.filter(item => item.renderable).map(item => <option key={item.effect_id} value={item.effect_id}>{item.name}</option>)}</select></div>
      {effects.map((effect, index) => {
        const metadata = catalogue?.effects.find(item => item.effect_id === effect.effect_id);
        return <div className="effect-editor" key={`${effect.effect_id}-${index}`}><div><strong>{metadata?.name ?? effect.effect_id}</strong><button className="icon-button" aria-label={`Remove ${effect.effect_id}`} onClick={() => updateClip({ effects: effects.filter((_, i) => i !== index) })}>×</button></div>
          {metadata?.parameter_schema.map(field => <label key={field.name}>{field.name}<input type="number" min={field.minimum ?? undefined} max={field.maximum ?? undefined} step="any" value={Number(effect.parameters[field.name] ?? 0)} onChange={event => setEffect(index, { ...effect.parameters, [field.name]: Number(event.target.value) })} /></label>)}
        </div>;
      })}
    </section>
    <section className="inspector-group"><h3>Audio</h3>
      <label>Gain (dB)<input type="number" step="0.5" value={audio.gain_db} onChange={event => changeAudio({ gain_db: Number(event.target.value) })} /></label>
      <label>Pan<input type="number" min="-1" max="1" step="0.1" value={audio.pan} onChange={event => changeAudio({ pan: Number(event.target.value) })} /></label>
      <label>Fade in (ms)<input type="number" min="0" value={audio.fade_in_ms} onChange={event => changeAudio({ fade_in_ms: Number(event.target.value) })} /></label>
      <label>Fade out (ms)<input type="number" min="0" value={audio.fade_out_ms} onChange={event => changeAudio({ fade_out_ms: Number(event.target.value) })} /></label>
      <label className="check-label"><input type="checkbox" checked={audio.muted} onChange={event => changeAudio({ muted: event.target.checked })} /> Mute this clip</label>
    </section>
  </aside>;
}
