export type Project = { project_id: string; name: string };
export type Asset = {
  asset_id: string; kind: string; file_name: string; duration_ms: number | null;
  width: number | null; height: number | null; fps: number | null; has_audio: boolean;
  rotation: number; hdr: boolean; thumbnail_count: number;
  text?: string; uri?: string | null; metadata?: Record<string, unknown>;
};
export type Job = {
  job_id: string; status: string; progress: number; message: string; output: string | null;
  report: { ok: boolean; problems: string[]; measured: Record<string, unknown> } | null; error: string | null;
  encoder?: { crf: number; preset: string; audio_bitrate: string } | null;
  timings?: Record<string, number> | null;
};
export type CatalogueField = {
  name: string; value_type: string; required: boolean; default: string | number | null;
  minimum: number | null; maximum: number | null;
};
export type CatalogueItem = {
  transition_id?: string; effect_id?: string; name: string; renderable: boolean;
  parameter_schema: CatalogueField[]; duration_default_ticks?: number;
};
export type EditorCatalogue = { transitions: CatalogueItem[]; effects: CatalogueItem[] };
export type TimelineIssue = { code: string; message: string; clip_id?: string };
export type SavedTimeline = { revision: number; timeline: any };
export type SaveResult = { conflict: false; revision: number; issues: TimelineIssue[] }
  | { conflict: true; saved: SavedTimeline };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError'; }
}

export function editorToken(): string {
  return globalThis.document?.querySelector<HTMLMetaElement>('meta[name="editor-token"]')?.content ?? '';
}

export async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('X-Editor-Token', editorToken());
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) throw new ApiError(response.status, `${response.status}: ${await response.text() || response.statusText}`);
  return response.json() as Promise<T>;
}

export const json = (method: string, value?: unknown): RequestInit => ({
  method, ...(value === undefined ? {} : { body: JSON.stringify(value) }),
});

export async function saveTimeline(pid: string, baseRevision: number, timeline: unknown): Promise<SaveResult> {
  const response = await fetch(`/api/projects/${pid}/timeline`, {
    method: 'PUT', credentials: 'same-origin',
    headers: { 'X-Editor-Token': editorToken(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_revision: baseRevision, timeline }),
  });
  if (response.status === 409) {
    const saved = await api<SavedTimeline>(`/api/projects/${pid}/timeline`);
    return { conflict: true, saved };
  }
  if (!response.ok) throw new ApiError(response.status, `${response.status}: ${await response.text() || response.statusText}`);
  const body = await response.json() as { revision: number; issues?: TimelineIssue[] };
  return { conflict: false, revision: body.revision, issues: body.issues ?? [] };
}

export const assetProxyUrl = (pid: string, aid: string) => `/api/projects/${pid}/assets/${aid}/proxy`;
export const assetAudioUrl = (pid: string, aid: string) => `/api/projects/${pid}/assets/${aid}/audio`;
export const thumbnailUrl = (pid: string, aid: string, index = 0) => `/api/projects/${pid}/assets/${aid}/thumbnails/${index}`;
