import { Check, Eye, Flag, ListChecks, Map, Play, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import type { Phase } from '../lib/api'
import { cx } from '../lib/ui-utils'

const PHASES = [
  { id: 'understand', label: 'Understand', icon: Search },
  { id: 'plan', label: 'Plan', icon: Map },
  { id: 'execute', label: 'Execute', icon: Play },
  { id: 'observe', label: 'Observe', icon: Eye },
  { id: 'adapt', label: 'Adapt', icon: RefreshCw },
  { id: 'verify', label: 'Verify', icon: ShieldCheck },
  { id: 'complete', label: 'Complete', icon: Flag },
] as const

/**
 * The runtime loop from the problem statement, with the run's position in it.
 * Execute → Observe → Adapt is a loop; `visited` marks every phase the run has entered.
 */
export function PhaseTracker({ phase, visited, live }: { phase: Phase; visited: Set<string>; live: boolean }) {
  const finished = phase === 'completed' || phase === 'escalated' || phase === 'failed'
  const current = phase === 'completed' ? 'complete' : phase
  return (
    <div className="relative overflow-x-auto">
      <div className="min-w-[560px]">
      <ol className="grid grid-cols-7 gap-1" aria-label="Run phases">
        {PHASES.map((p, i) => {
          const isCurrent = !finished && current === p.id
          const done = visited.has(p.id) || (phase === 'completed' && p.id === 'complete')
          const Icon = done && !isCurrent ? Check : p.icon
          return (
            <li key={p.id} className="flex flex-col items-center gap-1.5 text-center" aria-current={isCurrent ? 'step' : undefined}>
              <div className="flex w-full items-center">
                <span className={cx('h-0.5 flex-1', i === 0 ? 'bg-transparent' : done || isCurrent ? 'bg-accent' : 'bg-line')} />
                <span
                  className={cx(
                    'grid size-8 shrink-0 place-items-center rounded-full border-2 transition',
                    isCurrent && 'border-accent bg-accent text-white',
                    isCurrent && live && 'live-dot',
                    !isCurrent && done && 'border-accent bg-accent-soft text-accent-ink',
                    !isCurrent && !done && 'border-line bg-surface text-ink-3',
                  )}
                >
                  <Icon className={cx('size-4', isCurrent && p.id === 'adapt' && 'animate-spin')} aria-hidden />
                </span>
                <span className={cx('h-0.5 flex-1', i === PHASES.length - 1 ? 'bg-transparent' : visited.has(PHASES[i + 1].id) ? 'bg-accent' : 'bg-line')} />
              </div>
              <span className={cx('text-xs', isCurrent ? 'font-semibold text-ink' : done ? 'text-ink-2' : 'text-ink-3')}>{p.label}</span>
            </li>
          )
        })}
      </ol>
      <div className="pointer-events-none mx-auto mt-1 flex w-[28.6%] items-center gap-1 text-[10px] text-ink-3" aria-hidden>
        <span className="h-2 flex-1 rounded-b-md border-x border-b border-dashed border-line-strong" />
      </div>
      <p className="mt-0.5 text-center text-[10px] text-ink-3">
        <ListChecks className="mr-1 inline size-3" aria-hidden />
        Execute → Observe → Adapt repeats until every step's expected result holds
      </p>
      </div>
    </div>
  )
}
