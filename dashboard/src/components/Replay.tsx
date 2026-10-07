import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Pause, Play, Rewind } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { describe } from '../lib/activity'
import type { RunEvent } from '../lib/api'
import { buildFrames, type Frame } from '../lib/replay'
import { clock, compactArgs, humanise, noteOf } from '../lib/format'
import { cx } from '../lib/ui-utils'
import { Badge, Button, Empty, Mono } from './ui'

/** One line for a result: the runtime's note if there is one, else the title of the page it landed on. */
function summaryOf(output: string): string {
  const lines = output.split('\n')
  if (lines.some((l) => l.startsWith('Note: '))) return noteOf(output)
  const title = lines.find((l) => l.startsWith('Title: '))?.slice(7)
  const message = new URLSearchParams(lines[0].split('?')[1] ?? '').get('msg')
  return [title, message].filter(Boolean).join(' · ') || noteOf(output)
}

const SPEEDS = [{ label: '1×', ms: 2000 }, { label: '2×', ms: 1000 }, { label: '4×', ms: 500 }]

export function Replay({ events }: { events: RunEvent[] }) {
  const frames = useMemo(() => buildFrames(events), [events])
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(0)
  const last = Math.max(frames.length - 1, 0)
  const at = Math.min(index, last)

  useEffect(() => {
    if (!playing) return
    const timer = window.setInterval(() => {
      setIndex((i) => {
        if (i >= last) {
          setPlaying(false)
          return i
        }
        return i + 1
      })
    }, SPEEDS[speed].ms)
    return () => window.clearInterval(timer)
  }, [playing, speed, last])

  if (!frames.length) return <Empty icon={Rewind} title="Nothing to replay yet" />
  const frame = frames[at]
  const go = (i: number) => setIndex(Math.max(0, Math.min(last, i)))

  return (
    <div
      className="p-4 outline-none"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight') go(at + 1)
        if (e.key === 'ArrowLeft') go(at - 1)
      }}
      aria-label="Run replay. Use the arrow keys to step."
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => go(0)} aria-label="First moment"><ChevronsLeft className="size-4" /></Button>
        <Button size="sm" variant="ghost" onClick={() => go(at - 1)} aria-label="Previous moment"><ChevronLeft className="size-4" /></Button>
        <Button size="sm" variant="secondary" onClick={() => { if (at >= last) go(0); setPlaying(!playing) }} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => go(at + 1)} aria-label="Next moment"><ChevronRight className="size-4" /></Button>
        <Button size="sm" variant="ghost" onClick={() => go(last)} aria-label="Last moment"><ChevronsRight className="size-4" /></Button>
        <button onClick={() => setSpeed((speed + 1) % SPEEDS.length)} className="rounded-md px-2 py-1 font-mono text-xs text-ink-2 hover:bg-surface-3" aria-label="Playback speed">
          {SPEEDS[speed].label}
        </button>
        <input
          type="range"
          min={0}
          max={last}
          value={at}
          onChange={(e) => go(Number(e.target.value))}
          className="min-w-32 flex-1 accent-[var(--accent)]"
          aria-label="Moment in the run"
        />
        <span className="font-mono text-xs text-ink-3 tabular-nums">
          {at + 1} / {frames.length}
        </span>
      </div>

      <FrameView frame={frame} />
    </div>
  )
}

