import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Dev server runs at http://localhost:5173 (vite default).
// We mirror the nginx prod proxy routes so the same fetch URLs work in dev:
//   GET /api/core/healthz  → core-api:8000/healthz
//   GET /api/ai/healthz    → ai-service:8100/healthz
// When running 'npm run dev' against `make up` infrastructure, point dev
// proxies at localhost ports (the host-side mapping in podman-compose.yml).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api/core': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/core/, ''),
      },
      '/api/ai': {
        target: 'http://localhost:8100',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/ai/, ''),
        // SSE preservation in vite dev:
        ws: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 800,
  },
});
