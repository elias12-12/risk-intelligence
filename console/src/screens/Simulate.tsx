/**
 * The two what-ifs that answer a question about a subject or a charge.
 *
 * Both roll back by construction and both publish `persisted: false` on the
 * wire, so the frame around their answers comes from the payload rather than
 * from the fact that this file is called Simulate. Nothing on this screen can
 * write, and the one screen that can is deliberately somewhere else.
 *
 * `/simulate/subject` is the analyst's; `/simulate/transaction` is admin-only
 * (O4) because it is the one simulation that fabricates an EVENT, and a payload
 * showing a score against a transaction id is one screenshot away from being
 * read as something that occurred.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { SimulatedDecision, TransactionSimulation } from '../api/types'
import { ErrorNotice, Kv } from '../components/bits'
import { ActionRow, DecisionFrame, EvidenceBlock } from '../components/DecisionFrame'
import { EmptyPoolNote, ScoreBar } from '../components/ScoreBar'
import { when } from '../format'
import { useSession } from '../session'

export function SimulateScreen() {
  const { can } = useSession()
  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Simulate</h1>
          <p>
            The same engine — same planner, same point-in-time read, same
            precedence — inside a scope that rolls back. There is no "simulation
            mode" in the evaluator, because a second evaluation path would be a
            second answer.
          </p>
        </div>
      </div>

      <div className="grid two">
        <SubjectPanel />
        {can('admin')
          ? <TransactionPanel />
          : <div className="panel">
              <h2 style={{ marginBottom: 8 }}>A charge that never happened</h2>
              <div className="notice">
                <b>Admin only.</b> This is the one simulation that fabricates an
                event, and an event is what this system's whole record is made of.
                Every other defence is on the payload and every one of them
                depends on somebody reading it; the role is the one that does not.
              </div>
            </div>}
      </div>
    </div>
  )
}

/* --------------------------------------------------------- a stored subject */

