import { AlertTriangle, Ban, CheckCircle2, Clock, Hourglass, Loader2, XCircle, type LucideIcon } from 'lucide-react'
import type { TaskStatus } from './api'

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(' ')
}

export type Tone = 'neutral' | 'accent' | 'good' | 'warning' | 'serious' | 'critical' | 'violet'

export const TONES: Record<Tone, string> = {
  neutral: 'bg-surface-3 text-ink-2',
  accent: 'bg-accent-soft text-accent-ink',
  good: 'bg-good-soft text-good-ink',
  warning: 'bg-warning-soft text-warning-ink',
  serious: 'bg-serious-soft text-serious-ink',
  critical: 'bg-critical-soft text-critical-ink',
  violet: 'bg-violet-soft text-violet',
}

// Status colours are reserved for state, and always come with an icon and a label.
const TASK_STATUS: Record<TaskStatus, { tone: Tone; icon: LucideIcon; label: string }> = {
  queued: { tone: 'neutral', icon: Clock, label: 'Queued' },
  running: { tone: 'accent', icon: Loader2, label: 'Running' },
  waiting: { tone: 'warning', icon: Hourglass, label: 'Waiting for a person' },
  completed: { tone: 'good', icon: CheckCircle2, label: 'Completed' },
  escalated: { tone: 'serious', icon: AlertTriangle, label: 'Escalated' },
  failed: { tone: 'critical', icon: XCircle, label: 'Failed' },
  cancelled: { tone: 'neutral', icon: Ban, label: 'Cancelled' },
}

export function statusMeta(status: TaskStatus) {
  return TASK_STATUS[status] ?? TASK_STATUS.queued
}
