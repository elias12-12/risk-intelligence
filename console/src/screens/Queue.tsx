/**
 * The review queue — ordered by priority, and it explains its own order.
 *
 * Two things this screen is careful about.
 *
 * **The order is published, so it is rendered.** `priority = score × exposure ×
 * recency`, with all three factors and the formula on every entry. A single
 * opaque number deciding which customer a human looks at first is precisely what
 * §1 refuses for a score, and with more force here, because queue order decides
 * what gets human attention at all. The factors are shown multiplied out.
 *
 * **The list does not move under the pointer (O5).** A background cycle can
 * raise a case at any moment, and the queue is ordered by priority rather than
 * by arrival — so a new case does not append, it *inserts*, and every row below
 * it shifts. Reordering a list under a pointer is how somebody dispositions the
 * case they were not reading, and a disposition is append-only, so the
 * correction stays in the record forever. So: poll `/cycle`, and when the
 * watermark moves, fetch the new queue into a holding area and offer a button.
 * Nothing on screen changes until it is clicked.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { QueueEntry } from '../api/types'
import { Band, Empty, ErrorNotice, Loading } from '../components/bits'
import { useCycle } from '../cycle'
import { money, whenShort } from '../format'

export function QueueScreen() {
  const [shown, setShown] = useState<QueueEntry[] | null>(null)
  const [pending, setPending] = useState<QueueEntry[] | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [includeWorked, setIncludeWorked] = useState(false)
  const { frontier } = useCycle()
  const seenFrontier = useRef<string | null>(null)

  const load = useCallback(async (into: 'shown' | 'pending') => {
    try {
      const next = await api.queue({ includeWorked })
      if (into === 'shown') { setShown(next); setPending(null) } else setPending(next)
      setError(null)
    } catch (err) {
      setError(err as Error)
    }
  }, [includeWorked])

  // First load, and a reload whenever the analyst changes what they are asking
  // for. Both are clicks, so both may move the list.
  useEffect(() => { void load('shown') }, [load])

  // The watermark moved: something arrived. Fetch it, but do not show it.
  useEffect(() => {
    if (frontier === null) return
    if (seenFrontier.current === null) { seenFrontier.current = frontier; return }
    if (seenFrontier.current === frontier) return
    seenFrontier.current = frontier
    void load('pending')
  }, [frontier, load])

  const newCases = pending && shown
    ? pending.filter((p) => !shown.some((s) => s.alert_id === p.alert_id)).length
    : 0
  const changed = pending && shown && (newCases > 0 || pending.length !== shown.length)

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Queue</h1>
          <p>
            Cases nobody has worked yet, most urgent first. "Worked" means a
            person wrote a verdict — a case closed by the synthetic settler is
            still here, because a fixture script closing a case is not an analyst
            having worked it.
          </p>
        </div>
        <div className="row">
          <label className="checkline" style={{ margin: 0 }}>
            <input type="checkbox" checked={includeWorked}
                   onChange={(e) => setIncludeWorked(e.target.checked)} />
            <span style={{ fontSize: 13 }}>include cases a person has worked</span>
          </label>
          <button className="ghost small" onClick={() => void load('shown')}>reload</button>
        </div>
      </div>

      {changed && (
        <div className="notice good" style={{ marginBottom: 14 }}>
          <div className="spread">
            <span>
              {newCases > 0
                ? <><b>{newCases}</b> new {newCases === 1 ? 'case' : 'cases'} since this list loaded.</>
                : <>The queue has changed since this list loaded.</>}{' '}
              Nothing has moved on screen — the order would shift under you.
            </span>
            <button className="primary small"
                    onClick={() => { setShown(pending); setPending(null) }}>
              show {newCases > 0 ? `${newCases} new` : 'the current queue'}
            </button>
          </div>
        </div>
      )}

      <ErrorNotice error={error} />

      {!shown ? <Loading what="the queue" />
        : shown.length === 0 ? <Empty>Nothing waiting. Every open case has a verdict from a person.</Empty>
        : (
          <div className="panel" style={{ padding: 0 }}>
            <div className="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Subject</th>
                    <th className="num">Score</th>
                    <th>Band</th>
                    <th>Action</th>
                    <th className="num">Exposure</th>
                    <th className="num">Priority</th>
                    <th>Why that priority</th>
                    <th className="num">Events</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((e) => (
                    <tr key={e.alert_id}>
                      <td>
                        <Link to={`/alerts/${e.alert_id}`}><b>#{e.alert_id}</b></Link>
                        <div className="tiny dim">{e.title}</div>
                        {e.worked_by_analyst && <span className="chip off">worked</span>}
                      </td>
                      <td>
                        <div className="id">{e.subject_id}</div>
                        <div className="tiny dim">{e.subject_type}</div>
                      </td>
                      <td className="num"><b>{e.score}</b></td>
                      <td><Band band={e.band} /></td>
                      <td>
                        <span className="chip">{e.action_taken}</span>
                        {e.unresolved_executions > 0 && (
                          <div className="tiny dim">{e.unresolved_executions} unsettled</div>
                        )}
                      </td>
                      <td className="num">
                        {e.exposure_amount ? money(e.exposure_amount) : <span className="dim">—</span>}
                        {e.exposure_basis && <div className="tiny dim">{e.exposure_basis}</div>}
                      </td>
                      <td className="num"><b>{e.priority}</b></td>
                      <td className="tiny dim mono" style={{ whiteSpace: 'nowrap' }}>
                        {e.score_factor} × {e.exposure_factor} × {e.recency_factor}
                        <div>{e.age_hours}h old</div>
                      </td>
                      <td className="num">
                        {e.triggering_events}
                        <div className="tiny dim">{whenShort(e.last_event_at)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {shown[0] && (
              <div className="tiny dim" style={{ padding: '10px 12px', borderTop: '1px solid var(--line-soft)' }}>
                {shown[0].priority_basis}
              </div>
            )}
          </div>
        )}
    </div>
  )
}
