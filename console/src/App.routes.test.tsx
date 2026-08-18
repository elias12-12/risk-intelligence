/**
 * Every route in the app, mounted where the browser actually mounts it.
 *
 * `routing.test.tsx` proves the basename arithmetic against a reduced route
 * table. This one mounts the REAL `App` — every screen, the real session and
 * cycle providers — under the `/console` prefix and walks it. The bug it exists
 * to catch is the one that is invisible to a unit test of any single screen: a
 * route that resolves in isolation and throws, blanks or 404s once the prefix
 * and the rest of the tree are around it.
 *
 * It also checks the hrefs. A `<NavLink to="/rules">` under a basename must
 * render `/console/rules`; if it renders `/rules` the link leaves the app
 * entirely and the failure looks like "the tab does nothing".
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { CycleProvider } from './cycle'
import { SessionProvider } from './session'

const api = vi.hoisted(() => ({
  me: vi.fn(),
  queue: vi.fn(),
  alerts: vi.fn(),
  alert: vi.fn(),
  executions: vi.fn(),
  copilot: vi.fn(),
  report: vi.fn(),
  verdict: vi.fn(),
  kpis: vi.fn(),
  disposition: vi.fn(),
  rules: vi.fn(),
  rule: vi.fn(),
  features: vi.fn(),
  reference: vi.fn(),
  createRule: vi.fn(),
  updateRule: vi.fn(),
  promoteRule: vi.fn(),
  retireRule: vi.fn(),
  simulateSubject: vi.fn(),
  simulateRule: vi.fn(),
  simulateTransaction: vi.fn(),
  authorize: vi.fn(),
  cycleState: vi.fn(),
  runCycle: vi.fn(),
  rescore: vi.fn(),
}))
vi.mock('./api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    constructor(public status: number, public detail: unknown, message: string) {
      super(message)
    }
    get lines() { return [this.message] }
  },
  TOKEN_KEY: 'glassbox.token',
}))

const BASENAME = '/console'

/** The three nav landmarks App.tsx labels, queried by label rather than by
 *  position. Screens link to each other, so an unscoped `getByRole('link',
 *  { name: 'Send a charge' })` matches the tab AND the simulator's pointer at
 *  it — and the ambiguity is the screens being helpful, not a bug. */
const sections = () => within(screen.getByRole('navigation', { name: 'sections' }))
const actions = () => within(screen.getByRole('navigation', { name: 'actions' }))
const subtabs = () => within(screen.getByRole('navigation', { name: 'dashboard sections' }))

function mount(path: string) {
  return render(
    <MemoryRouter basename={BASENAME} initialEntries={[`${BASENAME}${path}`]}>
      <SessionProvider>
        <CycleProvider>
          <App />
        </CycleProvider>
      </SessionProvider>
    </MemoryRouter>,
  )
}

// This jsdom is started without a storage backend, and `session.tsx` reads a
// token from it to decide whether to ask `/me` at all. A signed-out console
// renders no admin tab, which would make the "Send a charge" assertions below
// pass for the wrong reason.
const store = new Map<string, string>()
Object.defineProperty(window, 'localStorage', {
  configurable: true,
  value: {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { store.set(k, v) },
    removeItem: (k: string) => { store.delete(k) },
    clear: () => { store.clear() },
  },
})