function FrameView({ frame }: { frame: Frame }) {
  const head = frame.head
  const d = head.data
  const known = describe(head)
  const [page, setPage] = useState<number | null>(null)
  const isDecision = head.type === 'decision'

  return (
    <div className="mt-4 space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
        <span className="font-mono">{clock(frame.at)}</span>
        <Badge tone="accent">{humanise(frame.phase)}</Badge>
        {frame.step && <span>step <strong className="font-medium text-ink-2">{humanise(frame.step)}</strong></span>}
      </div>

      <section>
        <h3 className="text-xs font-semibold tracking-wide text-ink-3 uppercase">{isDecision ? 'The operator decided' : 'What happened'}</h3>
        {isDecision ? (
          <div className="mt-2 space-y-1.5">
            <p className="text-sm font-medium text-ink">
              {d.kind === 'tool' ? (
                <>
                  {d.call?.tool} {d.call?.arguments?.action && <Mono>{d.call.arguments.action}</Mono>}
                  {(d.then ?? []).length > 0 && <span className="font-normal text-ink-3"> then {(d.then as { tool: string }[]).map((c) => c.tool).join(', ')}</span>}
                </>
              ) : (
                `${humanise(d.kind)}${d.step_id ? `: ${humanise(d.step_id)}` : ''}`
              )}
            </p>
            {(d.note || d.reason) && <p className="border-l-2 border-accent pl-3 text-sm italic text-ink-2">“{d.note || d.reason}”</p>}
            {d.expectation && (
              <p className="text-xs text-ink-3">
                <strong className="font-medium text-ink-2">Expected:</strong> {d.expectation}
              </p>
            )}
            {d.call?.arguments && <p className="font-mono text-[11px] break-words text-ink-3">{compactArgs(d.call.arguments)}</p>}
          </div>
        ) : (
          <p className="mt-2 text-sm text-ink">
            {known?.title ?? head.type}
            {known?.detail && <span className="mt-1 block text-xs text-ink-3">{String(known.detail)}</span>}
          </p>
        )}
      </section>

      {frame.calls.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold tracking-wide text-ink-3 uppercase">Then it saw</h3>
          <ol className="mt-2 space-y-2">
            {frame.calls.map(({ call, result, policy }, i) => {
              const r = result?.data
              const output: string = r?.output ?? ''
              return (
                <li key={call.seq} className="rounded-lg border border-line">
                  <div className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
                    <span className={cx('size-2 rounded-full', !r ? 'bg-line-strong' : r.ok ? 'bg-good' : r.error === 'needs_approval' ? 'bg-warning' : 'bg-critical')} aria-hidden />
                    <span className="font-medium text-ink">{call.data.tool}</span>
                    <span className="text-ink-3">{!r ? 'no result recorded' : r.ok ? 'ok' : humanise(r.error)}</span>
                    {policy.map((p) => (
                      <Badge key={p.seq} tone={p.type === 'guard.blocked' ? 'critical' : p.data.outcome === 'needs_approval' ? 'warning' : 'neutral'}>
                        {p.type === 'guard.blocked' ? 'blocked' : p.type === 'policy.grant' ? `grant ${p.data.grant}` : `policy: ${humanise(p.data.outcome)}`}
                      </Badge>
                    ))}
                    {output.startsWith('URL: ') && (
                      <button onClick={() => setPage(page === i ? null : i)} className="ml-auto text-accent-ink hover:underline">
                        {page === i ? 'Hide page' : 'Show the page it read'}
                      </button>
                    )}
                  </div>
                  {output && <p className="border-t border-line px-3 py-1.5 text-xs text-ink-2">{summaryOf(output)}</p>}
                  {page === i && (
                    <pre className="max-h-80 overflow-auto border-t border-line bg-surface-2 px-3 py-2 font-mono text-[11px] leading-5 whitespace-pre-wrap text-ink-2">{output}</pre>
                  )}
                </li>
              )
            })}
          </ol>
        </section>
      )}

      {frame.after.filter((e) => e.type !== 'phase').length > 0 && (
        <section>
          <h3 className="text-xs font-semibold tracking-wide text-ink-3 uppercase">And so</h3>
          <ul className="mt-2 space-y-1">
            {frame.after.filter((e) => e.type !== 'phase').map((e) => {
              const info = describe(e)
              return (
                <li key={e.seq} className="text-sm text-ink-2">
                  {info?.title ?? e.type}
                  {info?.detail && <span className="block text-xs text-ink-3">{String(info.detail)}</span>}
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </div>
  )
}
