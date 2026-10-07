import { ChevronRight, CornerDownRight, Layers, ListTodo, ShieldCheck, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/Layout'
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, PhasePill, Skeleton, StatusPill, Tabs } from '../components/ui'
import { cx } from '../lib/ui-utils'
import { api, type Task } from '../lib/api'
import { PRIORITY_LABEL, humanise, timeAgo } from '../lib/format'
import { usePoll } from '../lib/hooks'

type Filter = 'all' | 'active' | 'waiting' | 'done' | 'attention'

const MATCH: Record<Filter, (t: Task) => boolean> = {
  all: () => true,
  active: (t) => t.status === 'queued' || t.status === 'running',
  waiting: (t) => t.status === 'waiting',
  done: (t) => t.status === 'completed',
  attention: (t) => t.status === 'escalated' || t.status === 'failed',
}

// The type of work: a ticket's category once the operator has understood it, or a supervisor request.
function typeOf(t: Task): string {
  if (t.source === 'supervisor') return 'supervisor'
  return t.run?.category ?? 'unknown'
}

function typeLabel(type: string): string {
  return type === 'supervisor' ? 'Supervisor requests' : type === 'unknown' ? 'Not yet understood' : humanise(type)
}

export function Tasks() {
  const { data: tasks, refresh } = usePoll<Task[]>('/tasks', 3000)
  const [filter, setFilter] = useState<Filter>('all')
  const [type, setType] = useState('all')
  const [bulkOpen, setBulkOpen] = useState(false)

  const all = tasks ?? []
  const types = [...new Set(all.map(typeOf))].sort()
  const matches = (t: Task) => MATCH[filter](t) && (type === 'all' || typeOf(t) === type)
  const parents = all.filter((t) => !t.parent_id)
  const childrenOf = (id: string) => all.filter((t) => t.parent_id === id).sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
  const shown = parents.filter((t) => matches(t) || childrenOf(t.id).some(matches))
  const count = (f: Filter) => all.filter(MATCH[f]).length

  async function cancel(id: string) {
    await api.post(`/tasks/${id}/cancel`, {})
    void refresh()
  }

  return (
    <>
      <PageHeader
        title="Tasks"
        subtitle="The operator's work queue. Higher priority first; paused tasks are re-checked automatically."
        actions={
          <Button variant="secondary" onClick={() => setBulkOpen(!bulkOpen)} aria-expanded={bulkOpen}>
            <Layers className="size-4" aria-hidden /> Queue several tickets
          </Button>
        }
      />
      {bulkOpen && <BulkQueue onQueued={() => void refresh()} />}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <Tabs
            value={filter}
            onChange={setFilter}
            tabs={[
              { id: 'all', label: `All ${all.length}` },
              { id: 'active', label: `Active ${count('active')}` },
              { id: 'waiting', label: `Waiting ${count('waiting')}` },
              { id: 'done', label: `Completed ${count('done')}` },
              { id: 'attention', label: `Needs a person ${count('attention')}` },
            ]}
          />
          <label className="flex items-center gap-2 text-xs text-ink-3">
            Type
            <select value={type} onChange={(e) => setType(e.target.value)} className="h-8 rounded-lg border border-line-strong bg-surface px-2 text-xs text-ink">
              <option value="all">All types</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {typeLabel(t)} ({all.filter((x) => typeOf(x) === t).length})
                </option>
              ))}
            </select>
          </label>
      </div>
      <Card>
        {!tasks ? (
          <div className="space-y-2 p-5">{Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : shown.length === 0 ? (
          <Empty icon={ListTodo} title="No tasks here">Queue a ticket from the command centre.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[780px] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-ink-3">
                  <th className="px-5 py-2.5 font-medium">Task</th>
                  <th className="px-3 py-2.5 font-medium">Request</th>
                  <th className="px-3 py-2.5 font-medium">Priority</th>
                  <th className="px-3 py-2.5 font-medium">Status</th>
                  <th className="px-3 py-2.5 font-medium">Run</th>
                  <th className="px-3 py-2.5 font-medium">Updated</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {shown.map((t) => (
                  <TaskRows key={t.id} task={t} subtasks={childrenOf(t.id)} onCancel={cancel} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

function TaskRows({ task, subtasks, onCancel }: { task: Task; subtasks: Task[]; onCancel: (id: string) => void }) {
  return (
    <>
      <Row task={task} onCancel={onCancel} />
      {subtasks.map((c) => (
        <Row key={c.id} task={c} child onCancel={onCancel} />
      ))}
    </>
  )
}

function Row({ task, child, onCancel }: { task: Task; child?: boolean; onCancel: (id: string) => void }) {
  const run = task.run
  return (
    <tr className={cx('border-b border-line last:border-0 hover:bg-surface-2', child && 'bg-surface-2/50')}>
      <td className="px-5 py-3 align-top">
        <span className={cx('flex items-center gap-1 whitespace-nowrap font-mono text-xs text-ink-3', child && 'pl-4')}>
          {child && <CornerDownRight className="size-3.5" aria-hidden />}
          {task.id}
        </span>
      </td>
      <td className="max-w-md px-3 py-3 align-top">
        {task.ticket_id && <span className="mr-2 font-mono text-xs font-semibold">{task.ticket_id}</span>}
        {run?.category && <Badge className="mr-2 align-middle">{humanise(run.category)}</Badge>}
        <span className="text-ink-2">{task.ticket_id && task.text.startsWith('Resolve support ticket') ? 'Resolve the ticket' : task.text}</span>
        {run?.outcome && <p className="mt-0.5 line-clamp-1 text-xs text-ink-3">{run.outcome}</p>}
        {task.error && task.status !== 'completed' && <p className="mt-0.5 line-clamp-1 text-xs text-serious-ink">{task.error}</p>}
      </td>
      <td className="px-3 py-3 align-top text-xs text-ink-2">{PRIORITY_LABEL[task.priority] ?? task.priority}</td>
      <td className="px-3 py-3 align-top">
        <StatusPill status={task.status} />
      </td>
      <td className="px-3 py-3 align-top">
        {run ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {task.status === 'running' && <PhasePill phase={run.phase} />}
            {run.current_step && task.status === 'running' && <span className="text-xs text-ink-3">{run.current_step}</span>}
            {run.verified && <Badge tone="good" icon={ShieldCheck}>verified</Badge>}
          </div>
        ) : (
          <span className="text-xs text-ink-3">not started</span>
        )}
      </td>
      <td className="px-3 py-3 align-top whitespace-nowrap text-xs text-ink-3">{timeAgo(task.updated_at)}</td>
      <td className="px-5 py-3 text-right align-top">
        <div className="flex justify-end gap-1">
          {(task.status === 'queued' || task.status === 'waiting') && (
            <Button size="sm" variant="ghost" onClick={() => onCancel(task.id)} aria-label={`Cancel ${task.id}`}>
              <X className="size-3.5" aria-hidden /> Cancel
            </Button>
          )}
          {run && (
            <Link to={`/runs/${run.run_id}`} className="inline-flex h-8 items-center gap-1 rounded-lg px-2.5 text-xs font-medium text-accent-ink hover:bg-accent-soft">
              Open <ChevronRight className="size-3.5" aria-hidden />
            </Link>
          )}
        </div>
      </td>
    </tr>
  )
}

function BulkQueue({ onQueued }: { onQueued: () => void }) {
  const [text, setText] = useState('')
  const [priority, setPriority] = useState('normal')
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ids = text.split(/[\s,;]+/).map((t) => t.trim()).filter(Boolean)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const before = new Set((await api.get<Task[]>('/tasks')).map((t) => t.id))
      const queued = await api.post<Task[]>('/tasks/bulk', { ticket_ids: ids, priority })
      const fresh = queued.filter((t) => !before.has(t.id)).length
      const already = queued.length - fresh
      setResult(`Queued ${fresh} ticket${fresh === 1 ? '' : 's'}${already ? `; ${already} already in the queue` : ''}.`)
      setText('')
      onQueued()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Card className="mb-4">
      <CardHeader title="Queue several tickets" subtitle="One task per ticket. Workers pick them up by priority; a ticket already queued is not queued twice." icon={Layers} />
      <form onSubmit={(e) => void submit(e)} className="space-y-3 p-5">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="TKT-1001, TKT-1002, TKT-1015"
          aria-label="Ticket ids"
          className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 font-mono text-sm"
        />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <select value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Priority" className="h-9 rounded-lg border border-line-strong bg-surface px-2 text-sm">
              {['low', 'normal', 'high', 'urgent'].map((p) => <option key={p} value={p}>{humanise(p)}</option>)}
            </select>
            {result && <span className="text-xs text-good-ink">{result}</span>}
          </div>
          <Button type="submit" disabled={!ids.length}>Queue {ids.length || ''} ticket{ids.length === 1 ? '' : 's'}</Button>
        </div>
        {error && <ErrorNote message={error} />}
      </form>
    </Card>
  )
}
