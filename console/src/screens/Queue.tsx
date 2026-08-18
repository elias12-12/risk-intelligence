/**
 * The review queue — ordered by priority, and it explains its own order.
 *
 * Three things this panel is careful about.
 *
 * **The order is published, so it is rendered.** `priority = score × exposure ×
 * recency`, with all three factors and the formula available on every entry. A
 * single opaque number deciding which customer a human looks at first is
 * precisely what §1 refuses for a score, and with more force here, because queue
 * order decides what gets human attention at all.
 *
 * **The list does not move under the pointer (O5).** A background cycle can
 * raise a case at any moment, and the queue is ordered by priority rather than
 * by arrival — so a new case does not append, it *inserts*, and every row below
 * it shifts. That mechanism now lives in `useHeld`, and the dashboard owns the
 * banner, because the tiles have the same problem and two competing banners is
 * worse than one. This file renders what it is handed.
 *
 * **It filters, and it never sorts.** A column header that reordered the queue
 * would be the console overriding a published order with one of its own, on the
 * one screen whose whole claim is that the order is the server's and is
 * explained. Filters only narrow, the row count says how far, and the remaining
 * rows are in exactly the order `GET /queue` returned them.
 */
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import type { QueueEntry } from '../api/types'
import { Band, Empty, ErrorNotice, Loading } from '../components/bits'
import { money, whenShort } from '../format'
import type { Held } from '../useHeld'

const ALL = ''

