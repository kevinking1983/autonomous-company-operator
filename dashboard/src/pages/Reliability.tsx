import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  ExternalLink,
  FlaskConical,
  Gauge,
  Loader2,
  Play,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Square,
  Timer,
  Wrench,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/Layout'
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, Mono, Skeleton, StatTile, Tabs } from '../components/ui'
import { api, type EvalCatalog, type EvalDetail, type EvalMeta, type EvalResult, type FaultConfig, type SandboxFaults } from '../lib/api'
import { humanise, percent, timeAgo } from '../lib/format'
import { usePoll } from '../lib/hooks'
import { cx } from '../lib/ui-utils'

const ACTIVE = new Set(['queued', 'running', 'stopping'])
const MODEL_CALLS_PER_RUN = 30

export function Reliability() {
  const [params, setParams] = useSearchParams()
  const { data: catalog } = usePoll<EvalCatalog>('/evals/catalog')
  const { data: evals, refresh: refreshEvals } = usePoll<EvalMeta[]>('/evals', 3000)
  const [showNew, setShowNew] = useState(false)
  const selected = params.get('eval') ?? evals?.[0]?.id ?? null
  const select = (id: string) => setParams({ eval: id })

  return (
    <>
      <PageHeader
        title="Reliability lab"
        subtitle="The operator on known scenarios, under injected faults, scored against the systems' own records, never its own account."
        actions={
          <Button onClick={() => setShowNew(!showNew)} aria-expanded={showNew}>
            <FlaskConical className="size-4" aria-hidden /> New eval
          </Button>
        }
      />
      {showNew && catalog && (
        <NewEval
          catalog={catalog}
          busy={Boolean(evals?.some((e) => ACTIVE.has(e.status)))}
          onStarted={(id) => {
            setShowNew(false)
            void refreshEvals()
            select(id)
          }}
        />
      )}

      {!evals ? (
        <Skeleton className="h-64" />
      ) : evals.length === 0 ? (
        <Card>
          <Empty icon={FlaskConical} title="No evals yet">
            Start one with <strong>New eval</strong>, or from a terminal: <Mono>make eval SUITE=smoke</Mono>.
          </Empty>
        </Card>
      ) : (
        selected && catalog && <EvalView key={selected} evalId={selected} catalog={catalog} />
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Switchboard />
        <History evals={evals ?? []} selected={selected} onSelect={select} />
      </div>
    </>
  )
}

// ───────────────────────── one eval ─────────────────────────

function EvalView({ evalId, catalog }: { evalId: string; catalog: EvalCatalog }) {
  const { data, error } = usePoll<EvalDetail>(`/evals/${evalId}`, 3000)
  const [picked, setPicked] = useState<[string, string] | null>(null)
  if (error) return <ErrorNote message={error} />
  if (!data) return <Skeleton className="h-64" />
  const { meta, results, scorecard: card } = data
  const byPair = new Map(results.map((r) => [`${r.case}|${r.profile}`, r]))
  const active = ACTIVE.has(meta.status)
  const result = picked ? byPair.get(picked.join('|')) : undefined
  // Many fault profiles: the matrix takes the full width and the side cards go underneath it.
  const wide = new Set(meta.pairs.map(([, p]) => p)).size > 4

  return (
    <div className="space-y-6">
      {active && <Progress meta={meta} done={results.length} />}
      {meta.status === 'paused' && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-warning/40 bg-warning-soft px-4 py-3 text-sm text-warning-ink">
          <AlertTriangle className="size-4" aria-hidden /> Paused: {meta.reason ?? 'stopped early'}. Results so far are kept; resume when the model is available again.
          <ResumeButton evalId={evalId} />
        </div>
      )}
      {meta.status === 'stopped' && results.length < meta.pairs.length && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm text-ink-2">
          Stopped after {results.length} of {meta.pairs.length} runs{meta.reason ? ` (${meta.reason})` : ''}.
          <ResumeButton evalId={evalId} />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Passed"
          value={percent(card.pass_rate)}
          icon={CheckCircle2}
          tone="good"
          detail={`${card.passed} of ${card.scored} scored runs${card.errors ? ` · ${card.errors} not scored` : ''}`}
        />
        <StatTile label="Money exactly right" value={percent(card.money_correct_rate)} icon={ShieldCheck} tone="accent" detail="right amount, right place, once" />
        <StatTile
          label="Unsafe runs"
          value={card.unsafe_runs}
          icon={ShieldAlert}
          tone={card.unsafe_runs ? 'critical' : 'good'}
          detail={card.unsafe_runs ? 'money moved where it should not have' : 'no money where it should not be'}
        />
        <StatTile
          label="Own verifier agrees"
          value={percent(card.verifier_agreement)}
          icon={Gauge}
          tone={card.verifier_false_passes ? 'warning' : 'neutral'}
          detail={`${card.verifier_false_passes} false pass${card.verifier_false_passes === 1 ? '' : 'es'} (said done, was not)`}
        />
      </div>

      <div className={cx('grid grid-cols-1 gap-6', wide ? 'xl:grid-cols-2' : 'xl:grid-cols-3')}>
        <Card className="xl:col-span-2">
          <CardHeader
            title={`${meta.label} · ${meta.id}`}
            subtitle={`Cases down, fault profiles across. Models: ${meta.models.join(', ') || 'scripted'}`}
            icon={FlaskConical}
          />
          <Matrix meta={meta} byPair={byPair} catalog={catalog} picked={picked} onPick={setPicked} />
        </Card>
        <div className={wide ? 'contents' : 'space-y-6'}>
          <Card>
            <CardHeader title="Pass rate by fault profile" subtitle="Share of runs passing every critical check" icon={Wrench} />
            <ProfileBars card={card.by_profile} catalog={catalog} />
          </Card>
          <Card>
            <CardHeader title="Cost and effort per run" icon={Timer} />
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 p-5 text-sm">
              <Metric label="Tool calls" value={card.avg.tool_calls} />
              <Metric label="Model calls" value={card.avg.llm_calls} />
              <Metric label="Seconds" value={card.avg.seconds} />
              <Metric label="Adaptations" value={card.avg.adaptations} />
              <Metric label="Faults injected (total)" value={card.faults_injected} />
              <Metric label="Asked a person right" value={percent(card.human_gate_rate)} />
            </dl>
          </Card>
        </div>
      </div>

      {picked && (
        <ResultDetail evalId={evalId} pair={picked} result={result} catalog={catalog} onClose={() => setPicked(null)} />
      )}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number | string | null }) {
  return (
    <div>
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="font-semibold tabular-nums">{value ?? '—'}</dd>
    </div>
  )
}

function ResumeButton({ evalId }: { evalId: string }) {
  const [error, setError] = useState<string | null>(null)
  return (
    <>
      <Button size="sm" variant="secondary" onClick={() => api.post(`/evals/${evalId}/resume`, {}).catch((e: Error) => setError(e.message))}>
        <Play className="size-3.5" aria-hidden /> Resume
      </Button>
      {error && <span className="text-xs text-critical-ink">{error}</span>}
    </>
  )
}

function Progress({ meta, done }: { meta: EvalMeta; done: number }) {
  const { data: log } = usePoll<{ lines: string[] }>(`/evals/${meta.id}/log?lines=12`, 3000)
  const total = meta.pairs.length
  const share = total ? done / total : 0
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4">
        <div className="flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin text-accent-ink" aria-hidden />
          <span className="font-medium">{meta.status === 'stopping' ? 'Stopping after this run' : 'Running'}</span>
          {meta.current && (
            <span className="text-ink-3">
              · {humanise(meta.current[0])} under <strong className="font-medium text-ink-2">{humanise(meta.current[1])}</strong>
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs text-ink-3 tabular-nums">
            {done} / {total}
          </span>
          {meta.status !== 'stopping' && (
            <Button size="sm" variant="secondary" onClick={() => void api.post(`/evals/${meta.id}/stop`, {})}>
              <Square className="size-3.5" aria-hidden /> Stop
            </Button>
          )}
        </div>
      </div>
      <div className="mx-5 mt-3 h-2 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-valuenow={done} aria-valuemin={0} aria-valuemax={total}>
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${share * 100}%` }} />
      </div>
      {log && log.lines.length > 0 && (
        <pre className="m-5 max-h-40 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-5 text-ink-2">{log.lines.join('\n')}</pre>
      )}
    </Card>
  )
}

// Status always comes with an icon and a word, never colour alone.
const CELL: Record<string, { icon: LucideIcon; label: string; className: string }> = {
  pass: { icon: CheckCircle2, label: 'Pass', className: 'bg-good-soft text-good-ink' },
  fail: { icon: XCircle, label: 'Fail', className: 'bg-critical-soft text-critical-ink' },
  error: { icon: AlertTriangle, label: 'Not scored', className: 'bg-warning-soft text-warning-ink' },
  running: { icon: Loader2, label: 'Running', className: 'bg-accent-soft text-accent-ink [&>svg]:animate-spin' },
  pending: { icon: Circle, label: 'To do', className: 'text-ink-3' },
}

function Matrix({
  meta,
  byPair,
  catalog,
  picked,
  onPick,
}: {
  meta: EvalMeta
  byPair: Map<string, EvalResult>
  catalog: EvalCatalog
  picked: [string, string] | null
  onPick: (pair: [string, string]) => void
}) {
  const cases = [...new Set(meta.pairs.map(([c]) => c))]
  const profiles = catalog.profiles.map((p) => p.name).filter((p) => meta.pairs.some(([, q]) => q === p))
  const planned = new Set(meta.pairs.map((p) => p.join('|')))
  const ticket = (key: string) => catalog.cases.find((c) => c.key === key)?.ticket_id
  const label = (name: string) => catalog.profiles.find((p) => p.name === name)?.label ?? name
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink-3">
            <th className="px-5 py-2.5 font-medium">Case</th>
            {profiles.map((p) => (
              <th key={p} className="px-2 py-2.5 text-center font-medium whitespace-nowrap">{label(p)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c} className="border-b border-line last:border-0">
              <td className="px-5 py-2">
                <p className="font-medium whitespace-nowrap">{humanise(c)}</p>
                <p className="font-mono text-[11px] text-ink-3">{ticket(c)}</p>
              </td>
              {profiles.map((p) => {
                const key = `${c}|${p}`
                if (!planned.has(key)) return <td key={p} className="px-2 py-2 text-center text-xs text-ink-3">·</td>
                const r = byPair.get(key)
                const running = meta.current?.[0] === c && meta.current?.[1] === p && ACTIVE.has(meta.status)
                const cell = CELL[r?.status ?? (running ? 'running' : 'pending')]
                const Icon = cell.icon
                const isPicked = picked?.[0] === c && picked?.[1] === p
                return (
                  <td key={p} className="px-2 py-2 text-center">
                    <button
                      onClick={() => onPick([c, p])}
                      disabled={!r}
                      className={cx(
                        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
                        cell.className,
                        r && 'hover:ring-2 hover:ring-line-strong',
                        isPicked && 'ring-2 ring-accent',
                      )}
                      aria-label={`${humanise(c)} under ${label(p)}: ${cell.label}`}
                    >
                      <Icon className="size-3.5" aria-hidden />
                      {cell.label}
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ProfileBars({ card, catalog }: { card: Record<string, { runs: number; passed: number; pass_rate: number | null }>; catalog: EvalCatalog }) {
  const rows = catalog.profiles.filter((p) => card[p.name])
  if (!rows.length) return <p className="p-5 text-sm text-ink-3">No scored runs yet.</p>
  return (
    <ul className="space-y-3 p-5" aria-label="Pass rate by fault profile">
      {rows.map((p) => {
        const g = card[p.name]
        const share = g.pass_rate ?? 0
        return (
          <li key={p.name} title={p.description}>
            <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
              <span className="text-ink-2">{p.label}</span>
              <span className="font-mono text-ink-3 tabular-nums">
                {g.passed}/{g.runs} · {percent(g.pass_rate)}
              </span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-surface-3">
              <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(share * 100, share ? 2 : 0)}%` }} />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function ResultDetail({
  evalId,
  pair,
  result,
  catalog,
  onClose,
}: {
  evalId: string
  pair: [string, string]
  result: EvalResult | undefined
  catalog: EvalCatalog
  onClose: () => void
}) {
  const [caseKey, profile] = pair
  const info = catalog.cases.find((c) => c.key === caseKey)
  const prof = catalog.profiles.find((p) => p.name === profile)
  if (!result) return null
  const cell = CELL[result.status]
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {humanise(caseKey)} under {prof?.label ?? profile}
            <Badge tone={result.status === 'pass' ? 'good' : result.status === 'fail' ? 'critical' : 'warning'} icon={cell.icon}>
              {cell.label}
            </Badge>
          </span>
        }
        subtitle={`${info?.ticket_id} · ${info?.summary}${prof ? ` · ${prof.description}` : ''}`}
        action={
          <div className="flex items-center gap-2">
            {result.run_id && (
              <Link
                to={`/runs/${result.run_id}?scope=${encodeURIComponent(`eval:${evalId}:${caseKey}:${profile}`)}`}
                className="inline-flex items-center gap-1 text-xs text-accent-ink hover:underline"
              >
                Open the run <ExternalLink className="size-3" />
              </Link>
            )}
            <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
              ✕
            </Button>
          </div>
        }
      />
      {result.error && <p className="border-b border-line px-5 py-2 text-sm text-warning-ink">{result.error}</p>}
      <div className="grid grid-cols-1 gap-6 p-5 lg:grid-cols-[1fr_260px]">
        <ul className="space-y-2">
          {result.checks.map((c) => (
            <li key={c.id + c.label} className="flex gap-2.5 text-sm">
              {c.passed ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good-ink" aria-label="passed" />
              ) : c.critical ? (
                <XCircle className="mt-0.5 size-4 shrink-0 text-critical-ink" aria-label="failed" />
              ) : (
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning-ink" aria-label="noted" />
              )}
              <span>
                {c.label}
                {!c.critical && <span className="ml-1.5 text-xs text-ink-3">(noted, not required)</span>}
                <span className="block text-xs text-ink-3">{c.detail}</span>
              </span>
            </li>
          ))}
        </ul>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 self-start rounded-lg bg-surface-2 p-4 text-sm">
          <Metric label="Run ended" value={result.phase ?? '—'} />
          <Metric label="Own verifier" value={result.verified === null ? '—' : result.verified ? 'passed' : 'failed'} />
          <Metric label="Tool calls" value={result.metrics.tool_calls ?? null} />
          <Metric label="Model calls" value={result.metrics.llm_calls ?? null} />
          <Metric label="Seconds" value={result.metrics.seconds ?? null} />
          <Metric label="Faults hit" value={result.faults_injected} />
          <Metric label="Adaptations" value={result.metrics.adaptations ?? null} />
          <Metric label="Approvals asked" value={result.approvals_asked} />
        </dl>
      </div>
    </Card>
  )
}

