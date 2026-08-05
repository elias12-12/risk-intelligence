/**
 * The one place this console talks to the API.
 *
 * Everything goes through `/api`, which the Vite dev proxy rewrites onto
 * `:8000` (decision 9) and which FastAPI serves same-origin once the bundle is
 * mounted. There is no base-URL configuration and no CORS: a second way to reach
 * the service is a second thing to get wrong.
 *
 * The bearer token rides on every request that has one. Reads are open — that is
 * `api/auth.py`'s deliberate choice, not an oversight — so the queue, the alert
 * detail and the KPIs all render before anyone signs in. What needs a token is
 * what leaves a mark.
 */
import type {
  AlertDetail, AlertSummary, AuthorizationOutcome, CaseReport, CaseVerdict,
  CopilotResponse, CycleReport, CycleState, ExecutionRecord, FeatureView,
  KpiSet, Principal, QueueEntry, ReferenceVocabulary, RuleDetail, RuleDraft,
  RuleSimulation, RuleSummary, SimulatedDecision, TransactionDraft,
  TransactionSimulation,
} from './types'

export const TOKEN_KEY = 'glassbox.token'

/**
 * `/api` in development, where the Vite proxy strips the prefix and forwards to
 * `:8000`. Empty in a build, because the bundle is then served same-origin by
 * FastAPI and the routes are at the root — which is the whole reason decision 9
 * chose a proxy over a CORS allowlist: CORS never becomes a production surface,
 * so there is no allowlist to get wrong and no `*` to leave behind.
 */
const BASE = import.meta.env.DEV ? '/api' : ''

/** A failed request, carrying the status so a caller can tell 401 from 403 from
 *  422 — which matters, because those three mean "sign in", "wrong role" and
 *  "the validator rejected your rule", and a console that renders all of them as
 *  "something went wrong" is hiding the most useful message the API sends. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** `rules/validate.py` returns a LIST of rejections. Rendered as lines, not
   *  flattened into one sentence — twenty-two distinct rejections exist so an
   *  author can read them. */
  get lines(): string[] {
    const d = this.detail
    if (Array.isArray(d)) {
      return d.map((item) =>
        typeof item === 'string' ? item
          : typeof item === 'object' && item && 'msg' in item
            ? `${(item as { loc?: unknown[] }).loc?.slice(1).join('.') ?? ''}: ${(item as { msg: string }).msg}`
            : JSON.stringify(item))
    }
    if (typeof d === 'string') return [d]
    return [this.message]
  }
}

function token(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('content-type', 'application/json')
  const t = token()
  if (t) headers.set('authorization', `Bearer ${t}`)

  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    let detail: unknown = null
    try {
      const body = await res.json()
      detail = (body as { detail?: unknown }).detail ?? body
    } catch {
      detail = await res.text().catch(() => null)
    }
    throw new ApiError(res.status, detail, `${res.status} on ${path}`)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  // --- identity -----------------------------------------------------------
  me: () => request<Principal>('/me'),

  // --- the analyst's surfaces (open) --------------------------------------
  queue: (opts: { includeWorked?: boolean } = {}) =>
    request<QueueEntry[]>(`/queue${opts.includeWorked ? '?include_worked=true' : ''}`),
  alerts: () => request<AlertSummary[]>('/alerts'),
  alert: (id: number) => request<AlertDetail>(`/alerts/${id}`),
  executions: (id: number) => request<ExecutionRecord[]>(`/alerts/${id}/executions`),
  copilot: (id: number) => request<CopilotResponse>(`/alerts/${id}/copilot`),
  report: (id: number) => request<CaseReport>(`/alerts/${id}/report`),
  verdict: (id: number) => request<CaseVerdict>(`/alerts/${id}/outcome`),
  kpis: () => request<KpiSet>('/kpis'),

  // --- the analyst's one write --------------------------------------------
  disposition: (id: number, body: { disposition: string; notes?: string | null; feeds_retrain?: boolean }) =>
    post<CaseVerdict>(`/alerts/${id}/outcome`, body),

  // --- the control plane (read open, write admin) -------------------------
  rules: () => request<RuleSummary[]>('/rules'),
  rule: (id: string) => request<RuleDetail>(`/rules/${id}`),
  features: () => request<FeatureView[]>('/features'),
  reference: () => request<ReferenceVocabulary>('/reference'),

  createRule: (rule: RuleDraft) => post<RuleDetail>('/rules', rule),
  updateRule: (id: string, rule: RuleDraft) =>
    request<RuleDetail>(`/rules/${id}`, { method: 'PUT', body: JSON.stringify(rule) }),
  promoteRule: (id: string) => post<RuleDetail>(`/rules/${id}/promote`, {}),
  retireRule: (id: string) => request<unknown>(`/rules/${id}`, { method: 'DELETE' }),

  // --- simulation: nothing is written -------------------------------------
  simulateSubject: (body: {
    subject_type: string; subject_id: string
    lane?: 'inline_sync' | 'async'; replay_as_of?: string | null
  }) => post<SimulatedDecision>('/simulate/subject', body),

  simulateRule: (body: { rule: RuleDraft; sample_cap?: number; examples?: number; diff_limit?: number }) =>
    post<RuleSimulation>('/simulate/rule', body),

  simulateTransaction: (body: { transaction: TransactionDraft; lane?: 'inline_sync' | 'async' }) =>
    post<TransactionSimulation>('/simulate/transaction', body),

  // --- arrival: this one commits, and it can stop a charge -----------------
  authorize: (charge: Record<string, unknown>) =>
    post<AuthorizationOutcome>('/authorize', charge),

  // --- liveness, and the only source of it (invariant 8) ------------------
  cycleState: () => request<CycleState>('/cycle'),
  runCycle: () => post<CycleReport>('/cycle', {}),
}
