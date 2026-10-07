import type { RunEvent } from './api'

/** One moment of the run: a decision (or a person's input, or a verdict) and everything that followed it. */
export interface Frame {
  at: string
  phase: string
  step: string | null
  head: RunEvent
  calls: { call: RunEvent; result?: RunEvent; policy: RunEvent[] }[]
  after: RunEvent[] // adaptations, steps marked done, phase changes caused by this moment
}

const STARTS = new Set(['decision', 'human.request', 'human.decided', 'human.answered', 'verify.result', 'run.completed', 'run.escalated', 'run.handover', 'contract.created', 'contract.revised', 'plan.created', 'plan.replan'])
const AFTER = new Set(['adapt.decision', 'step.done', 'step.failed', 'phase', 'browser.uncertain', 'browser.unsaved_changes_lost'])
const POLICY = new Set(['policy.decision', 'guard.blocked', 'policy.grant'])

/** Fold the audit log into frames a person can step through, decision by decision. */
export function buildFrames(events: RunEvent[]): Frame[] {
  const frames: Frame[] = []
  let phase = 'understand'
  let step: string | null = null
  for (const e of events) {
    const current: Frame | undefined = frames[frames.length - 1]
    if (STARTS.has(e.type) || (e.type === 'tool.call' && !current)) {
      if (e.type === 'decision' && e.data.step_id) step = e.data.step_id
      frames.push({ at: e.at, phase, step, head: e, calls: [], after: [] })
    }
    const frame = frames[frames.length - 1]
    if (!frame) continue
    if (e.type === 'tool.call') frame.calls.push({ call: e, policy: [] })
    else if (e.type === 'tool.result') {
      const open = frame.calls.findLast((c) => !c.result && c.call.data.tool === e.data.tool)
      if (open) open.result = e
    } else if (POLICY.has(e.type) && frame.calls.length) frame.calls[frame.calls.length - 1].policy.push(e)
    else if (AFTER.has(e.type)) frame.after.push(e)
    if (e.type === 'phase') phase = e.data.to
  }
  return frames
}
