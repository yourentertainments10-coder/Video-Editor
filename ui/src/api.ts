export type Project = { project_id: string; name: string };
export type Asset = {
  asset_id: string; kind: string; file_name: string; duration_ms: number | null;
  width: number | null; height: number | null; fps: number | null; has_audio: boolean;
  rotation: number; hdr: boolean; thumbnail_count: number;
};
export type Job = {
  job_id: string; status: string; progress: number; message: string; output: string | null;
  report: { ok: boolean; problems: string[]; measured: Record<string, unknown> } | null; error: string | null;
};

export function editorToken(): string {
  return globalThis.document?.querySelector<HTMLMetaElement>('meta[name="editor-token"]')?.content ?? '';
}

export async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('X-Editor-Token', editorToken());
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail || response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const json = (method: string, value?: unknown): RequestInit => ({
  method, ...(value === undefined ? {} : { body: JSON.stringify(value) }),
});

export async function mediaBlobUrl(url: string): Promise<string> {
  const response = await fetch(url, { headers: { 'X-Editor-Token': editorToken() } });
  if (!response.ok) throw new Error(`Media request failed (${response.status})`);
  return URL.createObjectURL(await response.blob());
}