beforeEach(() => {
  window.localStorage.setItem('glassbox.token', 'demo-admin')
  for (const fn of Object.values(api)) fn.mockReset()
  api.me.mockResolvedValue({ actor: 'demo', role: 'admin' })
  api.cycleState.mockResolvedValue({
    scheduler_running: true, interval_seconds: 30, started_at: null,
    frontier: '2026-01-15T15:00:00Z', streams: {}, recent_ticks: [],
  })
  api.queue.mockResolvedValue([])
  api.kpis.mockResolvedValue({
    as_of: '2026-01-15T15:00:00Z',
    window_start: '2026-01-08T15:00:00Z',
    window_end: '2026-01-15T15:00:00Z',
    baseline_start: null, baseline_end: null,
    baseline_available: false, baseline_absent_reason: 'not enough data', tiles: [],
  })
  api.reference.mockResolvedValue({ reason_codes: [] })
  api.rules.mockResolvedValue([])
  api.features.mockResolvedValue([])
  api.alerts.mockResolvedValue([])
  api.alert.mockResolvedValue({
    alert_id: 4, decision_id: 9, title: 'A case that exists',
    subject: { type: 'customer', id: 'cust_44' }, execution_mode: 'async',
    score: '87', band: 'high', signals: [], subjects: [], rules_fired: [],
    evidence: [], created_at: '2026-01-15T14:00:00Z',
    // Required and non-nullable in `alert.v1`, so the screen renders it
    // unconditionally and a fixture without it is the fixture's bug.
    action: {
      taken: 'block', source_rule: null, vetoed_by: null,
      prevent_threshold_met: true, review_threshold_met: true,
      recommended: null, would_clear: null,
    },
  })
  api.executions.mockResolvedValue([])
  api.copilot.mockResolvedValue({ answers: [], model_backed: false })
  api.verdict.mockResolvedValue(null)
})

/** Every route App.tsx declares, and the text that proves it rendered. */
const ROUTES: Array<[string, string | RegExp]> = [
  ['/', 'Dashboard'],
  ['/measurement', 'Dashboard'],
  ['/rules', 'Rules'],
  ['/simulate', 'Simulate'],
  ['/authorize', 'Send a charge'],
  ['/alerts/4', 'A case that exists'],
]

describe('every route resolves under the /console prefix', () => {
  for (const [path, proof] of ROUTES) {
    it(`renders ${path}`, async () => {
      mount(path)
      expect(await screen.findByRole('heading', { level: 1, name: proof }))
        .toBeInTheDocument()
    })
  }

  it('redirects the old /kpis link to the measurement sub-tab', async () => {
    mount('/kpis')
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' }))
      .toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Window' })).toBeInTheDocument()
  })
})

describe('every link points inside the app', () => {
  it('prefixes the nav hrefs with the basename', async () => {
    mount('/')
    // A link rendered without the prefix leaves the app: the browser does a
    // full navigation to a path the dev server answers with the shell, the
    // router then matches nothing, and the tab "does nothing".
    await screen.findByRole('navigation', { name: 'actions' })
    for (const [name, href] of [
      ['Dashboard', '/console'],
      ['Rules', '/console/rules'],
      ['Simulate', '/console/simulate'],
    ] as const) {
      expect(sections().getByRole('link', { name })).toHaveAttribute('href', href)
    }
    expect(actions().getByRole('link', { name: 'Send a charge' }))
      .toHaveAttribute('href', '/console/authorize')
  })

  it('prefixes the sub-tabs too', async () => {
    mount('/')
    await screen.findByRole('navigation', { name: 'dashboard sections' })
    expect(subtabs().getByRole('link', { name: 'Work queue' }))
      .toHaveAttribute('href', '/console')
    expect(subtabs().getByRole('link', { name: 'Measurement' }))
      .toHaveAttribute('href', '/console/measurement')
  })
})

describe('clicking a tab actually moves', () => {
  it('walks the whole navigation without a reload', async () => {
    const user = userEvent.setup()
    mount('/')
    await screen.findByRole('heading', { level: 1, name: 'Dashboard' })

    await user.click(subtabs().getByRole('link', { name: 'Measurement' }))
    expect(await screen.findByRole('heading', { name: 'Window' })).toBeInTheDocument()

    await user.click(sections().getByRole('link', { name: 'Rules' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Rules' })).toBeInTheDocument()

    await user.click(sections().getByRole('link', { name: 'Simulate' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Simulate' })).toBeInTheDocument()

    await user.click(actions().getByRole('link', { name: 'Send a charge' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Send a charge' }))
      .toBeInTheDocument()

    await user.click(sections().getByRole('link', { name: 'Dashboard' }))
    await waitFor(() => expect(
      screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument())
  })
})
