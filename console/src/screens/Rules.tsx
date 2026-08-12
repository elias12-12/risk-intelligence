/**
 * The control plane, as it is — including the rules that act on nothing.
 *
 * `GET /rules` returns `inactive` rules too, because this is what the control
 * plane *is*, not the engine's view of it. Two published fields carry the
 * distinction that matters and both are rendered: `evaluated` (does the engine
 * score it at all) and `takes_action` (does a decision it reaches let it act).
 * A shadow rule is `evaluated: true, takes_action: false`, and rendering only
 * "status: shadow" would leave a reader to know what that means.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { FeatureView, RescoreReport, RuleSummary } from '../api/types'
import { Empty, ErrorNotice, Loading } from '../components/bits'
import { useSession } from '../session'
import { useAsync } from '../useAsync'

type Lane = 'async' | 'inline_sync'

/**
 * Apply the rules as they stand now to the data that is already here.
 *
 * THE CONTROL A PROMOTED RULE HAD NO WAY TO REACH. The cycle is driven by
 * arrivals — on a caught-up database it reports "nothing has arrived since the
 * last cycle" and stops, correctly — so a rule authored, published and promoted
 * in this console produced no alert and moved no tile, and nothing anywhere said
 * why. This is the other verb.
 *
 * The counts are rendered rather than a toast, deliberately: "done" is a claim
 * about work nobody watched, and the numbers are the only thing that shows
 * whether the rule reached anything.
 */
