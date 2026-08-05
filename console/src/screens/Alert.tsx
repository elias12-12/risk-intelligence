/**
 * One case, and everything the system can say about it.
 *
 * The bar comes from the shared `ScoreBar`, the frame from `DecisionFrame`, the
 * copilot lines and the case report from `explanation.v1` — all of them rendered
 * as the payload wrote them. §13's constraint 2 is that contributions, scores
 * and thresholds are *quoted, never restated*, and the server enforces it with a
 * `Quoter` every number passes through. This screen keeps that true by printing
 * the lines it is given rather than assembling sentences of its own.
 */
import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type {
  AlertDetail, CaseReport, CaseVerdict, CopilotResponse, ExecutionRecord,
} from '../api/types'
import { Band, Empty, ErrorNotice, Kv, Loading } from '../components/bits'
import { ActionRow, DecisionFrame, EvidenceBlock } from '../components/DecisionFrame'
import { EmptyPoolNote, ScoreBar } from '../components/ScoreBar'
import { duration, when } from '../format'
import { useSession } from '../session'
import { useAsync } from '../useAsync'

const DISPOSITIONS = [
  { value: 'confirmed_fraud', label: 'Confirmed fraud' },
  { value: 'false_positive', label: 'False positive' },
  { value: 'confirmed_legit', label: 'Confirmed legitimate' },
  { value: 'inconclusive', label: 'Inconclusive' },
] as const

