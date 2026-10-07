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
  del: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
  evidenceUrl: (runId: string, name: string) => `/api/runs/${runId}/evidence/${encodeURIComponent(name)}`,
  streamUrl: (runId: string, after = 0) => `/api/runs/${runId}/stream?after=${after}`,
  liveUrl: (runId: string) => `/api/runs/${runId}/live.jpg`,
}
