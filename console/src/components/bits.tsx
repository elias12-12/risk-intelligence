/** Small shared pieces. Nothing here decides anything; they render what they
 *  are handed, which is the rule the rest of the console is built on. */
import type { ReactNode } from 'react'

import { ApiError } from '../api/client'

export function Band({ band }: { band: string | null | undefined }) {
  if (!band) return null
  return <span className={`chip band-${band}`}>{band}</span>
}

export function Chip({ kind, children }: { kind?: string; children: ReactNode }) {
  return <span className={kind ? `chip ${kind}` : 'chip'}>{children}</span>
}

export function Loading({ what }: { what: string }) {
  return <div className="empty">loading {what}…</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

/**
 * An error, rendered as what the server actually said.
 *
 * 401, 403 and 422 mean "sign in", "wrong role" and "the validator rejected
 * this", and `rules/validate.py` sends twenty-two distinct rejections written to
 * be read by an author. Collapsing them into "something went wrong" would throw
 * away the most useful thing this API returns.
 */
export function ErrorNotice({ error }: { error: Error | ApiError | null }) {
  if (!error) return null
  if (!(error instanceof ApiError)) {
    return <div className="notice bad">{error.message}</div>
  }
  const head = error.status === 401 ? 'Not signed in'
    : error.status === 403 ? 'This needs the admin role'
    : error.status === 409 ? 'Refused — conflicting state'
    : error.status === 422 ? 'Rejected'
    : `Failed (${error.status})`
  const lines = error.lines
  return (
    <div className="notice bad">
      <b>{head}</b>
      {lines.length === 1 ? <div style={{ marginTop: 4 }}>{lines[0]}</div> : (
        <ul>{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
      )}
    </div>
  )
}

/** A definition list, for the evidence blocks. */
export function Kv({ rows }: { rows: Array<[string, ReactNode]> }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (shown.length === 0) return null
  return (
    <dl className="kv">
      {shown.map(([k, v]) => <div key={k} style={{ display: 'contents' }}>
        <dt>{k}</dt><dd>{v}</dd>
      </div>)}
    </dl>
  )
}
