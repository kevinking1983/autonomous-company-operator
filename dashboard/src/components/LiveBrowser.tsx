import { Monitor, MonitorOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { Card, CardHeader, Empty } from './ui'

const REFRESH_MS = 1500

/**
 * What the operator's browser shows: a frame saved after every browser action, re-fetched while the run is
 * working. Once the run ends, the last frame stays as "the last page it saw".
 */
export function LiveBrowser({ runId, working, url }: { runId: string; working: boolean; url: string | null }) {
  const [tick, setTick] = useState(0)
  const [failedTick, setFailedTick] = useState<number | null>(null)

  useEffect(() => {
    if (!working) return
    const timer = window.setInterval(() => setTick((t) => t + 1), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [working])

  const missing = failedTick === tick
  return (
    <Card>
      <CardHeader
        title={working ? 'Live browser' : 'Last page the operator saw'}
        subtitle={url ?? (working ? 'Refreshes after every browser action' : undefined)}
        icon={Monitor}
        action={
          working && (
            <span className="inline-flex items-center gap-1.5 text-xs text-accent-ink">
              <span className="live-dot size-2 rounded-full bg-accent" aria-hidden /> live
            </span>
          )
        }
      />
      {missing ? (
        <Empty icon={MonitorOff} title={working ? 'No browser activity yet' : 'No browser frames for this run'} />
      ) : (
        <div className="p-3">
          <img
            src={`${api.liveUrl(runId)}?t=${tick}`}
            alt={`The operator's browser${url ? ` at ${url}` : ''}`}
            onError={() => setFailedTick(tick)}
            className="w-full rounded-lg border border-line bg-surface-2"
          />
        </div>
      )}
    </Card>
  )
}
