import { BookMarked, Brain, GraduationCap, History, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/Layout'
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, Mono, Skeleton, StatusPill } from '../components/ui'
import { api, type Episode, type Fact, type TaskStatus } from '../lib/api'
import { rupees, timeAgo } from '../lib/format'
import { usePoll } from '../lib/hooks'

export function Memory() {
  const { data: facts, refresh } = usePoll<Fact[]>('/memory/facts')
  const { data: episodes } = usePoll<Episode[]>('/memory/episodes?limit=30', 5000)
  const learned = (facts ?? []).filter((f) => f.learned)
  const builtin = (facts ?? []).filter((f) => !f.learned)

  async function forget(id: string) {
    await api.del(`/memory/facts/${id.replace('learned-', '')}`)
    void refresh()
  }

  return (
    <>
      <PageHeader title="Memory" subtitle="What the operator has learned from people, and how earlier requests were handled. Every run sees this." />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Card>
            <CardHeader title="Learned from supervisors" subtitle="Rules people taught the operator, e.g. when rejecting an approval" icon={GraduationCap} />
            {!facts ? (
              <Skeleton className="m-5 h-20" />
            ) : learned.length === 0 ? (
              <Empty icon={GraduationCap} title="Nothing learned yet">Reject an approval with “Teach the operator” ticked, or add a rule below.</Empty>
            ) : (
              <ul className="divide-y divide-line">
                {learned.map((f) => (
                  <li key={f.id} className="flex gap-3 px-5 py-3">
                    <Brain className="mt-0.5 size-4 shrink-0 text-violet" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">{f.text}</p>
                      <p className="mt-0.5 text-xs text-ink-3">{f.source} · {timeAgo(f.created_at)}</p>
                    </div>
                    <Button size="sm" variant="ghost" onClick={() => void forget(f.id)} aria-label="Forget this rule">
                      <Trash2 className="size-3.5" aria-hidden />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <AddFact onAdded={() => void refresh()} />
          </Card>
          <Card>
            <CardHeader title="From the Company Pack" subtitle="Built-in company facts" icon={BookMarked} />
            <ul className="divide-y divide-line">
              {builtin.map((f) => (
                <li key={f.id} className="px-5 py-2.5 text-sm text-ink-2">
                  {f.text} <Badge className="ml-1">{f.source}</Badge>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <Card className="h-fit">
          <CardHeader title="Episodes" subtitle="One record per finished run; similar requests see how earlier ones went" icon={History} />
          {!episodes ? (
            <Skeleton className="m-5 h-40" />
          ) : episodes.length === 0 ? (
            <Empty icon={History} title="No finished runs yet" />
          ) : (
            <ul className="divide-y divide-line">
              {episodes.map((e) => {
                const money = e.changes.filter((c) => c.facts?.amount !== undefined)
                return (
                  <li key={e.run_id}>
                    <Link to={`/runs/${e.run_id}`} className="block px-5 py-3 hover:bg-surface-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusPill status={e.outcome as TaskStatus} />
                        {e.verified && <Badge tone="good" icon={ShieldCheck}>verified</Badge>}
                        {e.category && <Mono>{e.category}</Mono>}
                        <span className="ml-auto text-xs text-ink-3">{timeAgo(e.created_at)}</span>
                      </div>
                      <p className="mt-1.5 text-sm">{e.request}</p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-ink-3">{e.summary}</p>
                      {e.changes.length > 0 && (
                        <p className="mt-1 flex flex-wrap gap-1">
                          {[...new Set(e.changes.map((c) => c.action))].map((a) => <Mono key={a}>{a}</Mono>)}
                          {money.map((c, i) => <Badge key={i} tone="violet">{rupees(Number(c.facts.amount))}</Badge>)}
                        </p>
                      )}
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}

function AddFact({ onAdded }: { onAdded: () => void }) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.post('/memory/facts', { text, source: 'added by priya.supervisor in the dashboard' })
      setText('')
      onAdded()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }
  return (
    <form onSubmit={submit} className="flex gap-2 border-t border-line p-4">
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Teach a new rule, e.g. Always attach the refund id to the internal note" className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-3 text-sm" aria-label="New rule" />
      <Button type="submit" variant="secondary" disabled={text.trim().length < 5}>
        <Plus className="size-4" aria-hidden /> Add
      </Button>
      {error && <ErrorNote message={error} />}
    </form>
  )
}
