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

/** Must equal API_LEVEL in poc/server/app.py (checked by tests/test_polish.py). */
export const API_LEVEL = 3;

/** Turn an error response into a readable sentence (FastAPI sends {"detail": ...}). */
export function errorMessage(status: number, body: string, statusText = ''): string {
  let detail = body.trim();
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed.detail === 'string') detail = parsed.detail;
    else if (parsed && Array.isArray(parsed.detail)) detail = parsed.detail.map((d: { msg?: string }) => d.msg ?? '').join('; ');
  } catch { /* plain text body */ }
  if (status === 404 && (!detail || detail === 'Not Found')) {
    return 'The editor server does not know this action. Close the editor window and start it again (it is older than this page).';
  }
  if (status === 401) return 'This page lost its connection key. Reload the page.';
  return `${detail || statusText || 'Request failed'} (${status})`;
}

/** Problem text when the running server is older/newer than this page, else null. */
export function apiLevelProblem(serverLevel: unknown): string | null {
  if (serverLevel === API_LEVEL) return null;
  if (typeof serverLevel !== 'number' || serverLevel < API_LEVEL) {
    return 'The editor server is older than this page. Close the editor (or its command window) and start it again to use the newest features.';
  }
  return 'This page is older than the editor server. Reload the page (Ctrl+F5).';
}

export function editorToken(): string {
  return globalThis.document?.querySelector<HTMLMetaElement>('meta[name="editor-token"]')?.content ?? '';
}

export async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('X-Editor-Token', editorToken());
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) throw new ApiError(response.status, errorMessage(response.status, await response.text(), response.statusText));
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
  if (!response.ok) throw new ApiError(response.status, errorMessage(response.status, await response.text(), response.statusText));
  const body = await response.json() as { revision: number; issues?: TimelineIssue[] };
  return { conflict: false, revision: body.revision, issues: body.issues ?? [] };
}

export const assetProxyUrl = (pid: string, aid: string) => `/api/projects/${pid}/assets/${aid}/proxy`;
export const assetAudioUrl = (pid: string, aid: string) => `/api/projects/${pid}/assets/${aid}/audio`;
export const thumbnailUrl = (pid: string, aid: string, index = 0) => `/api/projects/${pid}/assets/${aid}/thumbnails/${index}`;
