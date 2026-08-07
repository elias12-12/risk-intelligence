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
// Where the proxy forwards. `127.0.0.1` when this runs on the machine the
// service runs on; `http://host.docker.internal:8000` when it runs in the
// container docker-compose.yml describes, because a container reaching the host
// does not reach it over loopback. One variable rather than two configs: the
// dev proxy stays the only origin policy, which is what decision 9 above is
// about, and the value is read once here rather than at every call site.
const API_TARGET = process.env.GLASSBOX_API ?? 'http://127.0.0.1:8000'

// A Windows bind mount does not deliver inotify events into a Linux container.
// The watcher registers, nothing ever fires, and hot reload stops being hot
// without a single error to say so. Polling is the only thing that sees those
// edits, and it costs idle CPU — so it is opt-in, set by the compose service
// and off for anyone running this directly.
const POLL = process.env.VITE_POLL === '1'

export default defineConfig({
  plugins: [react()],
  // The build is served from /console by FastAPI. Scoped rather than mounted at
  // the root so that adding it cannot shadow an API route or change what an
  // unknown path returns — the suite has 443 tests bound to this service and a
  // console should not be able to move any of them.
  base: '/console/',
  server: {
    port: 5173,
    // No `host` here. The container passes `--host 0.0.0.0` on the command
    // line, because it has to publish beyond its own loopback to be reachable
    // at all; a developer running `npm run dev` on their own machine has no
    // such need and does not get their dev server put on the local network by a
    // config file they did not read.
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
    ...(POLL ? { watch: { usePolling: true, interval: 300 } } : {}),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
