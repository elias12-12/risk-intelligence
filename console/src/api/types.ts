/**
 * Names for the payload shapes, all of them pointing at the generated schema.
 *
 * There is deliberately no hand-written interface in this file. Every type is an
 * alias into `schema.d.ts`, which `npm run types` regenerates from
 * `openapi.json`, which `scripts/export_openapi.py` regenerates from the app.
 * A second definition of a payload shape is the failure mode WEEK5-PLAN
 * decisions 13 and 27 exist to prevent, and a console is a very easy place to
 * reintroduce it — someone types out `interface Signal` because the import is
 * three levels deep, and now the bar has two definitions.
 */
import type { components } from './schema'

type S = components['schemas']

// alert.v1 — frozen. Signal / Action / Evidence / Subject live in its $defs
// closure and are REUSED READ-ONLY by every sibling below (decision 8).
export type Subject = S['Subject']
export type Signal = S['Signal']
export type Action = S['Action']
export type Evidence = S['Evidence']
export type AlertSummary = S['AlertSummary']
export type AlertDetail = S['AlertDetail']

// queue.v1
export type QueueEntry = S['QueueEntry']

// executions.v1
export type ExecutionRecord = S['ExecutionRecord']

// kpis.v1
export type KpiSet = S['KpiSet']
export type KpiTile = S['KpiTile']
export type KpiPart = S['KpiPart']

// explanation.v1
export type CopilotResponse = S['CopilotResponse']
export type CopilotAnswer = S['CopilotAnswer']
export type CaseReport = S['CaseReport']
export type Citation = S['Citation']

// dispositions.v1
export type CaseVerdict = S['CaseVerdict']
export type Disposition = S['Disposition']
export type DispositionRequest = S['DispositionRequest']

// catalog.v1 — the control plane
export type RuleSummary = S['RuleSummary']
export type RuleDetail = S['RuleDetail']
export type ConditionView = S['ConditionView']
export type ConditionPerformance = S['ConditionPerformance']
export type FeatureView = S['FeatureView']
export type ReferenceVocabulary = S['ReferenceVocabulary']
export type ReasonCodeValue = S['ReasonCodeValue']
export type RuleDraft = S['RuleDraft']
export type ConditionDraft = S['ConditionDraft']

// simulation.v1 — what the engine WOULD say
export type SimulatedDecision = S['SimulatedDecision']
export type RuleTrace = S['RuleTrace']
export type RuleSimulation = S['RuleSimulation']
export type TransactionSimulation = S['TransactionSimulation']
export type TransactionDraft = S['TransactionDraft']
export type DecisionDiff = S['DecisionDiff']
export type WorkedExample = S['WorkedExample']
export type FabricationLimit = S['FabricationLimit']

// ingest.v1 — what actually happened
export type AuthorizationRequest = S['AuthorizationRequest']
export type AuthorizationOutcome = S['AuthorizationOutcome']
export type ExecutionIssued = S['ExecutionIssued']
export type CycleReport = S['CycleReport']
export type RescoreReport = S['RescoreReport']
export type IngestReceipt = S['IngestReceipt']

/**
 * `GET /cycle` is the one endpoint with no `response_model`, so OpenAPI types it
 * as an open object and the generator can only say so. Declared here rather than
 * left as `unknown`, and marked as what it is: the single place in this client
 * where a shape is asserted rather than derived.
 *
 * Invariant 8 says nothing may assert liveness. That is about the VALUE, not the
 * shape — every field below is read off the payload and none of them is
 * defaulted to a cheerful answer. `scheduler_running` in particular has no
 * fallback anywhere in this console: if the strip cannot reach `/cycle`, it says
 * it cannot reach `/cycle`.
 */
export interface CycleState {
  scheduler_running: boolean
  interval_seconds: number | null
  started_at: string | null
  frontier: string | null
  streams: Record<string, string | null> | Array<Record<string, unknown>>
  recent_ticks: Array<{
    at?: string | null
    ran?: boolean
    reason?: string | null
    decisions?: number | null
    duration_ms?: number | null
    [k: string]: unknown
  }>
}

/** Who the presented token is. `GET /me`. */
export interface Principal {
  actor: string
  role: 'analyst' | 'admin'
}

/**
 * The three payloads that render the same bar (session 5's note 4).
 *
 * `AlertDetail` is a thing that happened. `AuthorizationOutcome` is a thing that
 * happened *and touched money*. `SimulatedDecision` is a thing that would have.
 * They share `Signal`, `Action` and `Evidence` unmodified and deliberately, so
 * ONE component renders all three — and `persisted` is the field that keeps them
 * from being confusable.
 */
export type Decisionish = AlertDetail | AuthorizationOutcome | SimulatedDecision

/** True only when the payload says so. An alert has no `persisted` field — it is
 *  a stored row by construction — so absence means stored, and the only thing
 *  that ever reads as *not* persisted is an explicit `persisted: false`. */
export function isPersisted(d: Decisionish): boolean {
  return !('persisted' in d) || d.persisted !== false
}
