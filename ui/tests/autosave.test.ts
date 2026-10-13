import { afterEach, describe, expect, it, vi } from 'vitest';
import { saveTimeline } from '../src/api';

describe('autosave conflict recovery', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reloads the server timeline when autosave receives a 409', async () => {
    const saved = { revision: 4, timeline: { duration_ticks: 2500, clips: [] } };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('timeline revision conflict', { status: 409 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await saveTimeline('p_12345678', 3, { duration_ticks: 3000 });
    expect(result).toEqual({ conflict: true, saved });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/projects/p_12345678/timeline');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'PUT' });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/projects/p_12345678/timeline');
  });
});
