/**
 * The frame that says whether what you are looking at happened.
 *
 * Session 5's note 4: three payloads render the same bar and one of them is not
 * real. `persisted` is the field that distinguishes a thing that happened from a
 * thing that would have, and the plan's acceptance criterion is that the two are
 * *never visually confusable* — so this is not a small grey label. A simulated
 * decision gets a dashed, hatched frame and a header sentence that says nothing
 * was written, and it gets them from the payload's own `persisted` field rather
 * than from which screen is rendering it. A screen that knew it was showing a
 * simulation and styled itself accordingly would be asserting the guarantee
 * instead of reading it — and `/simulate/*` publishing `persisted: false` on the
 * wire exists precisely so a caller does not have to infer it from the URL.
 */
import type { ReactNode } from 'react'

import { Kv } from './bits'
import { when } from '../format'
import type { Action, Evidence } from '../api/types'

export function DecisionFrame({
  persisted, stopped = false, children, note,
}: {
  persisted: boolean
  /** True when this decision refused a charge. A stronger frame again: it is the
   *  only thing in the console that represents money not moving. */
  stopped?: boolean
  note?: ReactNode
  children: ReactNode
}) {
  const kind = !persisted ? 'unreal' : stopped ? 'stopped' : 'real'
  return (
    <div className={`frame ${kind}`} data-testid="decision-frame" data-persisted={persisted}>
      <div className="frame-head">
        {!persisted ? (
          <>
            <span>◇ NOT PERSISTED</span>
            <span className="dim" style={{ fontWeight: 400 }}>
              the engine's answer, rolled back — no decision, alert, execution or
              feature value was written
            </span>
          </>
        ) : stopped ? (
          <>
            <span>■ DECLINED — this charge was stopped</span>
            <span className="dim" style={{ fontWeight: 400 }}>
              committed; the row carries the decision
            </span>
          </>
        ) : (
          <>
            <span>● STORED DECISION</span>
            <span className="dim" style={{ fontWeight: 400 }}>this happened</span>
          </>
        )}
        {note}
      </div>
      <div className="frame-body">{children}</div>
    </div>
  )
}

/**
 * What the system decided to do, and which rule authorised it.
 *
 * `action.source_rule` is non-null whenever the action is not `allow` — one of
 * alert.v1's two enforced invariants — so naming it is never optional. A veto is
 * rendered beside it because a capped decision with nothing on screen joining
 * the high score to the soft action is exactly the invisible policy §7 objects
 * to.
 */
export function ActionRow({ action }: { action: Action }) {
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row">
        <span className="chip on" style={{ fontSize: 12, padding: '3px 10px' }}>
          {action.taken}
        </span>
        {action.source_rule && (
          <span className="small muted">carried by <b className="id">{action.source_rule}</b></span>
        )}
        {action.vetoed_by && (
          <span className="chip veto">vetoed by {action.vetoed_by}</span>
        )}
        {action.prevent_threshold_met === true && (
          <span className="chip small">prevent threshold met</span>
        )}
        {action.prevent_threshold_met === false && (
          <span className="chip off">prevent threshold not met</span>
        )}
      </div>
      {action.recommended_text && (
        <div className="small muted">
          <b className="dim">Recommended · </b>{action.recommended_text}
        </div>
      )}
      {action.clear_text && (
        <div className="small muted">
          <b className="dim">What would clear it · </b>{action.clear_text}
        </div>
      )}
    </div>
  )
}

/**
 * What the decision could see, and what it could not.
 *
 * `degraded_features` is rendered as names, never as a count: §5's whole
 * argument is that a decision made on partial evidence must not look identical
 * to a clean one, and "3 degraded features" is a decision that looks clean with
 * a number next to it.
 */
export function EvidenceBlock({ evidence }: { evidence: Evidence }) {
  const rules = Object.entries(evidence.rule_version_set ?? {})
  const features = Object.entries(evidence.feature_version_set ?? {})
  const degraded = evidence.degraded_features ?? []

  return (
    <div className="stack" style={{ gap: 10 }}>
      {degraded.length > 0 && (
        <div className="notice warn">
          <b>Decided on degraded evidence.</b> These features were absent or
          staler than their <code>max_staleness</code> when this ran:{' '}
          {degraded.join(', ')}. An aggravating condition contributes 0; a
          mitigating one contributes 0 <i>and</i> strips the rule's preventive
          authority, because the system may look when it cannot see the evidence
          that would exonerate someone — it may not act.
        </div>
      )}
      <Kv rows={[
        ['evaluation', evidence.evaluation_id],
        ['trigger', evidence.evaluation_trigger],
        ['trigger row', evidence.trigger_type && evidence.trigger_id
          ? `${evidence.trigger_type} ${evidence.trigger_id}` : null],
        ['point-in-time bound', when(evidence.pit_bound_at)],
        ['replay ceiling', evidence.replay_as_of ? when(evidence.replay_as_of) : null],
        ['rule versions', rules.length
          ? rules.map(([r, v]) => `${r} v${v}`).join(', ') : null],
        ['feature versions', features.length
          ? `${features.length} specs — ${features.map(([f, v]) => `${f} v${v}`).join(', ')}`
          : null],
      ]} />
    </div>
  )
}
