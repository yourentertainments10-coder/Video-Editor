import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

describe('API authentication', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('attaches the local editor token header', async () => {
    vi.stubGlobal('document', { querySelector: () => ({ content: 'local-secret' }) });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal('fetch', fetchMock);
    await api('/api/health');
    expect(fetchMock.mock.calls[0][1].headers.get('X-Editor-Token')).toBe('local-secret');
  });
});
