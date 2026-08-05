/**
 * The token control.
 *
 * Reads are open, so this is not a gate in front of the console — it is how the
 * two surfaces that leave a mark get an actor to record. The copy says what
 * `api/auth.py` says about itself, because a login box that looks like
 * authentication when it is a hardcoded map is the same species of unearned
 * claim as a green liveness dot.
 */
import { useState } from 'react'

import { useSession } from '../session'

export function SignIn() {
  const { principal, error, loading, signIn, signOut } = useSession()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')

  if (loading) return <span className="tiny dim">checking…</span>

  if (principal) {
    return (
      <div className="row tight">
        <span className="chip on">{principal.role}</span>
        <span className="small muted">{principal.actor}</span>
        <button className="ghost small" onClick={signOut}>sign out</button>
      </div>
    )
  }

  if (!open) {
    return (
      <div className="row tight">
        {error && <span className="tiny" style={{ color: 'var(--stopped)' }}>{error}</span>}
        <button className="small" onClick={() => setOpen(true)}>sign in</button>
      </div>
    )
  }

  return (
    <form className="row tight"
          onSubmit={(e) => { e.preventDefault(); void signIn(value); setOpen(false) }}>
      <input autoFocus value={value} placeholder="bearer token"
             style={{ width: 190 }} onChange={(e) => setValue(e.target.value)} />
      <button className="primary small" type="submit">use</button>
      <button className="ghost small" type="button" onClick={() => setOpen(false)}>cancel</button>
      <span className="tiny dim" style={{ maxWidth: 220 }}>
        demo tokens: <code>analyst-token</code>, <code>admin-token</code>. Not
        authentication — a hardcoded map, over plain HTTP, so the audit columns
        have a true actor.
      </span>
    </form>
  )
}
