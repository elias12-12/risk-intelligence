/**
 * Author a rule, test it against history, then publish it into shadow.
 *
 * The order on this screen is the argument. **Test** posts to
 * `/simulate/rule`, which applies the draft inside a scope that rolls back and
 * runs the ordinary pipeline against real history — no "simulation mode" in the
 * evaluator, because a second evaluation path would be a second answer.
 * **Publish** posts to `/rules`, which writes the definition, snapshots it into
 * `rule_versions` with the actor who published it, and lands the rule in
 * **shadow**. Promoting it is a separate call on the rule's own page, because it
 * is a separate decision.
 *
 * The two are deliberately different endpoints sharing one validator (decision
 * 6), and the console keeps them different: the test button cannot write and the
 * publish button cannot be reached by pressing it.
 *
 * Every dropdown is filled from `GET /reference`, which serves the same `ref_*`
 * rows the foreign keys enforce and the same operator tuple `conditions.fires()`
 * dispatches on — so this form cannot offer something the validator will refuse.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type {
  ConditionDraft, FeatureView, ReferenceVocabulary, RuleDraft, RuleSimulation,
} from '../api/types'
import { Empty, ErrorNotice, Loading } from '../components/bits'
import { ScoreBar } from '../components/ScoreBar'
import { signed, whenShort } from '../format'
import { useSession } from '../session'
import { useAsync } from '../useAsync'

/** `RuleDraft.conditions` has a `default_factory`, so the schema marks it
 *  optional and the generated type allows it to be absent. A form always has a
 *  list — possibly empty — so the local state narrows it once here rather than
 *  guarding at every one of the dozen places that edit it. */
type Draft = RuleDraft & { conditions: ConditionDraft[] }

const BLANK_CONDITION: ConditionDraft = {
  condition_group: 1,
  feature_key: '',
  operator: '>=',
  threshold_num: null,
  threshold_text: null,
  contribution_points: '10',
  reason_code: null,
  signal_template: null,
  is_required: true,
}

const BLANK_RULE: Draft = {
  rule_id: '',
  name: '',
  description: null,
  subject_type: 'transaction',
  execution_mode: 'inline_sync',
  action: 'alert',
  review_threshold: '50',
  prevent_threshold: null,
  is_veto: false,
  combine: 'AND',
  status: 'shadow',
  evaluation_lag_seconds: 0,
  recommended_action_text: null,
  clear_text: null,
  conditions: [{ ...BLANK_CONDITION }],
}

