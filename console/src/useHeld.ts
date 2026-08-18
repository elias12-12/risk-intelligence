/**
 * Load something, and refuse to let a background tick move it under the pointer.
 *
 * `useAsync` is the honest three-state loader for anything a click asked for.
 * This is its sibling for the two surfaces a *cycle* can invalidate — the queue
 * and the tiles — and it exists because O5's argument is not specific to a
 * table:
 *
 *   The queue is ordered by priority, not by arrival, so a case that turns up
 *   does not append — it INSERTS, and every row below it shifts. Reordering a
 *   list under a pointer is how somebody dispositions the case they were not
 *   reading, and a disposition is append-only, so the correction stays in the
 *   record forever.
 *
 * The tiles have the milder version of the same problem: a number that changes
 * while it is being read, with nothing on screen saying it changed, is a number
 * the reader will quote wrong. So both go through here.
 *
 * The rule is: **nothing changes on screen unless a person asked.**
 *
 *   - deps change, or `reload()` — a click. Loads into `shown`. May move.
 *   - the watermark moves — not a click. Loads into `pending`, and `shown` is
 *     left exactly as it was until `accept()` is called.
 *
 * Extracted from `screens/Queue.tsx`, where it was written once for the queue,
 * because the dashboard now shows the queue and the tiles together and two
 * independently-staling panels behind two competing banners is worse than the
 * problem either banner solved.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { useCycle } from './cycle'

export interface Held<T> {
  /** What is on screen. Only ever changes because someone asked. */
  shown: T | null
  /** What arrived since, held back. Null when nothing has. */
  pending: T | null
  error: Error | null
  loading: boolean
  /** Fetch and show. A click, so it is allowed to move the view. */
  reload: () => void
  /** Promote what is held into what is shown. The button's job. */
  accept: () => void
}

export function useHeld<T>(fn: () => Promise<T>, deps: unknown[] = []): Held<T> {
  const [shown, setShown] = useState<T | null>(null)
  const [pending, setPending] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  const { frontier } = useCycle()
  const seenFrontier = useRef<string | null>(null)

  // Responses can land out of order — a slow first request finishing after a
  // fast second one would put the older payload on screen and nothing would say
  // so. Only the newest generation is allowed to write.
  const generation = useRef(0)

  // The caller passes a fresh closure every render; `deps` decides when it
  // re-runs, exactly as useEffect's contract works. Same bargain as `useAsync`.
  const run = useCallback(fn, deps)

  const load = useCallback(async (into: 'shown' | 'pending') => {
    const mine = ++generation.current
    if (into === 'shown') setLoading(true)
    try {
      const next = await run()
      if (generation.current !== mine) return
      if (into === 'shown') { setShown(next); setPending(null) } else setPending(next)
      setError(null)
    } catch (err) {
      if (generation.current !== mine) return
      setError(err as Error)
    } finally {
      if (generation.current === mine && into === 'shown') setLoading(false)
    }
  }, [run])

  // First load, and a reload whenever the caller changes what it is asking for.
  // Both are clicks, so both may move the view.
  useEffect(() => { void load('shown') }, [load, nonce])

  // The watermark moved: something arrived. Fetch it, but do not show it.
  useEffect(() => {
    if (frontier === null) return
    if (seenFrontier.current === null) { seenFrontier.current = frontier; return }
    if (seenFrontier.current === frontier) return
    seenFrontier.current = frontier
    void load('pending')
  }, [frontier, load])

  return {
    shown,
    pending,
    error,
    loading,
    reload: () => setNonce((n) => n + 1),
    accept: () => { setShown((held) => (pending === null ? held : pending)); setPending(null) },
  }
}
