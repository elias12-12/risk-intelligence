import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from './App'
import { CycleProvider } from './cycle'
import { SessionProvider } from './session'
import './styles.css'

/**
 * Where the router thinks "/" is.
 *
 * This bundle is not served from an origin root. `vite.config.ts` sets
 * `base: '/console/'` and `api/app.py`'s `_mount_console` serves it under the
 * same prefix — deliberately, so that adding a console cannot shadow an API
 * route or change what an unmatched path returns. The consequence is that
 * `location.pathname` is `/console/…`, and a router whose routes are declared
 * at `/`, `/rules`, `/simulate` matches NONE of them: React Router logs "no
 * routes matched" and renders nothing below the header.
 *
 * Read off `import.meta.env.BASE_URL` rather than written down here, because
 * `vite.config.ts` is where that prefix is configured and a second copy of it
 * would agree with the first until the day the mount moved. Vite sets it in
 * dev and in a build alike, so the dev server on :5173/console/ and the bundle
 * FastAPI serves at :8000/console resolve to the same routes.
 */
const BASENAME = import.meta.env.BASE_URL.replace(/\/$/, '')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={BASENAME}>
      <SessionProvider>
        <CycleProvider>
          <App />
        </CycleProvider>
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
)