// ───────────────────────── starting an eval ─────────────────────────

function NewEval({ catalog, busy, onStarted }: { catalog: EvalCatalog; busy: boolean; onStarted: (id: string) => void }) {
  const [mode, setMode] = useState<string>('smoke')
  const [cases, setCases] = useState<Set<string>>(new Set(['missing_item']))
  const [profiles, setProfiles] = useState<Set<string>>(new Set(['clean']))
  const [error, setError] = useState<string | null>(null)
  const suite = catalog.suites.find((s) => s.id === mode)
  const custom = [...cases].flatMap((c) => {
    const tags = catalog.cases.find((x) => x.key === c)?.tags ?? []
    return [...profiles].filter((p) => {
      const applies = catalog.profiles.find((x) => x.name === p)?.applies_to ?? []
      return !applies.length || applies.some((t) => tags.includes(t))
    })
  })
  const runs = suite ? suite.pairs.length : custom.length
  const toggle = (set: Set<string>, value: string, update: (s: Set<string>) => void) => {
    const next = new Set(set)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    update(next)
  }

  async function start() {
    setError(null)
    try {
      const meta = await api.post<EvalMeta>('/evals', suite ? { suite: suite.id } : { cases: [...cases], profiles: [...profiles] })
      onStarted(meta.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Card className="mb-6">
      <CardHeader title="New eval" subtitle="Each run resets the sandbox, injects the profile's faults, runs one ticket and scores the result" icon={FlaskConical} />
      <div className="space-y-4 p-5">
        <Tabs value={mode} onChange={setMode} tabs={[...catalog.suites.map((s) => ({ id: s.id, label: s.label })), { id: 'custom', label: 'Custom' }]} />
        {suite ? (
          <p className="text-sm text-ink-2">{suite.description}</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <fieldset>
              <legend className="mb-2 text-xs font-semibold tracking-wide text-ink-3 uppercase">Cases</legend>
              <div className="grid max-h-64 grid-cols-1 gap-1 overflow-y-auto">
                {catalog.cases.map((c) => (
                  <label key={c.key} className="flex items-start gap-2 rounded-md px-2 py-1 text-sm hover:bg-surface-2">
                    <input type="checkbox" checked={cases.has(c.key)} onChange={() => toggle(cases, c.key, setCases)} className="mt-0.5 size-4 accent-[var(--accent)]" />
                    <span>
                      {humanise(c.key)} <span className="font-mono text-[11px] text-ink-3">{c.ticket_id}</span>
                      <span className="block text-xs text-ink-3">{c.summary}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-2 text-xs font-semibold tracking-wide text-ink-3 uppercase">Fault profiles</legend>
              <div className="grid grid-cols-1 gap-1">
                {catalog.profiles.map((p) => (
                  <label key={p.name} className="flex items-start gap-2 rounded-md px-2 py-1 text-sm hover:bg-surface-2">
                    <input type="checkbox" checked={profiles.has(p.name)} onChange={() => toggle(profiles, p.name, setProfiles)} className="mt-0.5 size-4 accent-[var(--accent)]" />
                    <span>
                      {p.label}
                      <span className="block text-xs text-ink-3">{p.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
          <p className="text-xs text-ink-3">
            <strong className="text-ink-2">{runs} run{runs === 1 ? '' : 's'}</strong>, about {runs * MODEL_CALLS_PER_RUN} model calls. Runs one at a time and
            resets the sandbox, so pause other work first.
          </p>
          <Button onClick={() => void start()} disabled={busy || !runs}>
            <Play className="size-4" aria-hidden /> {busy ? 'An eval is running' : 'Start'}
          </Button>
        </div>
        {error && <ErrorNote message={error} />}
      </div>
    </Card>
  )
}

// ───────────────────────── the live sandbox's fault switchboard ─────────────────────────

function Switchboard() {
  const { data, error, refresh } = usePoll<SandboxFaults>('/sandbox/faults', 5000)
  const [draft, setDraft] = useState<Partial<FaultConfig> | null>(null)
  const [busy, setBusy] = useState(false)
  const config = { ...(data?.config ?? {}), ...(draft ?? {}) } as FaultConfig
  const set = (patch: Partial<FaultConfig>) => setDraft({ ...(draft ?? {}), ...patch })

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    try {
      await action()
      setDraft(null)
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader
        title="Fault switchboard"
        subtitle="Make the live sandbox misbehave, then give the operator a ticket and watch it adapt"
        icon={Wrench}
        action={data && <Badge tone={data.injected ? 'warning' : 'neutral'}>{data.injected} injected</Badge>}
      />
      {error ? (
        <div className="p-5"><ErrorNote message={`Sandbox not reachable: ${error}`} /></div>
      ) : !data ? (
        <Skeleton className="m-5 h-40" />
      ) : (
        <div className="space-y-4 p-5">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="text-sm">
              <span className="text-xs font-medium text-ink-2">Failing requests: {Math.round(config.error_rate * 100)}%</span>
              <input type="range" min={0} max={0.5} step={0.05} value={config.error_rate} onChange={(e) => set({ error_rate: Number(e.target.value) })} className="mt-1 w-full accent-[var(--accent)]" />
            </label>
            <label className="text-sm">
              <span className="text-xs font-medium text-ink-2">Extra latency</span>
              <select value={config.latency_ms} onChange={(e) => set({ latency_ms: Number(e.target.value) })} className="mt-1 h-9 w-full rounded-lg border border-line-strong bg-surface px-2 text-sm">
                {[0, 500, 1500, 3000].map((ms) => <option key={ms} value={ms}>{ms ? `${ms / 1000}s per request` : 'None'}</option>)}
              </select>
            </label>
            <NumberField label="Sign out after N more requests (0 = never)" value={config.session_expiry_in} onChange={(v) => set({ session_expiry_in: v })} />
            <NumberField label="Reject the next N forms as stale" value={config.stale_form_next} onChange={(v) => set({ stale_form_next: v })} />
            <NumberField label="Next N refunds commit, then time out" value={config.refund_commit_timeout_next} onChange={(v) => set({ refund_commit_timeout_next: v })} />
            <label className="flex items-center gap-2 self-end text-sm">
              <input type="checkbox" checked={config.layout === 'shifted'} onChange={(e) => set({ layout: e.target.checked ? 'shifted' : 'standard' })} className="size-4 accent-[var(--accent)]" />
              Redesigned layout (buttons renamed and moved)
            </label>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void run(() => api.post('/sandbox/reset', {}))}>
              <RotateCcw className="size-3.5" aria-hidden /> Reset the world
            </Button>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" disabled={busy} onClick={() => void run(() => api.put('/sandbox/faults', {}))}>
                All faults off
              </Button>
              <Button size="sm" disabled={busy || !draft} onClick={() => void run(() => api.put('/sandbox/faults', config))}>
                Apply
              </Button>
            </div>
          </div>
          {data.recent.length > 0 && (
            <ul className="space-y-0.5 font-mono text-[11px] text-ink-3">
              {data.recent.slice(-4).map((f, i) => (
                <li key={i}>
                  {timeAgo(f.at)} · {f.kind} · {f.system} {f.path}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="text-sm">
      <span className="text-xs font-medium text-ink-2">{label}</span>
      <input type="number" min={0} max={50} value={value} onChange={(e) => onChange(Math.max(0, Number(e.target.value)))} className="mt-1 h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm" />
    </label>
  )
}

function History({ evals, selected, onSelect }: { evals: EvalMeta[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <Card>
      <CardHeader title="Eval history" subtitle="Every eval keeps its runs, so any result can be opened later" icon={FlaskConical} />
      {evals.length === 0 ? (
        <Empty icon={FlaskConical} title="None yet" />
      ) : (
        <ul className="divide-y divide-line">
          {evals.map((e) => (
            <li key={e.id}>
              <button onClick={() => onSelect(e.id)} className={cx('flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-surface-2', e.id === selected && 'bg-accent-soft/50')}>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{e.label}</p>
                  <p className="font-mono text-[11px] text-ink-3">
                    {e.id} · {timeAgo(e.created_at)}
                  </p>
                </div>
                <span className="text-xs text-ink-3 tabular-nums">
                  {e.done}/{e.total}
                </span>
                <EvalStatusBadge status={e.status} />
                <span className="w-12 text-right text-sm font-semibold tabular-nums">{percent(e.pass_rate ?? null)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function EvalStatusBadge({ status }: { status: EvalMeta['status'] }) {
  const meta = {
    queued: { tone: 'neutral', icon: Circle, label: 'Queued' },
    running: { tone: 'accent', icon: Loader2, label: 'Running' },
    stopping: { tone: 'accent', icon: Loader2, label: 'Stopping' },
    stopped: { tone: 'neutral', icon: Square, label: 'Stopped' },
    paused: { tone: 'warning', icon: AlertTriangle, label: 'Paused' },
    completed: { tone: 'good', icon: CheckCircle2, label: 'Done' },
  }[status] as { tone: 'neutral' | 'accent' | 'warning' | 'good'; icon: LucideIcon; label: string }
  return (
    <Badge tone={meta.tone} icon={meta.icon} className={ACTIVE.has(status) ? '[&>svg]:animate-spin' : undefined}>
      {meta.label}
    </Badge>
  )
}
