/**
 * The queue and the measurement tiles, on one surface.
 *
 * WHY THEY ARE ONE TAB. They were two, and the split asked the reader to hold
 * the connection themselves: the queue is the work, the tiles are whether the
 * work is worth doing, and every question worth asking of one is asked against
 * the other. "Forty cases waiting" means one thing at a 12% false-positive rate
 * and a different thing at 60%.
 *
 * WHY THIS FILE OWNS BOTH PAYLOADS. Because of the banner. A cycle tick
 * invalidates the queue *and* the tiles, and O5's rule is that nothing moves on
 * screen unless a person asked. Two panels each holding their own replacement
 * behind their own button would put two competing "something changed" notices on
 * one page, and a reader who accepted one and not the other would be looking at
 * a queue and a set of tiles computed at different moments, with nothing saying
 * so. So both go through `useHeld`, there is exactly one button, and it swaps
 * both at once.
 *
 * WHY IT IS A LAYOUT ROUTE. The sub-tab is in the URL — `/` and `/measurement`
 * — so a link to the tiles is a link and the back button does what it looks
 * like it does. It is mounted as a layout route with two child routes rather
 * than as two sibling routes, because a layout route is not remounted when its
 * child changes: the window you chose and the payloads you are holding survive a
 * move between the tabs. Two sibling routes would refetch and reset both, and
 * "switch tab, come back, my window is gone" is the bug this shape prevents.
 */
import { useState } from 'react'
import { NavLink, useMatch, useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import type { KpiSet, QueueEntry, ReferenceVocabulary } from '../api/types'
import { HeadlineStrip } from '../components/HeadlineStrip'
import { MeasurementPanel, toParam } from './Measurement'
import type { KpiWindowChoice } from './Measurement'
import { QueuePanel } from './Queue'
import { useAsync } from '../useAsync'
import { useHeld } from '../useHeld'

export function DashboardScreen() {
  const navigate = useNavigate()
  const onMeasurement = useMatch('/measurement') !== null

  const [includeWorked, setIncludeWorked] = useState(false)
  const [choice, setChoice] = useState<KpiWindowChoice>({ windowDays: null, asOf: null })

  const queue = useHeld<QueueEntry[]>(
    () => api.queue({ includeWorked }), [includeWorked])

  const kpis = useHeld<KpiSet>(
    () => api.kpis({
      windowDays: choice.windowDays ?? undefined,
      asOf: choice.asOf ? toParam(choice.asOf) : undefined,
    }),
    [choice.windowDays, choice.asOf],
  )

  // The reason-code vocabulary. Deliberately NOT held back by the watermark: it
  // is a vocabulary, not a measurement, and it does not move when a case
  // arrives. Allowed to fail — the trends tile loses its glosses and nothing
  // else changes.
  const reference = useAsync<ReferenceVocabulary>(() => api.reference(), [])

  const shownQueue = queue.shown
  const newCases = queue.pending && shownQueue
    ? queue.pending.filter((p) => !shownQueue.some((s) => s.alert_id === p.alert_id)).length
    : 0
  const queueChanged = Boolean(queue.pending && shownQueue
    && (newCases > 0 || queue.pending.length !== shownQueue.length))
  const changed = queueChanged || kpis.pending !== null

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Dashboard</h1>
          <p>
            What is waiting, and whether the system that raised it is any good.
            Both come off stored rows, and each panel names the window it covers
            and what it cannot tell you.
          </p>
        </div>
      </div>

      <HeadlineStrip queue={shownQueue} set={kpis.shown}
                     onOpenMeasurement={() => navigate('/measurement')} />

      {/*
        One banner for the whole page. `aria-live` because the thing it reports
        happened without anyone doing anything, and a change nobody triggered is
        exactly the change a screen-reader user has no other way to learn about.
      */}
      <div aria-live="polite">
        {changed && (
          <div className="notice good" style={{ margin: '14px 0' }}>
            <div className="spread">
              <span>
                {newCases > 0
                  ? <><b>{newCases}</b> new {newCases === 1 ? 'case' : 'cases'} since this page loaded.</>
                  : <>The data has changed since this page loaded.</>}{' '}
                Nothing has moved on screen — the queue order would shift under
                you, and the tiles would change while you were reading them.
              </span>
              <button className="primary small"
                      onClick={() => { queue.accept(); kpis.accept() }}>
                show {newCases > 0 ? `${newCases} new` : 'the current figures'}
              </button>
            </div>
          </div>
        )}
      </div>

      <nav className="subtabs" aria-label="dashboard sections">
        <NavLink to="/" end className={({ isActive }) => (isActive ? 'on' : '')}>
          Work queue
        </NavLink>
        <NavLink to="/measurement" className={({ isActive }) => (isActive ? 'on' : '')}>
          Measurement
        </NavLink>
      </nav>

      {onMeasurement
        ? (
          <MeasurementPanel set={kpis.shown} error={kpis.error} loading={kpis.loading}
                            choice={choice} onChoice={setChoice}
                            reference={reference.data} />
        ) : (
          <QueuePanel queue={queue} includeWorked={includeWorked}
                      onIncludeWorked={setIncludeWorked} />
        )}
    </div>
  )
}
