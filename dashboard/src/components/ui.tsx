import { Circle, Loader2, XCircle, type LucideIcon } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { Phase, TaskStatus } from '../lib/api'
import { TONES, cx, statusMeta, type Tone } from '../lib/ui-utils'

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx('rounded-xl border border-line bg-surface shadow-card', className)}>{children}</section>
}

export function CardHeader({ title, subtitle, action, icon: Icon }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode; icon?: LucideIcon }) {
  return (
    <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
      <div className="flex min-w-0 items-start gap-2.5">
        {Icon && <Icon className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-ink-3">{subtitle}</p>}
        </div>
      </div>
      {action}
    </header>
  )
}

export function Badge({ children, tone = 'neutral', icon: Icon, className }: { children: ReactNode; tone?: Tone; icon?: LucideIcon; className?: string }) {
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap', TONES[tone], className)}>
      {Icon && <Icon className="size-3.5" aria-hidden />}
      {children}
    </span>
  )
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'good'; size?: 'sm' | 'md' }) {
  const variants = {
    primary: 'bg-accent text-white hover:brightness-110',
    good: 'bg-good-ink text-surface hover:brightness-110',
    danger: 'bg-critical text-white hover:brightness-110',
    secondary: 'border border-line-strong bg-surface text-ink hover:bg-surface-2',
    ghost: 'text-ink-2 hover:bg-surface-3 hover:text-ink',
  }
  return (
    <button
      {...props}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        size === 'sm' ? 'h-8 px-2.5 text-xs' : 'h-9 px-3.5 text-sm',
        variants[variant],
        className,
      )}
    />
  )
}

export function StatusPill({ status }: { status: TaskStatus }) {
  const s = statusMeta(status)
  return (
    <Badge tone={s.tone} icon={s.icon} className={status === 'running' ? '[&>svg]:animate-spin' : undefined}>
      {s.label}
    </Badge>
  )
}

const RUN_PHASE: Partial<Record<Phase, TaskStatus>> = {
  completed: 'completed',
  escalated: 'escalated',
  failed: 'failed',
  awaiting_human: 'waiting',
}

export function PhasePill({ phase }: { phase: Phase }) {
  const terminal = RUN_PHASE[phase]
  if (terminal) return <StatusPill status={terminal} />
  return (
    <Badge tone="accent" icon={Loader2} className="[&>svg]:animate-spin">
      {phase[0].toUpperCase() + phase.slice(1)}
    </Badge>
  )
}

export function StatTile({ label, value, detail, icon: Icon, tone = 'neutral' }: { label: string; value: ReactNode; detail?: ReactNode; icon: LucideIcon; tone?: Tone }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink-3">{label}</p>
        <span className={cx('grid size-7 place-items-center rounded-lg', TONES[tone])}>
          <Icon className="size-4" aria-hidden />
        </span>
      </div>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-ink">{value}</p>
      {detail && <p className="mt-1 text-xs text-ink-3">{detail}</p>}
    </Card>
  )
}

export function Empty({ icon: Icon = Circle, title, children }: { icon?: LucideIcon; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <span className="grid size-10 place-items-center rounded-full bg-surface-3 text-ink-3">
        <Icon className="size-5" aria-hidden />
      </span>
      <p className="mt-3 text-sm font-medium text-ink">{title}</p>
      {children && <div className="mt-1 max-w-sm text-xs text-ink-3">{children}</div>}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded-md bg-surface-3', className)} />
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-critical/30 bg-critical-soft px-3 py-2 text-sm text-critical-ink">
      <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{message}</span>
    </div>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (id: T) => void }) {
  return (
    <div role="tablist" className="inline-flex max-w-full overflow-x-auto rounded-lg border border-line bg-surface-2 p-0.5">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cx(
            'shrink-0 whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition',
            value === t.id ? 'bg-surface text-ink shadow-card' : 'text-ink-3 hover:text-ink',
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cx('rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[0.8em] text-ink-2', className)}>{children}</code>
}
