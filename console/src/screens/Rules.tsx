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
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { FeatureView, RuleSummary } from '../api/types'
import { Empty, ErrorNotice, Loading } from '../components/bits'
import { useSession } from '../session'
import { useAsync } from '../useAsync'

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
                <tr>
                  <th>Feature</th><th>Entity</th><th>Kind</th><th>Window</th>
                  <th>Absent</th><th>Inline</th><th className="num">Spec</th>
                </tr>
              </thead>
              <tbody>
                {features.data.map((f) => (
                  <tr key={f.feature_key}>
                    <td>
                      <span className="id">{f.feature_key}</span>
                      <div className="tiny dim">{f.display_name}</div>
                    </td>
                    <td className="tiny">{f.entity_type}</td>
                    <td className="tiny">
                      {f.source_kind}
                      {f.aggregation && <span className="dim"> · {f.aggregation}</span>}
                    </td>
                    <td className="tiny mono">{f.window_spec ?? '—'}</td>
                    <td className="tiny">
                      {f.has_default
                        ? <span className="mono">{JSON.stringify(f.default_when_absent)}</span>
                        : <span className="dim">no default — observable as absent</span>}
                    </td>
                    <td className="tiny">{f.inline_capable ? 'yes' : 'no'}</td>
                    <td className="num">{f.spec_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="tiny dim" style={{ marginTop: 10 }}>
          "Absent" carries the §5 distinction JSON cannot: a feature with no
          default can be observed <i>missing</i>, which is what lets a missing
          mitigator strip a rule's preventive authority. Every feature cited by a
          negative contribution has no default, and a test enforces it.
        </div>
      </div>
    </div>
  )
}
