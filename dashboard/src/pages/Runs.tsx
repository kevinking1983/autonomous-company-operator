import { ChevronRight, History, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/Layout'
import { Badge, Card, Empty, PhasePill, Skeleton } from '../components/ui'
import type { RunBrief, RunState } from '../lib/api'
import { timeAgo } from '../lib/format'
import { usePoll } from '../lib/hooks'

type RunRow = RunBrief & { request: RunState['request'] }

export function Runs() {
  const { data: runs } = usePoll<RunRow[]>('/runs?limit=100', 5000)
  return (
    <>
      <PageHeader title="Runs" subtitle="Every run, with its audit trail, evidence and report. Includes runs started from the command line." />
      <Card>
        {!runs ? (
          <div className="space-y-2 p-5">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : runs.length === 0 ? (
          <Empty icon={History} title="No runs yet" />
        ) : (
          <ul className="divide-y divide-line">
            {runs.map((r) => (
              <li key={r.run_id}>
                <Link to={`/runs/${r.run_id}`} className="flex items-center gap-4 px-5 py-3.5 hover:bg-surface-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">
                      {r.request.ticket_id && <span className="mr-2 font-mono text-xs font-semibold">{r.request.ticket_id}</span>}
                      <span className="text-ink-2">{r.request.text}</span>
                    </p>
                    <p className="mt-0.5 truncate text-xs text-ink-3">{r.outcome ?? 'Understanding the request…'}</p>
                  </div>
                  {r.verified && <Badge tone="good" icon={ShieldCheck}>verified</Badge>}
                  <PhasePill phase={r.phase} />
                  <span className="w-20 text-right text-xs text-ink-3">{timeAgo(r.updated_at)}</span>
                  <ChevronRight className="size-4 text-ink-3" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}
