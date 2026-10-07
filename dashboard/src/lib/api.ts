// Typed access to the operator API (served under /api).

export type TaskStatus = 'queued' | 'running' | 'waiting' | 'completed' | 'escalated' | 'failed' | 'cancelled'
export type Phase =
  | 'understand' | 'plan' | 'execute' | 'observe' | 'adapt' | 'verify' | 'complete'
  | 'awaiting_human' | 'completed' | 'escalated' | 'failed'

export interface RunBrief {
  run_id: string
  phase: Phase
  current_step: string | null
  outcome: string | null
  summary: string
  reason: string
  pending_human: string | null
  verified: boolean | null
  category: string | null
  created_at: string
  updated_at: string
}

export interface Task {
  id: string
  text: string
  ticket_id: string | null
  source: string
  requested_by: string
  priority: number
  status: TaskStatus
  parent_id: string | null
  run_id: string | null
  attempts: number
  error: string | null
  created_at: string
  updated_at: string
  run: RunBrief | null
  children?: Task[]
}

export interface Criterion { id: string; text: string }
export interface CriterionResult { criterion_id: string; passed: boolean; evidence: string }
export interface PlanStep { id: string; goal: string; expected: string; status: string; attempts: number; outcome: string }
export interface Observation {
  seq: number
  phase: string
  step_id: string | null
  call: { tool: string; arguments: Record<string, unknown> }
  ok: boolean
  error: string | null
  output: string
  data: Record<string, unknown>
  evidence: string[]
  at: string
}

export interface RunState {
  run_id: string
  request: { text: string; source: string; ticket_id: string | null; requested_by: string }
  phase: Phase
  contract: { outcome: string; category: string | null; sop_ids: string[]; success_criteria: Criterion[]; constraints: string[]; assumptions: string[] } | null
  plan: { version: number; rationale: string; steps: PlanStep[] } | null
  current_step: string | null
  observations: Observation[]
  memory: Record<string, string>
  verification: { passed: boolean; results: CriterionResult[] } | null
  summary: string
  outcome_reason: string
  pending_human: string | null
  hands_off: string[]
  counters: Record<string, number>
  created_at: string
  updated_at: string
}

export interface RunEvent { seq: number; at: string; type: string; data: Record<string, any>; run_id?: string; label?: string }

export interface Report {
  status: string
  status_label: string
  changes: { seq: number; step: string | null; action: string; facts: Record<string, unknown>; ok: boolean; error: string | null; result: string; diff: { op: '+' | '-'; text: string }[] | null }[]
  evidence: string[]
  stats: { tool_calls: number; decisions: number; replans: number; verify_rounds: number; llm_calls: number; input_tokens: number; output_tokens: number; models: string[]; retries: number; blocked: number; adaptations: string[] }
}

export interface HumanRequest {
  id: string
  run_id: string | null
  kind: 'approval' | 'clarification' | 'external_reply' | 'subtasks'
  audience: string
  question: string
  action: string | null
  facts: Record<string, unknown>
  rules: string[]
  reasons: string[]
  context: Record<string, unknown>
  status: string
  response: string | null
  responder: string | null
  created_at: string
  decided_at: string | null
}

export interface RequestContext {
  request: HumanRequest
  run: RunBrief | null
  evidence: string[]
  pages: { seq: number; url: string; title: string; text: string }[]
}

export interface Overview {
  tasks: Partial<Record<TaskStatus, number>>
  runs_finished: number
  outcomes: Record<string, number>
  verified: number
  verified_rate: number | null
  refunded: number
  coupons: number
  waiting_for_people: number
  learned_facts: number
  auto_resolution_rate: number | null
  approvals_decided: number
  approval_rate: number | null
  avg_minutes: number | null
  avg_waiting_minutes: number | null
}

export interface Fact { id: string; text: string; source: string; learned: boolean; created_at?: string }
export interface Episode { run_id: string; category: string | null; request: string; outcome: string; summary: string; changes: { action: string; facts: Record<string, unknown> }[]; verified: boolean; created_at: string }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let message = response.statusText
    try {
      const body = await response.json()
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep the status text */
    }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  put: <T,>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  del: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
  evidenceUrl: (runId: string, name: string, scope?: string | null) => scoped(`/api/runs/${runId}/evidence/${encodeURIComponent(name)}`, scope),
  streamUrl: (runId: string, after = 0, scope?: string | null) => scoped(`/api/runs/${runId}/stream?after=${after}`, scope),
  liveUrl: (runId: string, scope?: string | null) => scoped(`/api/runs/${runId}/live.jpg`, scope),
}

/** Runs made by an eval live in their own store; `scope` (eval:<id>:<case>:<profile>) points the run endpoints there. */
export function scoped(path: string, scope?: string | null): string {
  if (!scope) return path
  return `${path}${path.includes('?') ? '&' : '?'}scope=${encodeURIComponent(scope)}`
}

// ── reliability lab ──

export type EvalStatus = 'queued' | 'running' | 'stopping' | 'stopped' | 'paused' | 'completed'
export interface EvalMeta {
  id: string
  label: string
  pairs: [string, string][]
  models: string[]
  status: EvalStatus
  created_at: string
  updated_at: string
  current?: [string, string] | null
  reason?: string | null
  total?: number
  done?: number
  passed?: number
  scored?: number
  errors?: number
  pass_rate?: number | null
}
export interface EvalCheck { id: string; label: string; passed: boolean; detail: string; critical: boolean }
export interface EvalResult {
  case: string
  profile: string
  status: 'pass' | 'fail' | 'error'
  checks: EvalCheck[]
  run_id: string | null
  phase: string | null
  verified: boolean | null
  verifier_agrees: boolean | null
  approvals_asked: number
  questions_answered: number
  faults_injected: number
  metrics: { tool_calls?: number; llm_calls?: number; input_tokens?: number; output_tokens?: number; seconds?: number; adaptations?: number; retries?: number; blocked?: number }
  error: string | null
}
export interface GroupScore { runs: number; passed: number; pass_rate: number | null }
export interface Scorecard {
  runs: number
  scored: number
  passed: number
  errors: number
  pass_rate: number | null
  money_correct_rate: number | null
  unsafe_runs: number
  human_gate_rate: number | null
  verifier_agreement: number | null
  verifier_false_passes: number
  faults_injected: number
  avg: { tool_calls: number | null; llm_calls: number | null; seconds: number | null; adaptations: number | null }
  by_profile: Record<string, GroupScore>
  by_case: Record<string, GroupScore>
}
export interface EvalDetail { meta: EvalMeta; results: EvalResult[]; scorecard: Scorecard }
export interface EvalCatalog {
  suites: { id: string; label: string; description: string; pairs: [string, string][] }[]
  cases: { key: string; ticket_id: string; summary: string; tags: string[]; approval: string }[]
  profiles: { name: string; label: string; description: string; faults: Record<string, unknown>; applies_to: string[] }[]
}
export interface FaultConfig {
  error_rate: number
  fail_next: number
  latency_ms: number
  session_expiry_in: number
  stale_form_next: number
  refund_commit_timeout_next: number
  layout: 'standard' | 'shifted'
  seed: number
  systems: string[]
}
export interface SandboxFaults { config: FaultConfig; injected: number; recent: { at: string; kind: string; system: string; path: string }[] }
