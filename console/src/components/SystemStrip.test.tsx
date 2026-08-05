import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SystemStrip } from './SystemStrip'
import type { CycleState } from '../api/types'

const useCycle = vi.hoisted(() => vi.fn())
vi.mock('../cycle', () => ({ useCycle }))

function state(over: Partial<CycleState> = {}): CycleState {
  return {
    scheduler_running: true,
    interval_seconds: 30,
    started_at: '2026-08-05T06:30:00Z',
    frontier: '2026-01-15T15:00:00Z',
    streams: {},
    recent_ticks: [],
    ...over,
  }
}

/**
 * Cross-cutting invariant 8: *nothing asserts liveness.* These are the tests
 * that make that mechanical rather than careful — in particular the third one,
 * because "the service is unreachable" is the case where a console is most
 * tempted to keep showing the last cheerful answer it had.
 */
describe('SystemStrip', () => {
  it('says running only when the payload says running', () => {
    useCycle.mockReturnValue({ state: state(), unavailable: null, frontier: null, refresh: vi.fn() })
    render(<SystemStrip />)
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('30s')).toBeInTheDocument()
  })

  it('says NOT running when the interval disables it — which is what the suite sets', () => {
    useCycle.mockReturnValue({
      state: state({ scheduler_running: false, interval_seconds: 0, started_at: null }),
      unavailable: null, frontier: null, refresh: vi.fn(),
    })
    render(<SystemStrip />)
    expect(screen.getByText('not running')).toBeInTheDocument()
    expect(screen.getByText(/interval is 0, which disables it/)).toBeInTheDocument()
  })

  it('says it does not know, rather than showing a stale green dot', () => {
    useCycle.mockReturnValue({
      state: null, unavailable: 'could not reach the service',
      frontier: null, refresh: vi.fn(),
    })
    const { container } = render(<SystemStrip />)
    expect(screen.getByText(/engine status unknown/)).toBeInTheDocument()
    expect(screen.getByText(/could not reach the service/)).toBeInTheDocument()
    expect(container.querySelector('.dot.live')).toBeNull()
  })

  it('says it does not know when nobody is signed in', () => {
    // `GET /cycle` needs an analyst token, so a signed-out console genuinely
    // cannot tell. Guessing either way would be the unearned claim.
    useCycle.mockReturnValue({
      state: null, unavailable: 'sign in to see whether the engine is running',
      frontier: null, refresh: vi.fn(),
    })
    const { container } = render(<SystemStrip />)
    expect(screen.getByText(/sign in to see whether the engine is running/)).toBeInTheDocument()
    expect(container.querySelector('.dot.unknown')).not.toBeNull()
  })

  it('reports a tick that did nothing as having done nothing', () => {
    useCycle.mockReturnValue({
      state: state({ recent_ticks: [{ ran: false, reason: 'nothing arrived since the watermark' }] }),
      unavailable: null, frontier: null, refresh: vi.fn(),
    })
    render(<SystemStrip />)
    expect(screen.getByText(/nothing arrived since the watermark/)).toBeInTheDocument()
  })
})
