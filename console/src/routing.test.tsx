/**
 * The console is not served from an origin root, and its routes have to know.
 *
 * `vite.config.ts` sets `base: '/console/'` and `api/app.py`'s
 * `_mount_console` serves the bundle under the same prefix — on the stated
 * grounds that a console at `/` would need a catch-all, and a catch-all changes
 * what every unmatched path returns, including the ones 443 tests assert 404s
 * on. That decision is right, and it has a consequence at the other end:
 * `location.pathname` is `/console/…`, so a router whose routes are declared at
 * `/` matches nothing and the app renders a header above an empty page. React
 * Router says so — "No routes matched location /console/" — in the console log
 * that nobody has open.
 *
 * That failure is invisible to every other test in this suite, because they all
 * mount a `MemoryRouter` at `/`. These two are the ones that mount it where the
 * browser actually is.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

/** The shape of App.tsx's route table, reduced to what routing decides. */
function Tree() {
  return (
    <Routes>
      <Route path="/" element={<div>dashboard</div>}>
        <Route index element={null} />
        <Route path="measurement" element={null} />
      </Route>
      <Route path="/rules" element={<div>rules</div>} />
    </Routes>
  )
}

/** What `main.tsx` computes, from the one place the prefix is configured. */
function basenameFrom(baseUrl: string): string {
  return baseUrl.replace(/\/$/, '')
}

describe('the router is told where the app is mounted', () => {
  it('derives the basename from the configured base, trailing slash and all', () => {
    // Vite's BASE_URL always ends in a slash; React Router's basename must not.
    expect(basenameFrom('/console/')).toBe('/console')
    // ...and an app that IS at the root gets an empty basename, not "/".
    expect(basenameFrom('/')).toBe('')
  })

  it('renders the dashboard at the prefix the bundle is served from', () => {
    render(
      <MemoryRouter basename={basenameFrom('/console/')} initialEntries={['/console/']}>
        <Tree />
      </MemoryRouter>,
    )
    expect(screen.getByText('dashboard')).toBeInTheDocument()
  })

  it('resolves a deep link under the prefix', () => {
    // `_mount_console` returns the shell for any path under `/console`, so the
    // router is what has to make `/console/rules` mean the rules screen.
    render(
      <MemoryRouter basename={basenameFrom('/console/')} initialEntries={['/console/rules']}>
        <Tree />
      </MemoryRouter>,
    )
    expect(screen.getByText('rules')).toBeInTheDocument()
  })

  it('and the sub-tab under it', () => {
    render(
      <MemoryRouter basename={basenameFrom('/console/')}
                    initialEntries={['/console/measurement']}>
        <Tree />
      </MemoryRouter>,
    )
    // The layout route, matched through both the basename and its own child.
    expect(screen.getByText('dashboard')).toBeInTheDocument()
  })

  it('matches nothing when the basename is missing — the bug this guards', () => {
    render(<MemoryRouter initialEntries={['/console/']}><Tree /></MemoryRouter>)
    expect(screen.queryByText('dashboard')).toBeNull()
  })
})