function SubjectPanel() {
  const [type, setType] = useState('transaction')
  const [id, setId] = useState('TXN-48300')
  const [lane, setLane] = useState<'inline_sync' | 'async'>('inline_sync')
  const [replay, setReplay] = useState('')
  const [result, setResult] = useState<SimulatedDecision | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      setResult(await api.simulateSubject({
        subject_type: type, subject_id: id, lane,
        replay_as_of: replay || null,
      }))
      setError(null)
    } catch (err) { setError(err as Error); setResult(null) } finally { setBusy(false) }
  }

  return (
    <div className="stack">
      <div className="panel">
        <h2 style={{ marginBottom: 12 }}>Re-derive a stored subject</h2>
        <div className="stack" style={{ gap: 12 }}>
          <div className="fields">
            <div className="field">
              <label htmlFor="subject_type">Subject type</label>
              <input id="subject_type" value={type} onChange={(e) => setType(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="subject_id">Subject id</label>
              <input id="subject_id" value={id} onChange={(e) => setId(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="lane">Lane</label>
              <select id="lane" value={lane}
                      onChange={(e) => setLane(e.target.value as 'inline_sync' | 'async')}>
                <option value="inline_sync">inline_sync</option>
                <option value="async">async</option>
              </select>
            </div>
          </div>
          <div className="field">
            <label htmlFor="replay">Replay ceiling (optional)</label>
            <input id="replay" value={replay} placeholder="2026-01-15T14:07:11Z"
                   onChange={(e) => setReplay(e.target.value)} />
            <div className="tiny dim" style={{ marginTop: 5 }}>
              Set this to a stored decision's <code>decided_at</code> and the
              answer is <i>what that decision saw</i> rather than what we would
              say today. That is the audit question the bitemporal feature store
              exists to make answerable.
            </div>
          </div>
          <ErrorNotice error={error} />
          <button className="primary" onClick={() => void run()} disabled={busy}>
            {busy ? 'evaluating…' : 'Evaluate'}
          </button>
        </div>
      </div>

      {result && <SimulatedDecisionView d={result} />}
    </div>
  )
}

/* ------------------------------------------------- a charge that never was */

function TransactionPanel() {
  const [json, setJson] = useState(JSON.stringify({
    amount: '312.00',
    card_id: 'CARD-4417',
    account_id: 'ACC-4417',
    customer_id: 'CUST-OKAFOR',
    merchant_id: 'MER-GIFT',
    mcc: '5815',
    channel: 'cnp',
    entry_mode: 'ecom',
    txn_country: 'US',
    device_id: 'DEV-F90D2',
  }, null, 2))
  const [result, setResult] = useState<TransactionSimulation | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      setResult(await api.simulateTransaction({ transaction: JSON.parse(json) }))
      setError(null)
    } catch (err) { setError(err as Error); setResult(null) } finally { setBusy(false) }
  }

  return (
    <div className="stack">
      <div className="panel">
        <h2 style={{ marginBottom: 12 }}>A charge that never happened</h2>
        <div className="stack" style={{ gap: 12 }}>
          <div className="field">
            <label htmlFor="draft">Transaction draft</label>
            <textarea id="draft" value={json} style={{ minHeight: 250, fontFamily: 'var(--mono)' }}
                      onChange={(e) => setJson(e.target.value)} />
            <div className="tiny dim" style={{ marginTop: 5 }}>
              An omitted column is not a null one — unstated fields are dropped
              rather than sent as <code>null</code>, and the two get different
              answers. <code>synthetic_label</code> is refused: it is planted
              ground truth, and a fabricated charge that labelled itself would
              answer the question it was asked.
            </div>
          </div>
          <ErrorNotice error={error} />
          <button className="primary" onClick={() => void run()} disabled={busy}>
            {busy ? 'evaluating…' : 'Evaluate'}
          </button>
        </div>
      </div>

      {result && (
        <div className="stack">
          <SimulatedDecisionView d={result.decision} />

          <div className="panel">
            <h4 style={{ marginBottom: 10 }}>The feature pass this ran</h4>
            <div className="small muted">
              Scoped to {when(result.features.as_of)}, on the incremental runner —
              the same INSERT a real cycle uses, inside the scope, and gone after it.
            </div>
            {Object.keys(result.features.recomputed ?? {}).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <h4>Recomputed</h4>
                <div className="tiny mono dim" style={{ marginTop: 4 }}>
                  {Object.entries(result.features.recomputed ?? {})
                    .map(([k, n]) => `${k} (${n})`).join(', ')}
                </div>
              </div>
            )}
            {Object.keys(result.features.not_recomputed ?? {}).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <h4>Not recomputed, and why</h4>
                <div className="stack" style={{ gap: 3, marginTop: 4 }}>
                  {Object.entries(result.features.not_recomputed ?? {}).map(([k, why]) => (
                    <div key={k} className="tiny">
                      <span className="id">{k}</span>{' '}
                      <span className="dim">— {why}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {(result.limits ?? []).length > 0 && (
            <div className="panel">
              <h4 style={{ marginBottom: 10 }}>What this answer cannot tell you</h4>
              <div className="stack" style={{ gap: 8 }}>
                {(result.limits ?? []).map((l) => (
                  <div key={l.code} className="notice warn">
                    <b>{l.code}</b> — {l.detail}
                    {(l.features ?? []).length > 0 && (
                      <div className="tiny mono" style={{ marginTop: 4 }}>
                        {(l.features ?? []).join(', ')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="tiny dim" style={{ marginTop: 10 }}>{result.basis}</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ shared render */

function SimulatedDecisionView({ d }: { d: SimulatedDecision }) {
  return (
    <div className="stack">
      <DecisionFrame persisted={d.persisted !== false}>
        <div className="score-head">
          <div className={`score-value ${String(d.score) === '0' ? 'zero' : ''}`}>{d.score}</div>
          <span className={`chip band-${d.band}`}>{d.band}</span>
          <div className="small muted">
            <span className="id">{d.subject.type} {d.subject.id}</span> · {d.lane} lane
            · as of {when(d.as_of)}
          </div>
        </div>

        <div style={{ margin: '14px 0' }}>
          <ActionRow action={d.action} />
        </div>

        <div className="row" style={{ marginBottom: 12 }}>
          <span className={`chip ${d.would_alert ? 'on' : 'off'}`}>
            {d.would_alert ? 'would raise a case' : 'would raise no case'}
          </span>
          {(d.shadow_rules ?? []).length > 0 && (
            <span className="chip veto">
              shadow: {(d.shadow_rules ?? []).join(', ')} → {d.shadow_score} / {d.shadow_action}
            </span>
          )}
        </div>

        <ScoreBar signals={d.signals ?? []} score={String(d.score)} />
        {(d.signals ?? []).length === 0 && String(d.score) === '0' && (
          <div style={{ marginTop: 12 }}><EmptyPoolNote /></div>
        )}

        <div style={{ marginTop: 16 }}>
          <EvidenceBlock evidence={d.evidence} />
        </div>

        <div className="tiny dim" style={{ marginTop: 12 }}>{d.basis}</div>
      </DecisionFrame>

      {(d.rules ?? []).length > 0 && (
        <div className="panel">
          <h4 style={{ marginBottom: 10 }}>Which rules looked</h4>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Rule</th><th className="num">Score</th><th className="num">Review</th>
                  <th>Satisfied</th><th>Authority</th><th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {(d.rules ?? []).map((r) => (
                  <tr key={r.rule_id}>
                    <td>
                      <Link to={`/rules/${r.rule_id}`} className="id">{r.rule_id}</Link>
                      <div className="tiny dim">{r.name}</div>
                    </td>
                    <td className="num">{r.score}</td>
                    <td className="num dim">{r.review_threshold ?? '—'}</td>
                    <td>{r.satisfied ? 'yes' : <span className="dim">no</span>}</td>
                    <td>{r.authorised ? <b>yes</b> : <span className="dim">no</span>}</td>
                    <td className="tiny dim">
                      {r.shadow && <span className="chip off">shadow</span>}
                      {r.is_veto && (
                        <span className="chip veto">
                          veto {r.veto_established === null ? 'indeterminate'
                            : r.veto_established ? 'established' : 'not established'}
                        </span>
                      )}
                      {!r.preventive_authority && (
                        <span className="chip">no preventive authority</span>
                      )}
                      {(r.degraded_features ?? []).length > 0 && (
                        <div>degraded: {(r.degraded_features ?? []).join(', ')}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="tiny dim" style={{ marginTop: 10 }}>
            A rule that looked and was not allowed to speak is still a rule that
            looked — which is the question a trace answers.
          </div>
        </div>
      )}

      <div className="panel">
        <h4 style={{ marginBottom: 8 }}>Nothing was written</h4>
        <Kv rows={[
          ['persisted', String(d.persisted)],
          ['evaluation', d.evidence.evaluation_id],
          ['point-in-time bound', when(d.evidence.pit_bound_at)],
        ]} />
      </div>
    </div>
  )
}
