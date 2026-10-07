import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  FileSignature,
  Hand,
  Map,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  Split,
  ThumbsDown,
  ThumbsUp,
  UserRoundCheck,
  type LucideIcon,
  Hourglass,
} from 'lucide-react'
import type { RunEvent } from './api'
import { humanise } from './format'

export type Tone = 'neutral' | 'accent' | 'good' | 'warning' | 'serious' | 'critical' | 'violet'

/** A short, human description of an audit event (for feeds and timelines). */
export function describe(event: RunEvent): { icon: LucideIcon; tone: Tone; title: string; detail?: string } | null {
  const d = event.data
  switch (event.type) {
    case 'contract.created':
      return { icon: FileSignature, tone: 'violet', title: 'Understood the request', detail: d.contract?.outcome }
    case 'contract.revised':
      return { icon: FileSignature, tone: 'violet', title: 'Revised the task contract', detail: d.contract?.outcome }
    case 'plan.created':
      return { icon: Map, tone: 'accent', title: `Planned ${d.steps?.length ?? 0} steps (v${d.version})`, detail: d.steps?.map((s: { id: string }) => s.id).join(' → ') }
    case 'step.done':
      return { icon: CheckCircle2, tone: 'good', title: `Step done: ${humanise(d.step)}`, detail: d.outcome }
    case 'verify.result': {
      const failed = (d.results ?? []).filter((r: { passed: boolean }) => !r.passed).length
      return d.passed
        ? { icon: ShieldCheck, tone: 'good', title: 'Verified: every criterion holds' }
        : { icon: ShieldX, tone: 'serious', title: `Verification: ${failed} criterion${failed === 1 ? '' : 'a'} not met` }
    }
    case 'run.completed':
      return { icon: CheckCircle2, tone: 'good', title: 'Completed', detail: d.summary }
    case 'run.escalated':
      return { icon: AlertTriangle, tone: 'serious', title: 'Escalated to a person', detail: d.reason }
    case 'run.paused':
      return { icon: Hourglass, tone: 'warning', title: 'Paused until a person answers', detail: d.reason }
    case 'run.handover':
      return { icon: Hand, tone: 'serious', title: 'Handing over', detail: d.reason }
    case 'human.request':
      if (d.kind === 'approval') return { icon: UserRoundCheck, tone: 'warning', title: `Asked for approval: ${d.action}`, detail: d.question }
      if (d.kind === 'subtasks') return { icon: Split, tone: 'accent', title: 'Delegated sub-tasks', detail: d.question }
      if (d.kind === 'external_reply') return { icon: MessageSquare, tone: 'warning', title: 'Waiting for the customer', detail: d.question }
      return { icon: MessageSquare, tone: 'warning', title: 'Asked a question', detail: d.question }
    case 'human.decided':
      return d.approved
        ? {
            icon: ThumbsUp,
            tone: 'good',
            title: d.requested_amount ? `Approved by ${d.approver} for a lower amount: ₹${d.facts?.amount} of ₹${d.requested_amount}` : `Approved by ${d.approver}`,
            detail: d.note,
          }
        : { icon: ThumbsDown, tone: 'critical', title: `Rejected by ${d.approver}`, detail: d.note }
    case 'human.answered':
      return { icon: MessageSquare, tone: 'accent', title: 'Answer received', detail: d.response }
    case 'guard.blocked':
      return { icon: Ban, tone: 'critical', title: 'Blocked by policy', detail: d.reason }
    case 'adapt.decision':
      return { icon: RefreshCw, tone: 'warning', title: `Adapted: ${humanise(d.rule)}`, detail: d.hint }
    case 'subtasks.escalated':
      return { icon: Hand, tone: 'serious', title: `Left for a person: ${(d.records ?? []).join(', ')}` }
    default:
      return null
  }
}
