/**
 * Load something, and be honest about the three states it can be in.
 *
 * Deliberately small. The states are `loading`, `error` and `data` and there is
 * no fourth: a screen that renders a number while `loading` is true is rendering
 * a number from the last request, and this console's whole proposition is that
 * what is on screen is what the payload said.
 */
import { useCallback, useEffect, useState } from 'react'

import { ApiError } from './api/client'

export interface AsyncState<T> {
  data: T | null
  error: ApiError | Error | null
  loading: boolean
  reload: () => void
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  // The caller passes a fresh closure every render; `deps` is what decides when
  // it actually re-runs, exactly as useEffect's contract works.
  const run = useCallback(fn, deps)

  useEffect(() => {
    let live = true
    setLoading(true)
    run().then(
      (value) => { if (live) { setData(value); setError(null); setLoading(false) } },
      (err) => { if (live) { setError(err as Error); setData(null); setLoading(false) } },
    )
    return () => { live = false }
  }, [run, nonce])

  return { data, error, loading, reload: () => setNonce((n) => n + 1) }
}
