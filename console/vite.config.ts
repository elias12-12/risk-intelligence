/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Decision 9: a dev PROXY, not a CORS allowlist.
//
// The bundle is served same-origin from FastAPI in the end (`glassbox serve`
// mounts `console/dist` when it exists), so CORS never becomes a production
// surface. A dev-only allowlist would be a second origin policy that exists
// only on developer machines, and `*` would be a third — one nobody would
// remember to remove.
export default defineConfig({
  plugins: [react()],
  // The build is served from /console by FastAPI. Scoped rather than mounted at
  // the root so that adding it cannot shadow an API route or change what an
  // unknown path returns — the suite has 443 tests bound to this service and a
  // console should not be able to move any of them.
  base: '/console/',
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
