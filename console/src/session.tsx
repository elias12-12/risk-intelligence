/**
 * Who is acting, and what the console may therefore render.
 *
 * The role comes from `GET /me` and from nowhere else. A console that decided
 * locally which surfaces to show — by reading the token string, say — would be
 * asserting a permission the server has not granted, which is the same class of
 * unearned claim as a hardcoded liveness dot. The server is still the thing that
 * enforces it (`require_role`), so the worst a wrong answer here can do is offer
 * a screen that 403s; but offering it is itself a claim, and this one is cheap
 * to source properly.
 *
 * `api/auth.py` is not authentication and says so. Neither is this. It carries a
 * demo token so the audit columns have a true actor to record.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react'

import { api, ApiError, TOKEN_KEY } from './api/client'
import type { Principal } from './api/types'

interface SessionValue {
  principal: Principal | null
  /** Non-null when a token is present but the server rejected it. */
  error: string | null
  loading: boolean
  signIn: (token: string) => Promise<void>
  signOut: () => void
  /** Roles are ORDERED (`api/auth.py`): admin implies analyst. */
  can: (role: 'analyst' | 'admin') => boolean
}

const SessionContext = createContext<SessionValue | null>(null)

const ORDER = ['analyst', 'admin'] as const

export function SessionProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const resolve = useCallback(async () => {
    let stored: string | null = null
    try {
      stored = window.localStorage.getItem(TOKEN_KEY)
    } catch {
      stored = null
    }
    if (!stored) {
      setPrincipal(null)
      setError(null)
      setLoading(false)
      return
    }
    try {
      setPrincipal(await api.me())
      setError(null)
    } catch (err) {
      setPrincipal(null)
      setError(err instanceof ApiError && err.status === 401
        ? 'That token is not one this service knows.'
        : 'Could not reach the service to check the token.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void resolve() }, [resolve])

  const signIn = useCallback(async (value: string) => {
    window.localStorage.setItem(TOKEN_KEY, value.trim())
    setLoading(true)
    await resolve()
  }, [resolve])

  const signOut = useCallback(() => {
    window.localStorage.removeItem(TOKEN_KEY)
    setPrincipal(null)
    setError(null)
  }, [])

  const value = useMemo<SessionValue>(() => ({
    principal,
    error,
    loading,
    signIn,
    signOut,
    can: (role) => {
      if (!principal) return false
      return ORDER.indexOf(principal.role) >= ORDER.indexOf(role)
    },
  }), [principal, error, loading, signIn, signOut])

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession outside a SessionProvider')
  return value
}
