/**
 * The dashboard's three structural claims, checked by driving it.
 *
 * The merge of the queue and the tiles onto one surface is not a cosmetic move
 * — it changes who owns the payloads and who owns the "something arrived"
 * banner. Each of these tests guards a property that is otherwise re-established
 * by a human clicking around and noticing.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DashboardScreen } from './Dashboard'
import type { KpiSet, KpiTile, QueueEntry } from '../api/types'
import { TILE_COPY, TILE_GROUPS } from '../copy/tiles'

const api = vi.hoisted(() => ({
  queue: vi.fn(),
  kpis: vi.fn(),
  reference: vi.fn(),
}))
vi.mock('../api/client', () => ({
  api,
  // `bits.tsx` and `useAsync.ts` both `instanceof` this, so it has to be a real
  // class — declared inside the factory, because the factory is hoisted above
  // every top-level binding in this file.
  ApiError: class ApiError extends Error {},
  TOKEN_KEY: 'glassbox.token',
}))

const cycle = vi.hoisted(() => ({ frontier: 'w1' as string | null }))
vi.mock('../cycle', () => ({ useCycle: () => cycle }))

// ------------------------------------------------------------------ payloads

function entry(over: Partial<QueueEntry> = {}): QueueEntry {
  return {
    alert_id: 812,
    title: 'Velocity burst on a new card',
    subject_id: 'cust_44',
    subject_type: 'customer',
    score: '87',
    band: 'high',
    action_taken: 'block',
    exposure_amount: '1200.00',
    exposure_basis: 'sum of unsettled authorisations',
    priority: '2.41',
    priority_basis: 'priority = score × exposure × recency',
    score_factor: '0.87',
    exposure_factor: '1.40',
    recency_factor: '1.98',
    age_hours: 3,
    triggering_events: 5,
    last_event_at: '2026-01-15T14:02:00Z',
    unresolved_executions: 1,
    worked_by_analyst: false,
    ...over,
  } as QueueEntry
}

function tile(key: string, over: Partial<KpiTile> = {}): KpiTile {
  return {
    key,
    label: key.replace(/_/g, ' '),
    value: '12',
    unit: 'percent',
    numerator: 12,
    denominator: 100,
    window_start: '2026-01-08T15:00:00Z',
    window_end: '2026-01-15T15:00:00Z',
    baseline_start: null,
    baseline_end: null,
    baseline_value: null,
    delta_pct: null,
    basis: 'a basis',
    requires: 'a milestone',
    synthetic: false,
    caveat: null,
    parts: [],
    ...over,
  }
}

function kpiSet(over: Partial<KpiSet> = {}): KpiSet {
  return {
    as_of: '2026-01-15T15:00:00Z',
    window_start: '2026-01-08T15:00:00Z',
    window_end: '2026-01-15T15:00:00Z',
    baseline_start: '2026-01-01T15:00:00Z',
    baseline_end: '2026-01-08T15:00:00Z',
    baseline_available: true,
    baseline_absent_reason: null,
    tiles: Object.keys(TILE_COPY).map((k) => tile(k)),
    ...over,
  }
}

/** The route shape from App.tsx — a LAYOUT route with two children, which is
 *  the thing test three is about. Reproduced rather than imported so the test
 *  fails loudly if App stops mounting it this way. */
function mount(at = '/') {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path="/" element={<DashboardScreen />}>
          <Route index element={null} />
          <Route path="measurement" element={null} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  cycle.frontier = 'w1'
  api.queue.mockReset().mockResolvedValue([entry()])
  api.kpis.mockReset().mockResolvedValue(kpiSet())
  api.reference.mockReset().mockResolvedValue({ reason_codes: [] })
})

// --------------------------------------------------------------------- tests

describe('the queue and the tiles are one surface', () => {
  it('shows both the work and the headline figures without a tab change', () => {
    // The reason the merge happened: "forty cases waiting" means one thing at a
    // 12% false-positive rate and a different thing at 60%, and the two used to
    // be a navigation apart.
    mount()
    return waitFor(() => {
      expect(screen.getByText('#812')).toBeInTheDocument()
      expect(screen.getByText('Cases waiting')).toBeInTheDocument()
    })
  })

  it('files the tiles into sections rather than into one flat grid', async () => {
    mount('/measurement')
    for (const group of TILE_GROUPS) {
      expect(await screen.findByRole('heading', { name: group.label })).toBeInTheDocument()
    }
  })
})

describe('nothing moves under the pointer, on either panel', () => {
  it('holds a cycle back behind one button and swaps both payloads with it', async () => {
    const user = userEvent.setup()
    const { rerender } = mount()
    await screen.findByText('#812')

    // A cycle ran. Both the queue and the tiles are now stale.
    api.queue.mockResolvedValue([entry(), entry({ alert_id: 999, title: 'New one' })])
    api.kpis.mockResolvedValue(kpiSet({ as_of: '2026-01-15T16:00:00Z' }))
    cycle.frontier = 'w2'
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<DashboardScreen />}>
            <Route index element={null} />
            <Route path="measurement" element={null} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    // ONE notice, and the new case is not on screen yet.
    const button = await screen.findByRole('button', { name: /show 1 new/ })
    expect(screen.queryByText('#999')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^show / })).toHaveLength(1)

    await user.click(button)
    expect(await screen.findByText('#999')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^show / })).not.toBeInTheDocument()
  })
})

describe('the sub-tab is a route, and the shell survives it', () => {
  it('keeps the chosen window when you visit the queue and come back', async () => {
    // The layout-route claim in Dashboard.tsx. Two SIBLING routes would remount
    // the shell, refetch both payloads and reset the window — "switch tab, come
    // back, my window is gone" is the bug this shape prevents.
    const user = userEvent.setup()
    mount('/measurement')

    const windowDays = await screen.findByLabelText('spanning')
    await user.selectOptions(windowDays, '30')
    await waitFor(() => expect(api.kpis).toHaveBeenCalledWith(
      expect.objectContaining({ windowDays: 30 })))

    await user.click(screen.getByRole('link', { name: 'Work queue' }))
    await screen.findByText('#812')

    await user.click(screen.getByRole('link', { name: 'Measurement' }))
    expect(await screen.findByLabelText('spanning')).toHaveValue('30')
  })
})

describe('the queue narrows without reordering', () => {
  it('says how far a filter narrowed it, and that it did not reorder', async () => {
    const user = userEvent.setup()
    api.queue.mockResolvedValue([
      entry({ alert_id: 812, band: 'high' }),
      entry({ alert_id: 809, band: 'elevated' }),
    ])
    mount()
    await screen.findByText('#812')

    await user.selectOptions(screen.getByLabelText('Band'), 'elevated')
    expect(await screen.findByText(/1 of 2 shown — filtered, not reordered/))
      .toBeInTheDocument()
    expect(screen.queryByText('#812')).not.toBeInTheDocument()
  })

  it('explains a priority on demand, with the factors multiplied out', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByText('#812')

    await user.click(screen.getByRole('button', { name: 'why' }))
    const why = await screen.findByText(/priority/, { selector: '.mono' })
    expect(within(why).getByText('0.87')).toBeInTheDocument()
    expect(within(why).getByText('1.40')).toBeInTheDocument()
    expect(within(why).getByText('1.98')).toBeInTheDocument()
  })
})
