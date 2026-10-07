import { ArrowRight, Bot, Brain, CheckCircle2, HandCoins, Hourglass, Inbox, Send, ShieldCheck, Sparkles, ThumbsUp, Timer } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/Layout'
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, PhasePill, Skeleton, StatTile, Tabs } from '../components/ui'
import { cx, statusMeta } from '../lib/ui-utils'
import { describe } from '../lib/activity'
import { api, type Overview as OverviewData, type RunEvent, type Task, type TaskStatus } from '../lib/api'
import { duration, percent, rupees, timeAgo } from '../lib/format'
import { usePoll } from '../lib/hooks'

export function Overview() {
  const { data: overview } = usePoll<OverviewData>('/overview', 4000)
  const { data: running } = usePoll<Task[]>('/tasks?status=running', 3000)
  const { data: activity } = usePoll<RunEvent[]>('/activity?limit=30', 3000)

  const completed = overview?.tasks.completed ?? 0
  const money = (overview?.refunded ?? 0) + (overview?.coupons ?? 0)

  return (
    <>
      <PageHeader title="Command centre" subtitle="QuickBite customer support, worked end to end by the AI operator." />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {overview ? (
          <>
            <StatTile label="Tasks completed" value={completed} icon={CheckCircle2} tone="good" detail={`${overview.runs_finished} runs finished in total`} />
            <StatTile
              label="Verified outcomes"
              value={percent(overview.verified_rate)}
              icon={ShieldCheck}
              tone="accent"
              detail={`${overview.verified} independently verified`}
            />
            <StatTile
              label="Auto-resolved"
              value={percent(overview.auto_resolution_rate)}
              icon={Bot}
              tone="accent"
              detail="completed with nobody stepping in"
            />
            <StatTile label="Money given" value={rupees(money)} icon={HandCoins} tone="violet" detail={`${rupees(overview.refunded)} refunds · ${rupees(overview.coupons)} coupons`} />
            <StatTile
              label="Waiting for people"
              value={overview.waiting_for_people}
              icon={Hourglass}
              tone={overview.waiting_for_people ? 'warning' : 'neutral'}
              detail={overview.waiting_for_people ? <Link className="text-accent-ink hover:underline" to="/inbox">Open the inbox →</Link> : 'Nothing needs you'}
            />
            <StatTile
              label="Average time to finish"
              value={overview.avg_minutes === null ? '—' : duration(overview.avg_minutes)}
              icon={Timer}
              tone="neutral"
              detail={overview.avg_waiting_minutes ? `${duration(overview.avg_waiting_minutes)} of it waiting for people` : 'start to verified finish'}
            />
            <StatTile
              label="Approval rate"
              value={percent(overview.approval_rate)}
              icon={ThumbsUp}
              tone="neutral"
              detail={`of ${overview.approvals_decided} approval request${overview.approvals_decided === 1 ? '' : 's'} decided`}
            />
            <StatTile label="Learned from people" value={overview.learned_facts} icon={Brain} tone="neutral" detail="rules taught by supervisors" />
          </>
        ) : (
          Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-28" />)
        )}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <NewTask />
          <Card>
            <CardHeader title="Task outcomes" subtitle="Every task the queue has seen, by status" />
            <div className="p-5">{overview ? <OutcomeBar counts={overview.tasks} /> : <Skeleton className="h-16" />}</div>
          </Card>
          <Card>
            <CardHeader title="Working now" subtitle="Tasks a worker is executing at this moment" action={<Link to="/tasks" className="text-xs text-accent-ink hover:underline">All tasks</Link>} />
            {running && running.length > 0 ? (
              <ul className="divide-y divide-line">
                {running.map((t) => (
                  <li key={t.id}>
                    <Link to={t.run ? `/runs/${t.run.run_id}` : '/tasks'} className="flex items-center gap-3 px-5 py-3 hover:bg-surface-2">
                      <span className="font-mono text-xs text-ink-3">{t.id}</span>
                      <span className="min-w-0 flex-1 truncate text-sm">{t.ticket_id ? <strong className="font-medium">{t.ticket_id}</strong> : t.text}</span>
                      {t.run?.current_step && <span className="text-xs text-ink-3">step: {t.run.current_step}</span>}
                      {t.run && <PhasePill phase={t.run.phase} />}
                      <ArrowRight className="size-4 text-ink-3" aria-hidden />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty icon={Sparkles} title="No task is running right now">Queue a ticket above and start a worker with <code>make worker</code>.</Empty>
            )}
          </Card>
        </div>

        <Card className="lg:row-span-2">
          <CardHeader title="Live activity" subtitle="Notable events across recent runs" action={<span className="flex items-center gap-1.5 text-xs text-ink-3"><span className="live-dot size-2 rounded-full bg-accent" />live</span>} />
          {activity && activity.length > 0 ? (
            <ol className="max-h-[720px] divide-y divide-line overflow-y-auto">
              {activity.map((e) => {
                const info = describe(e)
                if (!info) return null
                const Icon = info.icon
                return (
                  <li key={`${e.run_id}-${e.seq}`}>
                    <Link to={`/runs/${e.run_id}`} className="flex gap-3 px-4 py-3 hover:bg-surface-2">
                      <span className={cx('mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg', toneBg(info.tone))}>
                        <Icon className="size-3.5" aria-hidden />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline justify-between gap-2">
                          <p className="truncate text-sm font-medium">{info.title}</p>
                          <span className="shrink-0 text-[11px] text-ink-3">{timeAgo(e.at)}</span>
                        </div>
                        <p className="truncate text-xs text-ink-3">{e.label}{info.detail ? ` · ${info.detail}` : ''}</p>
                      </div>
                    </Link>
                  </li>
                )
              })}
            </ol>
          ) : (
            <Empty title="No activity yet">Runs will appear here as the operator works.</Empty>
          )}
        </Card>
      </div>
    </>
  )
}

function toneBg(tone: string): string {
  return (
    {
      good: 'bg-good-soft text-good-ink',
      warning: 'bg-warning-soft text-warning-ink',
      serious: 'bg-serious-soft text-serious-ink',
      critical: 'bg-critical-soft text-critical-ink',
      accent: 'bg-accent-soft text-accent-ink',
      violet: 'bg-violet-soft text-violet',
    }[tone] ?? 'bg-surface-3 text-ink-2'
  )
}

const BAR_ORDER: TaskStatus[] = ['completed', 'escalated', 'failed', 'waiting', 'running', 'queued', 'cancelled']
const BAR_COLOUR: Record<TaskStatus, string> = {
  completed: 'bg-good',
  escalated: 'bg-serious',
  failed: 'bg-critical',
  waiting: 'bg-warning',
  running: 'bg-accent',
  queued: 'bg-line-strong',
  cancelled: 'bg-surface-3',
}

/** Part-to-whole of task statuses: one stacked bar, 2px gaps, and a legend that carries every count. */
function OutcomeBar({ counts }: { counts: Partial<Record<TaskStatus, number>> }) {
  const total = BAR_ORDER.reduce((sum, s) => sum + (counts[s] ?? 0), 0)
  if (!total) return <p className="text-sm text-ink-3">No tasks yet.</p>
  const present = BAR_ORDER.filter((s) => counts[s])
  return (
    <div>
      <div className="flex h-6 gap-0.5 overflow-hidden rounded-md" role="img" aria-label={present.map((s) => `${statusMeta(s).label}: ${counts[s]}`).join(', ')}>
        {present.map((s) => (
          <div
            key={s}
            className={cx('h-full first:rounded-l-md last:rounded-r-md', BAR_COLOUR[s])}
            style={{ width: `${((counts[s] ?? 0) / total) * 100}%` }}
            title={`${statusMeta(s).label}: ${counts[s]}`}
          />
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
        {present.map((s) => {
          const meta = statusMeta(s)
          const Icon = meta.icon
          return (
            <li key={s} className="flex items-center gap-1.5 text-xs text-ink-2">
              <span className={cx('size-2.5 rounded-sm', BAR_COLOUR[s])} aria-hidden />
              <Icon className="size-3.5 text-ink-3" aria-hidden />
              {meta.label} <span className="tabular font-semibold text-ink">{counts[s]}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function NewTask() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'ticket' | 'request'>('ticket')
  const [ticket, setTicket] = useState('')
  const [text, setText] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const body = mode === 'ticket' ? { ticket_id: ticket.trim().toUpperCase(), priority } : { text: text.trim(), priority, requested_by: 'priya.supervisor' }
      await api.post<Task>('/tasks', body)
      navigate('/tasks')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Give the operator work" subtitle="Queued tasks are picked up by the background workers" icon={Send} />
      <form onSubmit={submit} className="space-y-3 p-5">
        <Tabs
          value={mode}
          onChange={setMode}
          tabs={[
            { id: 'ticket', label: 'Resolve a ticket' },
            { id: 'request', label: 'Supervisor request' },
          ]}
        />
        {mode === 'ticket' ? (
          <div>
            <label htmlFor="ticket" className="text-xs font-medium text-ink-2">Ticket id</label>
            <input
              id="ticket"
              value={ticket}
              onChange={(e) => setTicket(e.target.value)}
              placeholder="TKT-1001"
              required
              className="mt-1 h-9 w-full rounded-lg border border-line-strong bg-surface px-3 font-mono text-sm uppercase outline-none focus:border-accent"
            />
            <p className="mt-1.5 flex flex-wrap gap-1.5 text-xs text-ink-3">
              Try:
              {['TKT-1001', 'TKT-1003', 'TKT-1010', 'TKT-1012'].map((t) => (
                <button key={t} type="button" onClick={() => setTicket(t)} className="rounded border border-line px-1.5 font-mono hover:border-accent hover:text-accent-ink">
                  {t}
                </button>
              ))}
            </p>
          </div>
        ) : (
          <div>
            <label htmlFor="request" className="text-xs font-medium text-ink-2">What should be done?</label>
            <textarea
              id="request"
              value={text}
              onChange={(e) => setText(e.target.value)}
              required
              rows={3}
              placeholder="Clear all open late-delivery tickets: resolve each one according to the late-delivery policy."
              className="mt-1 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </div>
        )}
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <label htmlFor="priority" className="text-xs font-medium text-ink-2">Priority</label>
            <select id="priority" value={priority} onChange={(e) => setPriority(e.target.value)} className="mt-1 block h-9 rounded-lg border border-line-strong bg-surface px-2.5 text-sm">
              {['low', 'normal', 'high', 'urgent'].map((p) => (
                <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>
              ))}
            </select>
          </div>
          <Button type="submit" disabled={busy}>
            <Send className="size-4" aria-hidden /> Queue task
          </Button>
        </div>
        {error && <ErrorNote message={error} />}
        <p className="flex items-center gap-1.5 text-xs text-ink-3">
          <Inbox className="size-3.5" aria-hidden /> Money above policy limits waits for your approval in the inbox.
          <Badge tone="neutral" className="ml-auto">sandbox</Badge>
        </p>
      </form>
    </Card>
  )
}
