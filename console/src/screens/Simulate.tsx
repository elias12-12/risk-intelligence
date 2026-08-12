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
import type {
  ReferenceVocabulary, RuleSummary, SimulatedDecision, TransactionSimulation,
} from '../api/types'
import { ErrorNotice, Kv } from '../components/bits'
import { ActionRow, DecisionFrame, EvidenceBlock } from '../components/DecisionFrame'
import { EmptyPoolNote, ScoreBar } from '../components/ScoreBar'
import { when } from '../format'
import { useSession } from '../session'
import { useAsync } from '../useAsync'

type Lane = 'inline_sync' | 'async'

/**
 * Subjects that are known to answer, so the screen does not require guessing an
 * id that exists AND a lane some rule targets it in.
 *
 * Every one is checked against a freshly bootstrapped database; the expectation
 * beside it is what the engine returned, not what anybody hoped for. They are
 * FIXTURE ids, which is why they may not appear in `db/` — a test forbids
 * schema and seeds from naming them, because a fixture id in a migration is a
 * demo pretending to be a system.
 */
const PRESETS: Array<{
  type: string; id: string; lane: Lane; expect: string; note?: string
}> = [
  { type: 'transaction', id: 'TXN-48291', lane: 'inline_sync',
    expect: '87 · high · challenge' },
  { type: 'transaction', id: 'TXN-48300', lane: 'inline_sync',
    expect: '68 · elevated · monitor', note: 'a veto capped it' },
  { type: 'transaction', id: 'TXN-48251', lane: 'inline_sync',
    expect: '0 · low · allow', note: 'mitigators consumed the pool' },
  { type: 'network', id: 'RING-1187', lane: 'async',
    expect: '64 · elevated · hold' },
  { type: 'account', id: 'ACC-2201', lane: 'async',
    expect: '58 · elevated · hold' },
]

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
  const [lane, setLane] = useState<Lane>('inline_sync')
  const [replay, setReplay] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [result, setResult] = useState<SimulatedDecision | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  // The vocabulary and the control plane, both read rather than hardcoded. The
  // seven subject types come from the same rows the foreign keys enforce, and
  // which (type, lane) pairs mean anything comes from the rules themselves.
  const reference = useAsync<ReferenceVocabulary>(() => api.reference(), [])
  const rules = useAsync<RuleSummary[]>(() => api.rules(), [])

  const subjectTypes = reference.data?.subject_types ?? []

  /**
   * Lanes some evaluated rule targets this subject type in.
   *
   * THE MAIN FAILURE MODE OF THIS SCREEN. A plan is only produced for a subject
   * type some rule names IN THAT LANE; anything else comes back
   * `SubjectNotEvaluable`, which is correct and reads as a broken form.
   * `network` on `inline_sync` is the one people hit — no inline rule scores a
   * ring, because a ring is not knowable in fifty milliseconds.
   */
  const lanesFor = (t: string): Lane[] => {
    const found = (rules.data ?? [])
      .filter((r) => r.subject_type === t && r.evaluated)
      .map((r) => r.execution_mode as Lane)
    return [...new Set(found)]
  }

  const evaluable = lanesFor(type)
  // Only claimed once the control plane has actually answered. Before that the
  // list is empty because nothing is known, not because nothing is targeted.
  const unevaluable = rules.data !== null && evaluable.length > 0
    && !evaluable.includes(lane)

  const apply = (p: typeof PRESETS[number]) => {
    setType(p.type); setId(p.id); setLane(p.lane); setResult(null); setError(null)
  }

  const run = async () => {
    setBusy(true)
    try {
      setResult(await api.simulateSubject({
        subject_type: type, subject_id: id, lane,
        replay_as_of: (advanced && replay) ? replay : null,
      }))
      setError(null)
    } catch (err) { setError(err as Error); setResult(null) } finally { setBusy(false) }
  }

  return (
    <div className="stack">
      <div className="panel">
        <h2 style={{ marginBottom: 4 }}>Re-score something we already decided</h2>
        <p className="tiny dim" style={{ marginBottom: 12 }}>
          Runs the same engine over existing data. Nothing is saved.
        </p>

        <div className="stack" style={{ gap: 12 }}>
          <div className="field">
            <label>Known-good subjects</label>
            <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
              {PRESETS.map((p) => (
                <button key={p.id} className="btn" onClick={() => apply(p)}
                        title={`${p.expect}${p.note ? ` — ${p.note}` : ''}`}>
                  {p.id}
                  <span className="dim"> · {p.expect}</span>
                </button>
              ))}
            </div>
            <div className="tiny dim" style={{ marginTop: 5 }}>
              Each one was run against a fresh build and reports what the engine
              actually returned.
            </div>
          </div>

          <div className="fields">
            <div className="field">
              <label htmlFor="subject_type">Subject type</label>
              <select id="subject_type" value={type}
                      onChange={(e) => { setType(e.target.value); setResult(null) }}>
                {/* From `GET /reference` — the same rows the foreign keys
                    enforce, so this cannot offer a type the engine refuses. */}
                {subjectTypes.map((s) => (
                  <option key={s.value} value={s.value}>{s.value}</option>
                ))}
                {subjectTypes.length === 0 && <option value={type}>{type}</option>}
              </select>
            </div>
            <div className="field">
              <label htmlFor="subject_id">Subject id</label>
              <input id="subject_id" value={id} onChange={(e) => setId(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="lane">Lane</label>
              <select id="lane" value={lane}
                      onChange={(e) => setLane(e.target.value as Lane)}>
                {(['inline_sync', 'async'] as Lane[]).map((l) => (
                  <option key={l} value={l}>
                    {l}{evaluable.length > 0 && !evaluable.includes(l)
                      ? ' — no rule scores this subject here' : ''}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {unevaluable && (
            <div className="notice warn tiny">
              No <b>{lane}</b> rule targets a <b>{type}</b>, so the planner has
              nothing to evaluate and this will come back
              <code> SubjectNotEvaluable</code>. That is the lane distinction
              doing its job rather than a missing id:{' '}
              {evaluable.length === 1
                ? <>this subject type is scored in <b>{evaluable[0]}</b>.</>
                : <>it is scored in {evaluable.join(' and ')}.</>}
            </div>
          )}

          <div>
            <button className="btn" onClick={() => setAdvanced(!advanced)}>
              {advanced ? 'Hide' : 'Show'} the replay ceiling
            </button>
          </div>

          {advanced && (
            <div className="field">
              <label htmlFor="replay">Replay ceiling (optional)</label>
              <input id="replay" value={replay} placeholder="2026-01-15T14:07:11Z"
                     onChange={(e) => setReplay(e.target.value)} />
              <div className="tiny dim" style={{ marginTop: 5 }}>
                <b>Leave blank for today's answer.</b> Set a past date to see what
                the system actually knew at that moment — useful when data has
                been corrected since.
              </div>
              <div className="notice warn tiny" style={{ marginTop: 8 }}>
                <b>It will return 0 on this dataset, and that is correct.</b> The
                ceiling bounds <i>when a feature was computed</i>, not when the
                event happened. Everything here was computed in one pass at build
                time, so any ceiling set back in the sample data's own era is
                before every feature row exists and the engine correctly finds
                nothing to score. The control becomes meaningful once features
                have been computed at more than one point in time — which is the
                audit question it exists for, and not something a fresh build
                has yet had.
              </div>
            </div>
          )}

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

        {/* §10. Pre-empts the question this panel always gets — "why didn't the
            mule-ring rule fire?" — and the answer is not a limitation of the
            simulator but of what a single charge can possibly show. */}
        <div className="notice tiny" style={{ marginBottom: 12 }}>
          <b>Only fast-lane transaction rules can fire here.</b> A single made-up
          charge is judged the way a card terminal would judge it — on its own, in
          milliseconds. Patterns that need several transactions to become visible
          (mule rings, account takeover) run in the slower lane and cannot be
          triggered by one hypothetical charge.
        </div>

        <div className="notice warn tiny" style={{ marginBottom: 12 }}>
          <b>A lone charge has no burst behind it.</b> The scoped feature pass is
          bounded at this charge's own instant, so a count like{' '}
          <code>card_cnp_count</code> reads <b>1</b> and not 5 — there is one
          card-not-present charge in the window, because you just invented it. A
          rule needing three will not fire on one, which is the rule being right.
          To see a burst, send five charges twenty seconds apart on the{' '}
          <Link to="/authorize">Send a charge</Link> screen.
        </div>

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