export function QueuePanel({ queue, includeWorked, onIncludeWorked }: {
  queue: Held<QueueEntry[]>
  includeWorked: boolean
  onIncludeWorked: (value: boolean) => void
}) {
  const [band, setBand] = useState(ALL)
  const [action, setAction] = useState(ALL)
  const [expanded, setExpanded] = useState<number | null>(null)

  const all = queue.shown

  // The options come off the rows on screen, not off a list written here. A
  // hardcoded vocabulary would offer a band the payload stopped using and hide
  // one it started, and `GET /reference` is not the authority on what is IN this
  // queue — the queue is.
  const bands = useMemo(() => distinct(all?.map((e) => e.band)), [all])
  const actions = useMemo(() => distinct(all?.map((e) => e.action_taken)), [all])

  const rows = useMemo(() => (all ?? []).filter((e) =>
    (band === ALL || e.band === band) && (action === ALL || e.action_taken === action)),
  [all, band, action])

  const narrowed = all !== null && rows.length !== all.length

  return (
    <section className="panel flush" aria-labelledby="queue-heading">
      <div className="panel-head padded">
        <div className="grow">
          <h2 id="queue-heading">The queue</h2>
          <p className="tiny dim measure">
            Most urgent first, in the order the service published. "Worked" means
            a person wrote a verdict — a case closed by the synthetic settler is
            still here, because a fixture script closing a case is not an analyst
            having worked it.
          </p>
        </div>
      </div>

      <div className="filterbar">
        <label className="inline" htmlFor="filter-band">Band</label>
        <select id="filter-band" className="compact" value={band}
                onChange={(e) => setBand(e.target.value)}>
          <option value={ALL}>any</option>
          {bands.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>

        <label className="inline" htmlFor="filter-action">Action</label>
        <select id="filter-action" className="compact" value={action}
                onChange={(e) => setAction(e.target.value)}>
          <option value={ALL}>any</option>
          {actions.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>

        <span className="checkline">
          <input id="include-worked" type="checkbox" checked={includeWorked}
                 onChange={(e) => onIncludeWorked(e.target.checked)} />
          <label htmlFor="include-worked" className="inline plain">
            include cases a person has worked
          </label>
        </span>

        <span className="grow" />

        <span className="tiny dim" aria-live="polite">
          {all === null ? '' : narrowed
            ? `${rows.length} of ${all.length} shown — filtered, not reordered`
            : `${all.length} ${all.length === 1 ? 'case' : 'cases'}`}
        </span>
        <button className="ghost small" onClick={queue.reload}>reload</button>
      </div>

      {queue.error && <div className="padded"><ErrorNotice error={queue.error} /></div>}

      {all === null ? <Loading what="the queue" />
        : all.length === 0
          ? <Empty>Nothing waiting. Every open case has a verdict from a person.</Empty>
          : rows.length === 0
            ? <Empty>No case matches those filters. The queue is not empty — the filter is.</Empty>
            : (
              <div className="scroll-x">
                <table className="queue-table">
                  <caption className="sr-only">
                    Cases waiting for review, in the priority order the service published.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Case</th>
                      <th scope="col">Subject</th>
                      <th scope="col" className="num">Score</th>
                      <th scope="col">Action</th>
                      <th scope="col" className="num">Exposure</th>
                      <th scope="col" className="num">Priority</th>
                      <th scope="col" className="num">Activity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((e) => (
                      <Row key={e.alert_id} entry={e}
                           open={expanded === e.alert_id}
                           onToggle={() => setExpanded(
                             expanded === e.alert_id ? null : e.alert_id)} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
    </section>
  )
}

/**
 * One case.
 *
 * The row is clickable as a convenience for a pointer, and the case number is a
 * real `<a>` — which is what a keyboard and a screen reader follow. A focusable
 * `<tr>` with an Enter handler would announce as a row and behave as a link, so
 * the link stays the link and the row click is a shortcut layered on top of it.
 *
 * The `data-label` attributes are read by the stylesheet below 820px, where the
 * table becomes a stack of cards. One DOM, two layouts: rendering a second copy
 * of every row for narrow screens would be a second place the queue is written,
 * and the two would agree until the day a column changed.
 */
function Row({ entry: e, open, onToggle }: {
  entry: QueueEntry
  open: boolean
  onToggle: () => void
}) {
  const navigate = useNavigate()

  return (
    <>
      <tr className="clickable"
          onClick={(ev) => {
            // The row is a shortcut, not a trap: a click that landed on the link
            // or on the disclosure button belongs to that control.
            if ((ev.target as HTMLElement).closest('a, button')) return
            navigate(`/alerts/${e.alert_id}`)
          }}>
        <td data-label="Case">
          <Link to={`/alerts/${e.alert_id}`}><b>#{e.alert_id}</b></Link>
          <div className="tiny dim">{e.title}</div>
          {e.worked_by_analyst && <span className="chip off">worked</span>}
        </td>
        <td data-label="Subject">
          <div className="id">{e.subject_id}</div>
          <div className="tiny dim">{e.subject_type}</div>
        </td>
        <td data-label="Score" className="num">
          <b>{e.score}</b>
          <div><Band band={e.band} /></div>
        </td>
        <td data-label="Action">
          <span className="chip">{e.action_taken}</span>
          {e.unresolved_executions > 0 && (
            <div className="tiny dim">{e.unresolved_executions} unsettled</div>
          )}
        </td>
        <td data-label="Exposure" className="num">
          {e.exposure_amount ? money(e.exposure_amount) : <span className="dim">—</span>}
        </td>
        <td data-label="Priority" className="num">
          <b>{e.priority}</b>
          <div>
            <button className="ghost small why" onClick={onToggle}
                    aria-expanded={open} aria-controls={`why-${e.alert_id}`}>
              {open ? 'hide why' : 'why'}
            </button>
          </div>
        </td>
        <td data-label="Activity" className="num">
          {e.triggering_events}
          <div className="tiny dim">{whenShort(e.last_event_at)}</div>
        </td>
      </tr>
      {open && (
        <tr className="why-row" id={`why-${e.alert_id}`}>
          <td colSpan={7}>
            <div className="why-body">
              <div className="mono">
                priority <b>{e.priority}</b> = score <b>{e.score_factor}</b>
                {' '}× exposure <b>{e.exposure_factor}</b>
                {' '}× recency <b>{e.recency_factor}</b>
              </div>
              <div className="tiny dim">
                {e.age_hours}h old
                {e.exposure_basis && <> · exposure basis: {e.exposure_basis}</>}
                {e.unresolved_executions > 0 && (
                  <> · {e.unresolved_executions} unsettled execution
                    {e.unresolved_executions === 1 ? '' : 's'}</>
                )}
              </div>
              <div className="tiny dim">{e.priority_basis}</div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/** The values actually present, in first-seen order. Never a written-down list. */
function distinct(values: Array<string | null | undefined> | undefined): string[] {
  const out: string[] = []
  for (const v of values ?? []) if (v && !out.includes(v)) out.push(v)
  return out
}
