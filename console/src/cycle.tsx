/**
 * Whether anything is actually running — asked, never assumed.
 *
 * Cross-cutting invariant 8: *nothing asserts liveness.* Whether the engine is
 * running comes from `GET /cycle` and the `ingest_watermark` rows behind it, and
 * a console indicator that is a hardcoded `true` is the same unearned claim §11
 * refuses on a KPI tile — in the one place a viewer has no way to check it.
 *
 * So there are exactly three answers here and the third one is load-bearing:
 * running, not running, and *we could not ask*. `/cycle` needs an analyst token,
 * so a signed-out console genuinely does not know, and it says that rather than
 * defaulting to either of the two answers it might have guessed.
 *
 * One poller for the whole app. The system strip and the queue both need the
 * watermark and two pollers would be two clocks.
 */
import {
  createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react'

import { api } from './api/client'
import type { CycleState } from './api/types'
import { useSession } from './session'

export const POLL_MS = 10_000

interface CycleValue {
  state: CycleState | null
  /** Why we have no state. Null when we have one. */
  unavailable: string | null
  /** The event-time watermark. When this moves, something arrived. */
  frontier: string | null
  refresh: () => void
}

const CycleContext = createContext<CycleValue | null>(null)

export function CycleProvider({ children }: { children: ReactNode }) {
  const { principal } = useSession()
  const [state, setState] = useState<CycleState | null>(null)
  const [unavailable, setUnavailable] = useState<string | null>('not asked yet')
  const [nonce, setNonce] = useState(0)
  const live = useRef(true)

  useEffect(() => {
    live.current = true
    if (!principal) {
      setState(null)
      setUnavailable('sign in to see whether the engine is running')
      return () => { live.current = false }
    }

    const tick = async () => {
      try {
        const next = await api.cycleState()
        if (!live.current) return
        setState(next)
        setUnavailable(null)
      } catch {
        if (!live.current) return
        setState(null)
        setUnavailable('could not reach the service')
      }
    }

    void tick()
    const handle = window.setInterval(tick, POLL_MS)
    return () => { live.current = false; window.clearInterval(handle) }
  }, [principal, nonce])

  const value = useMemo<CycleValue>(() => ({
    state,
    unavailable,
    frontier: state?.frontier ?? null,
    refresh: () => setNonce((n) => n + 1),
  }), [state, unavailable])

  return <CycleContext.Provider value={value}>{children}</CycleContext.Provider>
}

export function useCycle(): CycleValue {
  const value = useContext(CycleContext)
  if (!value) throw new Error('useCycle outside a CycleProvider')
  return value
}
