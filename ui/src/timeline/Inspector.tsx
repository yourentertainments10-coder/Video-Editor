import React from 'react';
import { EditorCatalogue } from '../api';
import { TITLE_ANIMATION_NAMES } from './AutoEditDialog';
import { Timeline, TimelineClip } from './ops';

type Props = { timeline: Timeline; clip: TimelineClip | null; catalogue: EditorCatalogue | null;
  onChange: (timeline: Timeline, historyKey?: string) => void; onCoalesceEnd?: (historyKey: string) => void };

export function Inspector({ timeline, clip, catalogue, onChange, onCoalesceEnd }: Props) {
  if (!clip) return <aside className="inspector panel"><h2>Inspector</h2><p>Select a clip to edit its settings.</p></aside>;
  const asset = timeline.assets.find(item => item.asset_id === clip.asset_id);
  const fieldKey = (field: string) => `${clip.clip_id}:${field}`;
  const fieldBlur = (field: string) => ({ onBlur: () => onCoalesceEnd?.(fieldKey(field)) });
  const updateClip = (patch: Partial<TimelineClip>, field?: string) => onChange({ ...timeline, clips: timeline.clips.map(item => item.clip_id === clip.clip_id ? { ...item, ...patch } : item) }, field ? fieldKey(field) : undefined);
  const transition = (clip.transition ?? null) as { transition_id: string; duration_ticks: number; parameters?: Record<string, unknown> } | null;
  const effects = (clip.effects ?? []) as Array<{ effect_id: string; parameters: Record<string, unknown> }>;
  const audio = (clip.audio ?? { gain_db: 0, pan: 0, muted: false, fade_in_ms: 0, fade_out_ms: 0 }) as { gain_db: number; pan: number; muted: boolean; fade_in_ms: number; fade_out_ms: number };
  const transitionList = catalogue?.transitions.filter(item => item.renderable) ?? [];
  const setEffect = (index: number, params: Record<string, unknown>, field: string) => updateClip({ effects: effects.map((effect, i) => i === index ? { ...effect, parameters: params } : effect) }, field);
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
  const changeAudio = (patch: Partial<typeof audio>, field: string) => updateClip({ audio: { ...audio, ...patch } }, field);
  const setText = (text: string) => onChange({ ...timeline, assets: timeline.assets.map(item => item.asset_id === clip.asset_id ? { ...item, text } : item) }, fieldKey('text'));
  const titleStyle = ((asset?.metadata as Record<string, unknown> | undefined)?.title_style ?? {}) as Record<string, unknown>;
  const setTitleStyle = (patch: Record<string, unknown>, field?: string) => onChange({ ...timeline, assets: timeline.assets.map(item => item.asset_id === clip.asset_id
    ? { ...item, metadata: { ...(item.metadata ?? {}), title_style: { ...titleStyle, ...patch, by: 'user' } } } : item) }, field ? fieldKey(field) : undefined);
  const setTextY = (position_y: number) => updateClip({ transform: { ...(clip.transform as object ?? {}), position_y } }, 'text-y');

  return <aside className="inspector panel">
    <h2>Inspector</h2><p className="inspector-subtitle">{asset?.kind === 'text' ? 'Text title' : asset?.kind ?? 'Clip'} · {clip.clip_id}</p>
    {asset?.kind === 'text' ? <section className="inspector-group"><h3>Text</h3>
      <label>Content<textarea value={String(asset.text ?? '')} onChange={event => setText(event.target.value)} rows={4} {...fieldBlur('text')} /></label>
      <label>Animation<select aria-label="Title animation" value={String(titleStyle.animation ?? 'fade')} onChange={event => setTitleStyle({ animation: event.target.value })}>
        {Object.entries(TITLE_ANIMATION_NAMES).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
      </select></label>
      <label>Colour<input type="color" aria-label="Title colour" value={String(titleStyle.color ?? '#ffffff')} onChange={event => setTitleStyle({ color: event.target.value.toUpperCase() }, 'title-color')} {...fieldBlur('title-color')} /></label>
      <label>Vertical position <input type="number" min="0" max="1" step="0.01" value={Number((clip.transform as any)?.position_y ?? 0)} onChange={event => setTextY(Number(event.target.value))} {...fieldBlur('text-y')} /></label>
    </section> : null}
    <section className="inspector-group"><h3>Transition in</h3>
      <label>Type<select value={transition?.transition_id ?? 'cut'} onChange={event => setTransition(event.target.value)}>
        {transitionList.map(item => <option key={item.transition_id} value={item.transition_id}>{item.name}</option>)}
      </select></label>
      {transition && transition.transition_id !== 'cut' && <label>Duration (ticks)<input type="number" min="1" max="30000" value={transition.duration_ticks} onChange={event => updateClip({ transition: { ...transition, duration_ticks: Number(event.target.value) } }, 'transition-duration')} /></label>}
    </section>
    <section className="inspector-group"><div className="inspector-title"><h3>Effects</h3><select aria-label="Add effect" value="" onChange={event => addEffect(event.target.value)}><option value="">Add effect…</option>
      {catalogue?.effects.filter(item => item.renderable).map(item => <option key={item.effect_id} value={item.effect_id}>{item.name}</option>)}</select></div>
      {effects.map((effect, index) => {
        const metadata = catalogue?.effects.find(item => item.effect_id === effect.effect_id);
        return <div className="effect-editor" key={`${effect.effect_id}-${index}`}><div><strong>{metadata?.name ?? effect.effect_id}</strong><button className="icon-button" aria-label={`Remove ${effect.effect_id}`} onClick={() => updateClip({ effects: effects.filter((_, i) => i !== index) })}>×</button></div>
          {metadata?.parameter_schema.map(field => <label key={field.name}>{field.name}<input type="number" min={field.minimum ?? undefined} max={field.maximum ?? undefined} step="any" value={Number(effect.parameters[field.name] ?? 0)} onChange={event => setEffect(index, { ...effect.parameters, [field.name]: Number(event.target.value) }, `effect-${index}-${field.name}`)} {...fieldBlur(`effect-${index}-${field.name}`)} /></label>)}
        </div>;
      })}
    </section>
    <section className="inspector-group"><h3>Audio</h3>
      <label>Gain (dB)<input type="number" step="0.5" value={audio.gain_db} onChange={event => changeAudio({ gain_db: Number(event.target.value) }, 'audio-gain')} {...fieldBlur('audio-gain')} /></label>
      <label>Pan<input type="number" min="-1" max="1" step="0.1" value={audio.pan} onChange={event => changeAudio({ pan: Number(event.target.value) }, 'audio-pan')} {...fieldBlur('audio-pan')} /></label>
      <label>Fade in (ms)<input type="number" min="0" value={audio.fade_in_ms} onChange={event => changeAudio({ fade_in_ms: Number(event.target.value) }, 'audio-fade-in')} {...fieldBlur('audio-fade-in')} /></label>
      <label>Fade out (ms)<input type="number" min="0" value={audio.fade_out_ms} onChange={event => changeAudio({ fade_out_ms: Number(event.target.value) }, 'audio-fade-out')} {...fieldBlur('audio-fade-out')} /></label>
      <label className="check-label"><input type="checkbox" checked={audio.muted} onChange={event => changeAudio({ muted: event.target.checked }, 'audio-muted')} /> Mute this clip</label>
    </section>
  </aside>;
}