export function RuleAuthorScreen() {
  const { id } = useParams<{ id: string }>()
  const editing = Boolean(id)
  const { can } = useSession()
  const navigate = useNavigate()

  const reference = useAsync<ReferenceVocabulary>(() => api.reference(), [])
  const features = useAsync<FeatureView[]>(() => api.features(), [])

  const [draft, setDraft] = useState<Draft>(BLANK_RULE)
  const [loaded, setLoaded] = useState(!editing)
  const [sim, setSim] = useState<RuleSimulation | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState<'test' | 'publish' | null>(null)

  useEffect(() => {
    if (!editing) return
    void (async () => {
      try {
        const r = await api.rule(id!)
        setDraft({
          rule_id: r.rule_id,
          name: r.name,
          description: r.description,
          subject_type: r.subject_type!,
          execution_mode: r.execution_mode!,
          action: r.action!,
          review_threshold: String(r.review_threshold ?? '50'),
          prevent_threshold: r.prevent_threshold === null || r.prevent_threshold === undefined
            ? null : String(r.prevent_threshold),
          is_veto: r.is_veto,
          combine: r.combine,
          // Decision 25: an edit may not change status. The form does not offer
          // it and the body echoes what the rule already is.
          status: r.status,
          evaluation_lag_seconds: r.evaluation_lag_seconds,
          recommended_action_text: r.recommended_action_text,
          clear_text: r.clear_text,
          conditions: (r.condition_set ?? []).map((c) => ({
            condition_group: c.condition_group,
            feature_key: c.feature_key,
            operator: c.operator,
            threshold_num: c.threshold_num === null || c.threshold_num === undefined
              ? null : String(c.threshold_num),
            threshold_text: c.threshold_text,
            contribution_points: String(c.contribution_points),
            reason_code: c.reason_code,
            signal_template: c.signal_template,
            is_required: c.is_required,
          })),
        })
        setLoaded(true)
      } catch (err) { setError(err as Error); setLoaded(true) }
    })()
  }, [editing, id])

  const featureKeys = useMemo(
    () => (features.data ?? []).map((f) => f.feature_key).sort(),
    [features.data])

  const featureByKey = useMemo(
    () => Object.fromEntries((features.data ?? []).map((f) => [f.feature_key, f])),
    [features.data])

  if (!can('admin')) {
    return (
      <div className="page">
        <div className="notice bad">
          <b>This needs the admin role.</b> Authoring a rule writes to the control
          plane, and the control plane decides what happens to customers.
        </div>
      </div>
    )
  }

  if (!loaded || reference.loading) return <div className="page"><Loading what="the vocabularies" /></div>

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }))
  const setCondition = (i: number, patch: Partial<ConditionDraft>) =>
    setDraft((d) => ({
      ...d,
      conditions: d.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)),
    }))

  const test = async () => {
    setBusy('test')
    try {
      setSim(await api.simulateRule({ rule: draft, examples: 4, diff_limit: 20 }))
      setError(null)
    } catch (err) { setError(err as Error); setSim(null) } finally { setBusy(null) }
  }

  const publish = async () => {
    setBusy('publish')
    try {
      const saved = editing
        ? await api.updateRule(draft.rule_id, draft)
        : await api.createRule(draft)
      setError(null)
      navigate(`/rules/${saved.rule_id}`)
    } catch (err) { setError(err as Error) } finally { setBusy(null) }
  }

  const ref = reference.data

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <div className="row tight" style={{ marginBottom: 4 }}>
            <Link to="/rules" className="small">← rules</Link>
          </div>
          <h1>{editing ? `Edit ${draft.rule_id}` : 'Author a rule'}</h1>
          <p>
            {editing
              ? 'A save that changes nothing is not a new version. A save that '
                + 'moves the definition bumps the counter and keeps the previous '
                + 'definition retrievable.'
              : 'A new rule lands in shadow: scored on every applicable subject, '
                + 'every condition it looks at recorded, and acting on nobody. '
                + 'Promoting it is a separate decision on its own page.'}
          </p>
        </div>
      </div>

      <div className="split">
        <div className="stack">
          <div className="panel">
            <h2 style={{ marginBottom: 12 }}>Definition</h2>
            <div className="stack" style={{ gap: 12 }}>
              <div className="fields">
                <div className="field">
                  <label htmlFor="rule_id">Rule id</label>
                  <input id="rule_id" value={draft.rule_id} disabled={editing}
                         placeholder="C-301"
                         onChange={(e) => set({ rule_id: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="name">Name</label>
                  <input id="name" value={draft.name}
                         onChange={(e) => set({ name: e.target.value })} />
                </div>
              </div>

              <div className="field">
                <label htmlFor="description">Description</label>
                <input id="description" value={draft.description ?? ''}
                       onChange={(e) => set({ description: e.target.value || null })} />
              </div>

              <div className="fields">
                <Select id="subject_type" label="Subject" value={draft.subject_type}
                        onChange={(v) => set({ subject_type: v })}
                        options={(ref?.subject_types ?? []).map((s) => s.value)} />
                <Select id="execution_mode" label="Lane" value={draft.execution_mode}
                        onChange={(v) => set({ execution_mode: v })}
                        options={(ref?.execution_modes ?? []).map((s) => s.value)} />
                <Select id="action" label="Action" value={draft.action}
                        onChange={(v) => set({ action: v })}
                        options={(ref?.actions ?? []).map((a) => a.action)} />
                <Select id="combine" label="Combine" value={draft.combine}
                        onChange={(v) => set({ combine: v })}
                        options={ref?.combine_values ?? ['AND', 'OR']} />
              </div>

              <div className="fields">
                <div className="field">
                  <label htmlFor="review">Review threshold</label>
                  <input id="review" value={String(draft.review_threshold)}
                         onChange={(e) => set({ review_threshold: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="prevent">Prevent threshold</label>
                  <input id="prevent" value={draft.prevent_threshold === null
                            || draft.prevent_threshold === undefined
                            ? '' : String(draft.prevent_threshold)}
                         placeholder="none — cannot prevent"
                         onChange={(e) => set({ prevent_threshold: e.target.value || null })} />
                </div>
                <div className="field">
                  <label htmlFor="lag">Evaluation lag (s)</label>
                  <input id="lag" value={String(draft.evaluation_lag_seconds)}
                         onChange={(e) => set({ evaluation_lag_seconds: Number(e.target.value) || 0 })} />
                </div>
              </div>

              <div className="checkline">
                <input id="is_veto" type="checkbox" checked={draft.is_veto}
                       onChange={(e) => set({ is_veto: e.target.checked })} />
                <label htmlFor="is_veto">
                  This is a veto rule — it caps severity when its conditions hold,
                  regardless of its score
                </label>
              </div>

              <div className="fields">
                <div className="field">
                  <label htmlFor="rec">Recommended action text</label>
                  <textarea id="rec" value={draft.recommended_action_text ?? ''}
                            onChange={(e) => set({ recommended_action_text: e.target.value || null })} />
                </div>
                <div className="field">
                  <label htmlFor="clear">What would clear it</label>
                  <textarea id="clear" value={draft.clear_text ?? ''}
                            onChange={(e) => set({ clear_text: e.target.value || null })} />
                </div>
              </div>

              <div className="tiny dim">
                Both are quoted verbatim wherever they appear — the sentence
                belongs to whoever authored the rule.
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2 className="grow">Conditions</h2>
              <button className="small"
                      onClick={() => setDraft((d) => ({
                        ...d, conditions: [...d.conditions, { ...BLANK_CONDITION }] }))}>
                add condition
              </button>
            </div>

            <div className="stack">
              {draft.conditions.map((c, i) => (
                <div className="panel" key={i} style={{ background: 'var(--panel-2)' }}>
                  <div className="fields">
                    <div className="field">
                      <label htmlFor={`feature-${i}`}>Feature</label>
                      <select id={`feature-${i}`} value={c.feature_key}
                              onChange={(e) => setCondition(i, { feature_key: e.target.value })}>
                        <option value="">choose…</option>
                        {featureKeys.map((k) => <option key={k} value={k}>{k}</option>)}
                      </select>
                      {/* What the feature MEANS and whether it can be observed
                          absent — moved here from the catalog table (§11),
                          because this is the one screen where the distinction
                          changes what you write. A mitigator may only cite a
                          feature with no default; seeing which is which while
                          choosing beats being refused by the validator after. */}
                      {featureByKey[c.feature_key] && (
                        <div className="tiny dim" style={{ marginTop: 5 }}>
                          {featureByKey[c.feature_key].description}
                          <div style={{ marginTop: 3 }}>
                            {featureByKey[c.feature_key].has_default
                              ? <>defaults to{' '}
                                  <span className="mono">
                                    {JSON.stringify(featureByKey[c.feature_key].default_when_absent)}
                                  </span>{' '}
                                  when missing — so it can never be observed absent,
                                  and a mitigator may not cite it</>
                              : <b>no default — observable as absent, so a mitigator may cite it</b>}
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="field">
                      <label htmlFor={`op-${i}`}>Operator</label>
                      <select id={`op-${i}`} value={c.operator}
                              onChange={(e) => setCondition(i, { operator: e.target.value })}>
                        {(ref?.operators ?? []).map((o) => (
                          <option key={o.operator} value={o.operator}>
                            {o.operator} — {o.takes}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor={`num-${i}`}>Threshold (number)</label>
                      <input id={`num-${i}`}
                             value={c.threshold_num === null || c.threshold_num === undefined
                               ? '' : String(c.threshold_num)}
                             onChange={(e) => setCondition(i, { threshold_num: e.target.value || null })} />
                    </div>
                    <div className="field">
                      <label htmlFor={`txt-${i}`}>Threshold (text)</label>
                      <input id={`txt-${i}`} value={c.threshold_text ?? ''}
                             onChange={(e) => setCondition(i, { threshold_text: e.target.value || null })} />
                    </div>
                  </div>

                  <div className="fields" style={{ marginTop: 10 }}>
                    <div className="field">
                      <label htmlFor={`pts-${i}`}>Points</label>
                      <input id={`pts-${i}`} value={String(c.contribution_points)}
                             onChange={(e) => setCondition(i, { contribution_points: e.target.value })} />
                    </div>
                    <div className="field">
                      <label htmlFor={`grp-${i}`}>Group</label>
                      <input id={`grp-${i}`} value={String(c.condition_group)}
                             onChange={(e) => setCondition(i, {
                               condition_group: Number(e.target.value) || 1 })} />
                    </div>
                    <div className="field">
                      <label htmlFor={`rc-${i}`}>Reason code</label>
                      <select id={`rc-${i}`} value={c.reason_code ?? ''}
                              onChange={(e) => setCondition(i, { reason_code: e.target.value || null })}>
                        <option value="">none</option>
                        {(ref?.reason_codes ?? []).map((r) => (
                          <option key={r.value} value={r.value}>{r.value}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div className="field" style={{ marginTop: 10 }}>
                    <label htmlFor={`tpl-${i}`}>Signal template</label>
                    <input id={`tpl-${i}`} value={c.signal_template ?? ''}
                           placeholder="the sentence an analyst reads — {v} interpolates the value"
                           onChange={(e) => setCondition(i, { signal_template: e.target.value || null })} />
                  </div>

                  <div className="spread" style={{ marginTop: 10 }}>
                    <div className="checkline">
                      <input id={`req-${i}`} type="checkbox" checked={c.is_required}
                             onChange={(e) => setCondition(i, { is_required: e.target.checked })} />
                      <label htmlFor={`req-${i}`}>required for the rule to be satisfied</label>
                    </div>
                    <div className="row tight">
                      <span className={`chip ${String(c.contribution_points).startsWith('-') ? 'mit' : 'agg'}`}>
                        {String(c.contribution_points).startsWith('-') ? 'mitigating' : 'aggravating'}
                      </span>
                      {draft.conditions.length > 1 && (
                        <button className="small ghost"
                                onClick={() => setDraft((d) => ({
                                  ...d, conditions: d.conditions.filter((_, j) => j !== i) }))}>
                          remove
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div className="tiny dim" style={{ marginTop: 12 }}>
              A negative contribution is a mitigator, and a mitigator may only cite
              a feature with no default — otherwise it can never be observed
              absent, so it can never strip preventive authority, and §5 becomes
              unreachable. The validator refuses one that does.
            </div>
          </div>

          <ErrorNotice error={error} />

          <div className="panel">
            <div className="row">
              <button onClick={() => void test()} disabled={busy !== null}>
                {busy === 'test' ? 'evaluating history…' : 'Test against history'}
              </button>
              <button className="primary" onClick={() => void publish()} disabled={busy !== null}>
                {busy === 'publish' ? 'publishing…'
                  : editing ? 'Save — publishes a version' : 'Publish into shadow'}
              </button>
              <span className="tiny dim">
                Testing writes nothing. Publishing writes the definition and
                snapshots it.
              </span>
            </div>
          </div>
        </div>

        <div className="stack">
          {sim ? <SimulationResult sim={sim} />
            : <div className="panel">
                <h4 style={{ marginBottom: 8 }}>No test run yet</h4>
                <div className="small muted">
                  <b>Test against history</b> applies this draft inside a scope
                  that rolls back, evaluates real subjects through the ordinary
                  pipeline, and reports what would have happened — including
                  which stored decisions would move.
                </div>
              </div>}
        </div>
      </div>
    </div>
  )
}

function Select({ id, label, value, options, onChange }: {
  id: string; label: string; value: string; options: string[]
  onChange: (value: string) => void
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}

/* ------------------------------------------------------- the what-if result */

function SimulationResult({ sim }: { sim: RuleSimulation }) {
  const p = sim.population
  const g = sim.ground_truth

  return (
    <div className="stack">
      <div className="frame unreal">
        <div className="frame-head">
          <span>◇ NOT PERSISTED</span>
          <span className="dim" style={{ fontWeight: 400 }}>
            evaluated as <b>{sim.evaluated_as}</b>, then rolled back
          </span>
        </div>
        <div className="frame-body stack">
          <div className="notice">{sim.shadow_note}</div>

          <div>
            <h4>Over the sample</h4>
            <div className="tile-parts" style={{ marginTop: 8 }}>
              <div className="tile-part"><span>would fire</span><b>{sim.would_fire}</b></div>
              <div className="tile-part"><span>…and would have authority</span><b>{sim.would_authorise}</b></div>
              <div className="tile-part"><span>…and would carry the action</span><b>{sim.would_carry_action}</b></div>
              <div className="tile-part"><span>would raise a case</span><b>{sim.would_alert}</b></div>
            </div>
            <div className="tiny dim" style={{ marginTop: 8 }}>{sim.hygiene_caveat}</div>
          </div>

          <div>
            <h4>Denominator</h4>
            <div className="small muted" style={{ marginTop: 6 }}>
              {p.subjects_evaluated.toLocaleString()} of{' '}
              {p.subjects_available.toLocaleString()} {p.subject_type} subjects on
              the {p.lane} lane, capped at {p.sample_cap.toLocaleString()}
              {p.truncated ? ' — truncated' : ''}.
            </div>
            <div className="tiny dim" style={{ marginTop: 6 }}>{p.basis}</div>
          </div>

          {g.labelled > 0 && (
            <div>
              <h4>Against planted ground truth</h4>
              <div className="small muted" style={{ marginTop: 6 }}>
                flagged {g.flagged}, of which {g.flagged_labelled} carry a label
                and {g.flagged_on_fraud} are fraud — precision{' '}
                <b>{g.precision_pct ?? '—'}%</b>
              </div>
              <div className="tiny tile-caveat" style={{ marginTop: 6 }}>⚠ {g.caveat}</div>
            </div>
          )}

          {sim.diff_total > 0 && (
            <div>
              <h4>Decisions that would move</h4>
              <div className="small muted" style={{ marginTop: 6 }}>
                {sim.diff_total} in total — {sim.diff_new} new,{' '}
                {sim.diff_score_changed} with a different score,{' '}
                {sim.diff_action_changed} with a different action.
              </div>
              <div className="scroll-x" style={{ marginTop: 8 }}>
                <table>
                  <thead>
                    <tr><th>Subject</th><th>Change</th><th className="num">Score</th><th>Action</th></tr>
                  </thead>
                  <tbody>
                    {(sim.diff ?? []).map((d) => (
                      <tr key={d.subject_id}>
                        <td className="id tiny">{d.subject_id}</td>
                        <td><span className="chip">{d.change}</span></td>
                        <td className="num tiny">
                          {d.stored_score ?? '—'} → <b>{d.simulated_score}</b>
                        </td>
                        <td className="tiny">
                          {d.stored_action ?? '—'} → <b>{d.simulated_action}</b>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="tiny dim">{sim.basis}</div>
        </div>
      </div>

      {(sim.conditions ?? []).length > 0 && (
        <div className="panel">
          <h4 style={{ marginBottom: 10 }}>How each condition behaved</h4>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Condition</th><th className="num">Pts</th>
                  <th className="num">Fired</th><th className="num">Fire rate</th>
                  <th className="num">Precision</th>
                </tr>
              </thead>
              <tbody>
                {(sim.conditions ?? []).map((c, i) => (
                  <tr key={i}>
                    <td>
                      <span className="id tiny">{c.feature_key}</span>
                      <div className="tiny dim">{c.operator} {c.threshold ?? ''} · {c.direction}</div>
                    </td>
                    <td className="num">{signed(String(c.contribution_points))}</td>
                    <td className="num">{c.fired}</td>
                    <td className="num">{c.fire_rate_pct ?? '—'}%</td>
                    <td className="num">{c.precision_pct ?? '—'}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {(sim.examples ?? []).length > 0 && (
        <div className="panel">
          <h4 style={{ marginBottom: 10 }}>Worked examples</h4>
          <div className="stack">
            {(sim.examples ?? []).map((e) => (
              <div key={e.subject_id} className="panel" style={{ background: 'var(--panel-2)' }}>
                <div className="spread" style={{ marginBottom: 8 }}>
                  <span className="id">{e.subject_id}</span>
                  <span className="row tight">
                    <b style={{ fontFamily: 'var(--mono)' }}>{e.score}</b>
                    <span className={`chip band-${e.band}`}>{e.band}</span>
                    <span className="chip">{e.action}</span>
                    {e.label && <span className="chip agg">{e.label}</span>}
                  </span>
                </div>
                <div className="tiny dim" style={{ marginBottom: 8 }}>
                  {whenShort(e.occurred_at)}
                  {e.action_source_rule && ` · action carried by ${e.action_source_rule}`}
                </div>
                <ScoreBar signals={e.signals ?? []} score={String(e.score)} />
              </div>
            ))}
          </div>
        </div>
      )}

      {(sim.examples ?? []).length === 0 && sim.would_fire === 0 && (
        <Empty>This draft fired on nothing in the sample.</Empty>
      )}
    </div>
  )
}