function RescoreControl() {
  const [lane, setLane] = useState<Lane>('async')
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<RescoreReport | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)

  async function run() {
    setRunning(true)
    setError(null)
    setReport(null)
    try {
      setReport(await api.rescore(lane))
    } catch (e) {
      setError(e as Error)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="grow">Re-score the population</h2>
        <select value={lane} onChange={(e) => setLane(e.target.value as Lane)}
                disabled={running} aria-label="lane">
          <option value="async">async — accounts, customers, merchants, rings</option>
          <option value="inline_sync">inline_sync — every transaction (slower)</option>
        </select>
        <button className="btn" onClick={run} disabled={running}>
          {running ? 'Re-scoring…' : 'Re-score'}
        </button>
      </div>
      <p className="tiny dim">
        Runs every active rule over every subject again. A newly promoted rule has
        never been applied to the data already here — the cycle reacts to arriving
        rows, not to new rules — and this is what applies it. Pick the lane the
        rule runs in.
      </p>

      <ErrorNotice error={error} />

      {report && (
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Lane</th><th className="num">Evaluated</th>
                <th className="num">Decisions</th><th className="num">Alerts</th>
                <th className="num">Folded</th><th className="num">Took</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="tiny">{report.lane}</td>
                {/* `totals` is whatever run_lane counted — named keys are read
                    out here, and a counter the engine learns later rides along
                    without the contract or this table having to be widened. */}
                <td className="num">{report.totals?.evaluations ?? 0}</td>
                <td className="num">{report.totals?.decisions ?? 0}</td>
                <td className="num">{report.totals?.alerts ?? 0}</td>
                <td className="num">{report.totals?.folded ?? 0}</td>
                <td className="num">{Math.round(Number(report.duration_ms ?? 0))} ms</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {report && (
        <div className="tiny dim" style={{ marginTop: 10 }}>
          A case seen again is <i>folded</i> rather than raised twice, so a second
          re-score of unchanged data raises nothing and folds everything. That is
          the dedup working, not the re-score failing.
        </div>
      )}
    </div>
  )
}

export function RulesScreen() {
  const rules = useAsync<RuleSummary[]>(() => api.rules(), [])
  const features = useAsync<FeatureView[]>(() => api.features(), [])
  const { can } = useSession()

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Rules</h1>
          <p>
            Detection logic lives in rows. A rule is a <code>rule_definitions</code>{' '}
            row plus <code>rule_conditions</code> rows; the Python interprets them
            and encodes no pattern of its own.
          </p>
        </div>
        {can('admin') && <Link className="btn" to="/rules/new">Author a rule</Link>}
      </div>

      {can('admin') && <RescoreControl />}

      <ErrorNotice error={rules.error} />
      {rules.loading && <Loading what="the control plane" />}

      {rules.data && (
        <div className="panel" style={{ padding: 0 }}>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Rule</th><th>Subject</th><th>Lane</th><th>Action</th>
                  <th className="num">Review</th><th className="num">Prevent</th>
                  <th className="num">Conditions</th><th>Status</th><th className="num">v</th>
                </tr>
              </thead>
              <tbody>
                {rules.data.map((r) => (
                  <tr key={r.rule_id}>
                    <td>
                      <Link to={`/rules/${r.rule_id}`}><b className="id">{r.rule_id}</b></Link>
                      <div className="tiny dim">{r.name}</div>
                    </td>
                    <td className="tiny">{r.subject_type}</td>
                    <td className="tiny">{r.execution_mode}</td>
                    <td>
                      <span className="chip">{r.action}</span>
                      {r.is_veto && <span className="chip veto">veto</span>}
                    </td>
                    <td className="num">{r.review_threshold ?? '—'}</td>
                    <td className="num">{r.prevent_threshold ?? '—'}</td>
                    <td className="num">{r.conditions}</td>
                    <td>
                      <span className={`chip ${r.takes_action ? 'on' : 'off'}`}>{r.status}</span>
                      <div className="tiny dim">
                        {r.evaluated ? 'evaluated' : 'not evaluated'}
                        {' · '}
                        {r.takes_action ? 'acts' : 'acts on nothing'}
                      </div>
                      {r.status_caveat && <div className="tiny dim">{r.status_caveat}</div>}
                    </td>
                    <td className="num">{r.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <h2 className="grow">Feature catalog</h2>
          <span className="tiny dim">
            what a rule may read. Adding one is a data-engineering act, not an
            admin one — there is no authoring endpoint for a computation spec.
          </span>
        </div>
        {features.loading && <Loading what="the catalog" />}
        {features.data && features.data.length === 0 && <Empty>No features.</Empty>}
        {features.data && features.data.length > 0 && (
          <div className="scroll-x">
            <table>
              <thead>
                {/* `Spec` (the version integer) and `Absent` are gone. The spec
                    version is a maintenance number nobody reads across 24 rows,
                    and the absent/default distinction moved to the AUTHORING
                    screen, where it changes what you write rather than sitting
                    in a catalog as noise. It is load-bearing — a feature with no
                    default can be observed missing, which is what lets a missing
                    mitigator strip a rule's preventive authority — so it was
                    moved, not deleted. */}
                <tr>
                  <th>Feature</th><th>Entity</th><th>Kind</th><th>Window</th>
                  <th>Inline</th>
                </tr>
              </thead>
              <tbody>
                {features.data.map((f) => (
                  <tr key={f.feature_key}>
                    <td>
                      <span className="id">{f.feature_key}</span>
                      {/* `feature_catalog.description`, straight off catalog.v1.
                          Written next to the computation it describes rather
                          than re-typed here, which is the only way the two
                          cannot drift. */}
                      <div className="tiny dim">{f.description ?? f.display_name}</div>
                    </td>
                    <td className="tiny">{f.entity_type}</td>
                    <td className="tiny">
                      {f.source_kind}
                      {f.aggregation && <span className="dim"> · {f.aggregation}</span>}
                    </td>
                    <td className="tiny mono">{f.window_spec ?? '—'}</td>
                    <td className="tiny">{f.inline_capable ? 'yes' : 'no'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="tiny dim" style={{ marginTop: 10 }}>
          One of these is produced by the system's own actions:{' '}
          <span className="id">card_challenge_fails_30d</span> counts step-ups
          this card failed. The engine challenges, the outcome settles, and it
          becomes evidence the next rule can read.
        </div>
      </div>
    </div>
  )
}