export function AlertScreen() {
  const { id } = useParams<{ id: string }>()
  const alertId = Number(id)

  const alert = useAsync<AlertDetail>(() => api.alert(alertId), [alertId])
  const executions = useAsync<ExecutionRecord[]>(() => api.executions(alertId), [alertId])
  const copilot = useAsync<CopilotResponse>(() => api.copilot(alertId), [alertId])
  const verdict = useAsync<CaseVerdict>(() => api.verdict(alertId), [alertId])

  if (alert.loading) return <div className="page"><Loading what="the case" /></div>
  if (alert.error) return <div className="page"><ErrorNotice error={alert.error} /></div>
  if (!alert.data) return <div className="page"><Empty>No such case.</Empty></div>

  const a = alert.data

  // Every one of these is `default_factory=list` on the server, which makes it
  // OPTIONAL in the JSON Schema and therefore possibly-absent in the generated
  // client — even though the server always serialises it. Defaulting to the
  // same empty the server would have sent is the honest reading; asserting
  // presence with `!` would be the console deciding it knows better than the
  // published schema.
  const signals = a.signals ?? []
  const subjects = a.subjects ?? []
  const rulesFired = a.rules_fired ?? []

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <div className="row tight" style={{ marginBottom: 4 }}>
            <Link to="/" className="small">← queue</Link>
          </div>
          <h1>{a.title}</h1>
          <p>
            Case <b>#{a.alert_id}</b> · decision <b>{a.decision_id}</b> ·{' '}
            <span className="id">{a.subject.type} {a.subject.id}</span> ·{' '}
            {a.execution_mode} lane
          </p>
        </div>
        <div className="row">
          <Band band={a.band} />
          <span className="chip">{a.status}</span>
        </div>
      </div>

      <div className="split">
        <div className="stack">
          <DecisionFrame persisted>
            <div className="score-head">
              <div className={`score-value ${a.score === '0' ? 'zero' : ''}`}>{a.score}</div>
              <Band band={a.band} />
              <div className="small muted">
                occurred {when(a.occurred_at)} · decided {when(a.decided_at)}
              </div>
            </div>

            <div style={{ margin: '14px 0' }}>
              <ActionRow action={a.action} />
            </div>

            <ScoreBar signals={signals} score={String(a.score)} />
            {signals.length === 0 && <div style={{ marginTop: 12 }}><EmptyPoolNote /></div>}
          </DecisionFrame>

          <CopilotPanel state={copilot} />

          <ExecutionsPanel state={executions} />

          <ReportPanel alertId={alertId} />
        </div>

        <div className="stack">
          <div className="panel">
            <h4 style={{ marginBottom: 10 }}>Evidence</h4>
            <EvidenceBlock evidence={a.evidence} />
          </div>

          {subjects.length > 0 && (
            <div className="panel">
              <h4 style={{ marginBottom: 10 }}>Subjects on this case</h4>
              <div className="stack" style={{ gap: 6 }}>
                {subjects.map((s) => (
                  <div key={`${s.type}:${s.id}`} className="row tight">
                    <span className="id">{s.id}</span>
                    <span className="chip off">{s.type}</span>
                    {s.role && <span className="tiny dim">{s.role}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {rulesFired.length > 0 && (
            <div className="panel">
              <h4 style={{ marginBottom: 10 }}>Rules that fired</h4>
              <div className="row tight">
                {rulesFired.map((r) => (
                  <Link key={r} to={`/rules/${r}`} className="chip">{r}</Link>
                ))}
              </div>
            </div>
          )}

          <DispositionPanel alertId={alertId} state={verdict} />
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ copilot */

function CopilotPanel({ state }: { state: ReturnType<typeof useAsync<CopilotResponse>> }) {
  if (state.loading) return <div className="panel"><Loading what="the copilot" /></div>
  if (state.error || !state.data) return null
  const c = state.data

  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="grow">Copilot</h2>
        <span className="chip off">{c.model_backed ? 'model-backed' : 'no model involved'}</span>
      </div>

      <div className="stack" style={{ gap: 12 }}>
        {c.answers.map((ans) => (
          <div key={ans.chip}>
            <h4>{ans.question}</h4>
            <div className="stack" style={{ gap: 3, marginTop: 6 }}>
              {ans.lines.map((line, i) => (
                <div key={i} className="small">{line}</div>
              ))}
            </div>
            {ans.score_quoted !== null && ans.score_quoted !== undefined && (
              <div className="tiny dim" style={{ marginTop: 5 }}>
                quotes the score · {ans.mitigating_cited}/{ans.mitigating_total} mitigating
                and {ans.veto_cited}/{ans.veto_total} veto signals cited
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="tiny dim" style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--line-soft)' }}>
        {c.method}
        {(c.reads ?? []).length > 0 && <> · reads only: {(c.reads ?? []).join(', ')}</>}
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- executions */

function ExecutionsPanel({ state }: { state: ReturnType<typeof useAsync<ExecutionRecord[]>> }) {
  if (state.loading || !state.data) return null
  const rows = state.data
  if (rows.length === 0) {
    return (
      <div className="panel">
        <h2 style={{ marginBottom: 8 }}>What was done</h2>
        <Empty>Nothing was issued for this case.</Empty>
      </div>
    )
  }
  const anySynthetic = rows.some((r) => r.synthetic)
  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="grow">What was done</h2>
        {anySynthetic && <span className="chip">outcome settled by a script</span>}
      </div>
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th>Action</th><th>Channel</th><th>Issued</th>
              <th>Outcome</th><th className="num">Latency</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.execution_id}>
                <td><span className="chip">{r.action}</span></td>
                <td className="mono">{r.channel ?? '—'}</td>
                <td className="tiny">{when(r.issued_at)}</td>
                <td>
                  {r.outcome
                    ? <>
                        <b>{r.outcome}</b>
                        <div className="tiny dim">
                          via {r.outcome_source}
                          {r.synthetic && ' · synthetic'}
                        </div>
                      </>
                    : <span className="dim">unresolved</span>}
                </td>
                <td className="num tiny">{duration(r.latency_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {anySynthetic && (
        <div className="tiny dim" style={{ marginTop: 10 }}>
          Nothing external answers a step-up here, so `resolve_actions.py` settles
          challenge outcomes deterministically against planted ground truth. Every
          such row is stamped synthetic, and no rate computed from them is a
          measured one.
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------- report */

function ReportPanel({ alertId }: { alertId: number }) {
  const [report, setReport] = useState<CaseReport | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  const generate = useCallback(async () => {
    setBusy(true)
    try {
      setReport(await api.report(alertId))
      setError(null)
    } catch (err) { setError(err as Error) } finally { setBusy(false) }
  }, [alertId])

  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="grow">Case report</h2>
        <button className="small" onClick={() => void generate()} disabled={busy}>
          {busy ? 'generating…' : report ? 'regenerate' : 'generate'}
        </button>
      </div>
      <ErrorNotice error={error} />
      {!report ? (
        <div className="small muted">
          A filing draft, templated over this case's stored rows. Every number in
          it carries the table and primary key it came from.
        </div>
      ) : (
        <div className="stack">
          {report.draft && <div className="notice warn">{report.draft_notice}</div>}
          {(report.unresolvable_versions ?? []).length > 0 && (
            <div className="notice warn">
              Version numbers with no stored definition behind them:{' '}
              {(report.unresolvable_versions ?? []).join(', ')}
            </div>
          )}
          <pre className="md">{report.markdown}</pre>
          <details>
            <summary className="small muted" style={{ cursor: 'pointer' }}>
              {(report.citations ?? []).length} citations
            </summary>
            <div className="scroll-x" style={{ marginTop: 8 }}>
              <table>
                <thead><tr><th>Value</th><th>Label</th><th>Source</th><th>Key</th></tr></thead>
                <tbody>
                  {(report.citations ?? []).map((c, i) => (
                    <tr key={i}>
                      <td className="mono">{c.value}</td>
                      <td className="tiny">{c.label}</td>
                      <td className="tiny mono">{c.source}</td>
                      <td className="tiny mono dim">{c.key}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
    </div>
  )
}

/* -------------------------------------------------------------- disposition */

/**
 * The analyst's verdict.
 *
 * §11 flags a console string that outran the system: an escalation toast
 * promising that a verdict feeds a future retrain of a model. There is no model
 * here — `alert_signals.source_model` has never been written and no payload
 * field claims otherwise — so the copy beside `feeds_retrain` says what the flag
 * actually does, which is mark the row as usable as a training label if anything
 * ever trains. That is the whole fix, and it is a sentence rather than a feature.
 *
 * (The flagged string is deliberately not quoted verbatim anywhere in this
 * package: `console.test.ts` greps the sources for it, and a test that its own
 * subject can defeat by being cited is not a test.)
 */
function DispositionPanel({ alertId, state }: {
  alertId: number
  state: ReturnType<typeof useAsync<CaseVerdict>>
}) {
  const { principal } = useSession()
  const [choice, setChoice] = useState<string>('false_positive')
  const [notes, setNotes] = useState('')
  const [feeds, setFeeds] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try {
      await api.disposition(alertId, { disposition: choice, notes: notes || null, feeds_retrain: feeds })
      setNotes('')
      setError(null)
      state.reload()
    } catch (err) { setError(err as Error) } finally { setBusy(false) }
  }

  const v = state.data

  return (
    <div className="panel">
      <h2 style={{ marginBottom: 10 }}>Verdict</h2>

      {v && v.dispositions > 0 && (
        <div className="stack" style={{ gap: 8, marginBottom: 14 }}>
          <Kv rows={[
            ['current', <b>{v.verdict}</b>],
            ['written by', v.verdict_source === 'analyst' ? 'an analyst' : 'the synthetic settler'],
            ['first decided', when(v.decided_at)],
            ['dispositions', String(v.dispositions)],
          ]} />
          {v.dispositions > 1 && (
            <div className="tiny dim">
              Append-only: the latest is the verdict, and the triage clock stays on
              the first. A correction hours later does not mean triage took hours
              longer.
            </div>
          )}
          {(v.history ?? []).length > 1 && (
            <details>
              <summary className="tiny muted" style={{ cursor: 'pointer' }}>history</summary>
              <div className="timeline" style={{ marginTop: 8 }}>
                {(v.history ?? []).map((h) => (
                  <div className="timeline-item" key={h.outcome_id}>
                    <span className="when">{when(h.decided_at)}</span>
                    <span>
                      <b>{h.disposition}</b>{' '}
                      <span className="dim">— {h.analyst_id ?? h.source}</span>
                      {h.notes && <div className="tiny muted">{h.notes}</div>}
                    </span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {!principal ? (
        <div className="notice">Sign in to write a verdict.</div>
      ) : (
        <div className="stack" style={{ gap: 10 }}>
          <div className="field">
            <label htmlFor="disposition">Disposition</label>
            <select id="disposition" value={choice} onChange={(e) => setChoice(e.target.value)}>
              {DISPOSITIONS.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="notes">Notes</label>
            <textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)}
                      placeholder="what you concluded, and why" />
          </div>
          <div className="checkline">
            <input id="feeds" type="checkbox" checked={feeds}
                   onChange={(e) => setFeeds(e.target.checked)} />
            <label htmlFor="feeds">usable as a training label</label>
          </div>
          <div className="tiny dim">
            That flag marks the row and nothing more. There is no model in this
            system and nothing retrains — <code>alert_signals.source_model</code> is
            a seam that has never been written to.
          </div>
          <ErrorNotice error={error} />
          <button className="primary" onClick={() => void submit()} disabled={busy}>
            {busy ? 'writing…' : 'Write verdict'}
          </button>
          <div className="tiny dim">
            Appended as a new row, attributed to <b>{principal.actor}</b> from the
            token — never from this form. The case's engine-owned status does not
            move.
          </div>
        </div>
      )}
    </div>
  )
}
