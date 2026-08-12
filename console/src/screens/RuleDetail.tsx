/**
 * One rule: its conditions, what each condition has actually earned, and its
 * published version history.
 *
 * The measurement sits beside the price on purpose — that is what this surface
 * is for. A condition that has never been evaluated publishes `performance:
 * null` plus a sentence (decision 17), and that sentence is rendered instead of
 * zeroes: `fire_rate: 0%, precision: 0%` on a newly authored rule reads as a
 * measurement of a bad condition rather than as an absence of evidence.
 *
 * Precision is direction-aware, as `v_condition_performance` measures it: an
 * aggravator is right when it fires on fraud, a mitigator when it fires on
 * legitimate traffic. The column header says which, because a single "precision"
 * column would invite exactly the inversion §5 objects to.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { ConditionView, RuleDetail } from '../api/types'
import { Empty, ErrorNotice, Kv, Loading } from '../components/bits'
import { signed, when } from '../format'
import { useSession } from '../session'
import { useAsync } from '../useAsync'

export function RuleDetailScreen() {
  const { id } = useParams<{ id: string }>()
  const rule = useAsync<RuleDetail>(() => api.rule(id!), [id])
  const { can } = useSession()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  if (rule.loading) return <div className="page"><Loading what="the rule" /></div>
  if (rule.error) return <div className="page"><ErrorNotice error={rule.error} /></div>
  if (!rule.data) return <div className="page"><Empty>No such rule.</Empty></div>

  const r = rule.data

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn(); setError(null); rule.reload() }
    catch (err) { setError(err as Error) }
    finally { setBusy(false) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <div className="row tight" style={{ marginBottom: 4 }}>
            <Link to="/rules" className="small">← rules</Link>
          </div>
          <h1><span className="id">{r.rule_id}</span> — {r.name}</h1>
          {r.description && <p>{r.description}</p>}
        </div>
        <div className="row">
          <span className={`chip ${r.takes_action ? 'on' : 'off'}`}>{r.status}</span>
          {r.is_veto && <span className="chip veto">veto</span>}
          {can('admin') && (
            <>
              <Link className="btn" to={`/rules/${r.rule_id}/edit`}>Edit</Link>
              {r.status === 'shadow' && (
                <button className="primary" disabled={busy}
                        onClick={() => void act(() => api.promoteRule(r.rule_id))}>
                  Promote to active
                </button>
              )}
              {r.status !== 'inactive' && (
                <button className="danger" disabled={busy}
                        onClick={() => void act(async () => {
                          await api.retireRule(r.rule_id)
                          navigate('/rules')
                        })}>
                  Retire
                </button>
              )}
            </>
          )}
        </div>
      </div>

      <ErrorNotice error={error} />

      {!r.takes_action && (
        <div className="notice warn" style={{ marginBottom: 14 }}>
          <b>This rule acts on nothing.</b>{' '}
          {r.status_caveat ?? 'It is scored on every applicable subject and every '
            + 'condition it looks at is recorded, and it contributes no signal, '
            + 'holds no authority, carries no severity and casts no veto.'}{' '}
          Promoting it is what makes it act, and that is a separate decision.
        </div>
      )}

      <div className="split">
        <div className="stack">
          <div className="panel">
            <h2 style={{ marginBottom: 4 }}>Conditions</h2>

            {/* §12. Replaces the server's PERFORMANCE_BASIS as the primary
                explanation — "precision_pct is direction-aware ... and HIGH IS
                BAD" is exactly right and is written for whoever maintains the
                view. The string still rides on catalog.v1 and is still rendered
                under each table, as the technical record. */}
            <p className="tiny dim" style={{ marginBottom: 4 }}>
              <b>How each condition is performing.</b> Every condition of every
              rule that applied, whether it fired or not.
            </p>
            <p className="tiny dim" style={{ marginBottom: 4 }}>
              A condition that adds points is right when it fires on fraud. A
              condition that <i>subtracts</i> points is right when it fires on
              legitimate activity — that is its job — so it is scored the other
              way round. Higher precision is better for both.
            </p>
            <p className="tiny dim" style={{ marginBottom: 12 }}>
              ⚠️ Measured against fraud we planted in this sample data. Exact
              here, meaningless anywhere else.
            </p>

            {(r.condition_set ?? []).length === 0 ? <Empty>No conditions.</Empty> : (
              <div className="stack" style={{ gap: 10 }}>
                {(r.condition_set ?? []).map((c) => <Condition key={c.condition_id} c={c} />)}
              </div>
            )}

            <ColumnHelp />
            <div className="tiny dim" style={{ marginTop: 12 }}>
              Combined with <b>{r.combine}</b>
              {r.combine === 'AND' && ' across condition groups — a rule whose '
                + 'required conditions are unsatisfied produces no partial score.'}
            </div>
          </div>

          {(r.recommended_action_text || r.clear_text) && (
            <div className="panel">
              <h2 style={{ marginBottom: 10 }}>Prose this rule owns</h2>
              <div className="stack" style={{ gap: 8 }}>
                {r.recommended_action_text && (
                  <div><h4>Recommended action</h4>
                    <div className="small">{r.recommended_action_text}</div></div>
                )}
                {r.clear_text && (
                  <div><h4>What would clear it</h4>
                    <div className="small">{r.clear_text}</div></div>
                )}
              </div>
              <div className="tiny dim" style={{ marginTop: 10 }}>
                Quoted verbatim wherever it appears. A surface that silently
                improved a sentence would be restating rather than quoting.
              </div>
            </div>
          )}
        </div>

        <div className="stack">
          <div className="panel">
            <h4 style={{ marginBottom: 10 }}>Policy</h4>
            <Kv rows={[
              ['subject', r.subject_type],
              ['lane', r.execution_mode],
              ['action', r.action],
              ['review threshold', String(r.review_threshold ?? '—')],
              ['prevent threshold', r.prevent_threshold !== null && r.prevent_threshold !== undefined
                ? String(r.prevent_threshold)
                : 'none — this rule cannot prevent'],
              ['evaluation lag', `${r.evaluation_lag_seconds}s`],
              ['combine', r.combine],
              ['created by', r.created_by],
              ['created', when(r.created_at)],
              ['shadow since', r.shadow_since ? when(r.shadow_since) : null],
            ]} />
          </div>

          <div className="panel">
            <div className="panel-head">
              <h4 className="grow">Published versions</h4>
              <span className={`chip ${r.versions_resolve ? 'on' : 'off'}`}>
                {r.versions_resolve ? 'resolve' : 'do not resolve'}
              </span>
            </div>
            {(r.versions ?? []).length === 0 ? (
              <div className="small muted">
                No snapshot. A stored <code>rule_version_set</code> naming this
                rule points at a definition nothing kept.
              </div>
            ) : (
              <div className="timeline">
                {(r.versions ?? []).map((v) => (
                  <div className="timeline-item" key={v.version}>
                    <span className="when">v{v.version}</span>
                    <span>
                      {when(v.published_at)}
                      <div className="tiny dim">
                        by {v.published_by ?? 'unknown'}
                        {v.status && ` · as ${v.status}`}
                      </div>
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div className="tiny dim" style={{ marginTop: 10 }}>
              The counter moves only when the definition moves. A save that
              changed nothing is not a new version, because a counter that ticks
              on every keystroke makes a stored version set meaningless.
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * Below ~30 evaluations a rate is not a measurement.
 *
 * Seed 0026 makes exactly this argument when it refuses to anchor a reprice on
 * `device_first_seen_min` — 36 firings, all of them on planted fixtures. A row
 * reading `Evaluated 1 · Fired 1 · Precision 100%` on the same screen as one
 * reading `Evaluated 9,844` invites the reader to treat them alike.
 */
const LOW_SAMPLE = 30

/** What each column of the performance table means. */
const COLUMN_HELP: Array<[string, string]> = [
  ['Evaluated', 'How many decisions looked at this condition'],
  ['Fired', 'How many times it was true'],
  ['Fire rate', 'Fired ÷ Evaluated — how much of the population it touches'],
  ['Fired on fraud', 'Of the times it fired, how many were on known fraud. '
    + 'Reads "Fired on legitimate" for a mitigating condition — the header flips, '
    + 'because that is what a mitigator should do'],
  ['Precision', 'How often it was right. Adds points: % of firings on fraud. '
    + 'Subtracts points: % on legitimate. Higher is better either way'],
  ['Pts / precision pt', 'What this condition charges per unit of accuracy. '
    + 'High is bad — the points are not being earned'],
  ['Degraded', 'How many times the evidence was missing or too old to use'],
]

function ColumnHelp() {
  return (
    <details style={{ marginTop: 14 }}>
      <summary className="tiny dim" style={{ cursor: 'pointer' }}>
        What the performance columns mean
      </summary>
      <div className="stack" style={{ gap: 4, marginTop: 8 }}>
        {COLUMN_HELP.map(([col, help]) => (
          <div key={col} className="tiny">
            <b>{col}</b> <span className="dim">— {help}</span>
          </div>
        ))}
      </div>
      <div className="tiny dim" style={{ marginTop: 10 }}>
        <b>Pts / precision pt found a real defect.</b>{' '}
        <span className="id">country_is_new_for_customer</span> was priced at +50
        and scored 7.4 — 4.4× the next aggravator. The +50 turned out to have been
        reverse-engineered from a screenshot to make a total add up. It was
        repriced to +12 in <span className="mono">db/seeds/0026_reprice_country_novelty.sql</span>,
        with the evidence in the comment. This column is what found it.
      </div>
    </details>
  )
}

function Condition({ c }: { c: ConditionView }) {
  const p = c.performance
  const mitigating = c.direction === 'mitigating'
  const thin = p !== null && p !== undefined && p.evaluated < LOW_SAMPLE
  return (
    <div className="panel" style={{ background: 'var(--panel-2)' }}>
      <div className="spread">
        <div className="row tight">
          <span className={`chip ${mitigating ? 'mit' : 'agg'}`}>{c.direction}</span>
          <span className="id">{c.feature_key}</span>
          <span className="mono dim">{c.operator}</span>
          <span className="mono">
            {c.threshold_num ?? c.threshold_text ?? ''}
          </span>
        </div>
        <div className="row tight">
          <b className={mitigating ? 'mit' : ''}
             style={{ fontFamily: 'var(--mono)', color: mitigating ? 'var(--mit)' : 'var(--agg)' }}>
            {signed(String(c.contribution_points))}
          </b>
          <span className="tiny dim">group {c.condition_group}</span>
          {c.is_required && <span className="chip off">required</span>}
        </div>
      </div>

      {c.signal_template && (
        <div className="tiny dim" style={{ marginTop: 6 }}>{c.signal_template}</div>
      )}

      <div style={{ marginTop: 10 }}>
        {!p ? (
          <div className="tiny dim">
            {c.performance_absent_because ?? 'No measurement.'}
          </div>
        ) : (
          <div className={`scroll-x ${thin ? 'thin-sample' : ''}`}>
            {thin && (
              <div className="tiny dim" style={{ marginBottom: 6 }}>
                ⚠ {p.evaluated} evaluation{p.evaluated === 1 ? '' : 's'} — too few
                to be a measurement. Every rate below is quoted with its
                denominator for this reason.
              </div>
            )}
            <table>
              <thead>
                <tr>
                  <th className="num">Evaluated</th>
                  <th className="num">Fired</th>
                  <th className="num">Fire rate</th>
                  <th className="num">
                    {mitigating ? 'Fired on legitimate' : 'Fired on fraud'}
                  </th>
                  <th className="num">Precision</th>
                  <th className="num">Pts / precision pt</th>
                  <th className="num">Degraded</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="num">{p.evaluated.toLocaleString()}</td>
                  <td className="num">{p.fired.toLocaleString()}</td>
                  <td className="num">{p.fire_rate_pct ?? '—'}%</td>
                  <td className="num">
                    {p.fired_on_fraud.toLocaleString()}
                    <span className="dim"> / {p.fired_labelled.toLocaleString()}</span>
                  </td>
                  <td className="num">{p.precision_pct ?? '—'}%</td>
                  <td className="num">{p.points_per_precision_point ?? '—'}</td>
                  <td className="num">{p.degraded.toLocaleString()}</td>
                </tr>
              </tbody>
            </table>
            <div className="tiny dim" style={{ marginTop: 6 }}>{p.basis}</div>
          </div>
        )}
      </div>
    </div>
  )
}
