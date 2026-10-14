/** Local Director brief → auto-edit form (pure; the parsing itself happens offline on the server). */
export type TransitionPolicy = 'auto' | 'cuts_only' | 'soft';
export type Mood = 'energetic' | 'calm';

export type BriefResponse = {
  brief: {
    duration_s: number | null; aspect: string | null; profile: string | null; mood: Mood | null;
    transition_policy: TransitionPolicy; title: string | null;
    only_asset_ids: string[]; exclude_asset_ids: string[];
  };
  understood: string[]; defaults: string[]; questions: string[]; unknown_words: string[];
  video_asset_ids: string[];
};

export type AutoEditForm = {
  mood: Mood; durationS: number; videoIds: string[]; transitionPolicy: TransitionPolicy; title: string;
};

/**
 * Fill the form from a parsed brief. Values the brief does not give keep what the user had.
 * Returns the new form, the export profile to switch to (if the brief named a format) and the
 * lines to show the user.
 */
export function applyBrief(form: AutoEditForm, response: BriefResponse): {
  form: AutoEditForm; profile: string | null; notes: string[]; questions: string[];
} {
  const b = response.brief;
  const next: AutoEditForm = {
    mood: b.mood ?? form.mood,
    durationS: b.duration_s ?? form.durationS,
    videoIds: response.video_asset_ids.length ? [...response.video_asset_ids] : [...form.videoIds],
    transitionPolicy: b.transition_policy === 'auto' ? form.transitionPolicy : b.transition_policy,
    title: b.title ?? form.title,
  };
  const notes = [...response.understood, ...response.defaults];
  if (response.unknown_words.length) notes.push(`Not used: ${response.unknown_words.join(', ')}`);
  const questions = [...response.questions];
  if (!response.video_asset_ids.length && (b.only_asset_ids.length || b.exclude_asset_ids.length)) {
    questions.push('That leaves no videos; kept your current selection.');
  }
  return { form: next, profile: b.profile, notes, questions };
}
