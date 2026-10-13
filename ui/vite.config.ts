import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_');
  return {
    base: '/static/',
    plugins: [react()],
    server: { proxy: { '/api': env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8765' } },
    test: { environment: 'node' },
  };
});
