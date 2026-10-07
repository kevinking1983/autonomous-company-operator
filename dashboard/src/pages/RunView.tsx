import {
  AlertTriangle,
  ArrowLeft,
  Ban,
  Bot,
  Brain,
  CheckCircle2,
  Circle,
  CircleDot,
  Cpu,
  FileSignature,
  Hand,
  Hourglass,
  ImageIcon,
  KeyRound,
  ListChecks,
  Loader2,
  MousePointerClick,
  ShieldCheck,
  ShieldX,
  Sparkles,
  X,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { LiveBrowser } from '../components/LiveBrowser'
import { Replay } from '../components/Replay'
import { PhaseTracker } from '../components/PhaseTracker'
import { Badge, Card, CardHeader, Empty, ErrorNote, Mono, PhasePill, Skeleton, Tabs } from '../components/ui'
import { cx } from '../lib/ui-utils'
import { describe } from '../lib/activity'
import { api, scoped, type Report, type RunEvent, type RunState } from '../lib/api'
import { clock, compactArgs, humanise, noteOf, rupees, timeAgo } from '../lib/format'
import { usePoll, useRunStream } from '../lib/hooks'

const FINISHED = new Set(['completed', 'escalated', 'failed'])

export function RunView() {
  const { runId = '' } = useParams()
  // A run made by an eval: ?scope=eval:<eval id>:<case>:<profile>
  const scope = useSearchParams()[0].get('scope')
  const evalId = scope?.split(':')[1]
  const { data: state, error } = usePoll<RunState>(scoped(`/runs/${runId}`, scope), 2000)
  const finished = state ? FINISHED.has(state.phase) : false
  const { data: report } = usePoll<Report>(scoped(`/runs/${runId}/report`, scope), finished ? 0 : 4000)
  const { events, live } = useRunStream<RunEvent>(api.streamUrl(runId, 0, scope))
  const [tab, setTab] = useState<'story' | 'replay' | 'everything'>('story')

  const visited = useMemo(() => {
    const set = new Set<string>()
    for (const e of events) if (e.type === 'phase') set.add(e.data.from)
    if (state) set.add(state.phase)
    return set
  }, [events, state])

  const lastUrl = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const output = events[i].type === 'tool.result' ? events[i].data.output : undefined
      if (typeof output === 'string' && output.startsWith('URL: ')) return output.slice(5, output.indexOf('\n')).split('?')[0]
    }
    return null
  }, [events])

  if (error) return <ErrorNote message={error} />
  if (!state) return <Skeleton className="h-96" />

  const results = new Map(state.verification?.results.map((r) => [r.criterion_id, r]) ?? [])
  const integrity = state.verification?.results.filter((r) => r.criterion_id.startsWith('integrity.')) ?? []

  return (
    <>
      <Link to={evalId ? `/reliability?eval=${evalId}` : '/runs'} className="mb-3 inline-flex items-center gap-1 text-xs text-ink-3 hover:text-ink">
        <ArrowLeft className="size-3.5" aria-hidden /> {evalId ? `Eval ${evalId}` : 'All runs'}
      </Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">
            {state.request.ticket_id && <span className="mr-2 font-mono">{state.request.ticket_id}</span>}
            {state.request.ticket_id ? <span className="text-ink-2">{state.contract?.category ? humanise(state.contract.category) : 'Support ticket'}</span> : state.request.text}
          </h1>
          <p className="mt-1 text-sm text-ink-3">
            Run <Mono>{state.run_id}</Mono> · requested by {state.request.requested_by} via {state.request.source} · started {timeAgo(state.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {state.verification?.passed && <Badge tone="good" icon={ShieldCheck}>Independently verified</Badge>}
          <PhasePill phase={state.phase} />
        </div>
      </div>

      {state.phase === 'awaiting_human' && state.pending_human && (
        <Banner tone="warning" icon={Hourglass}>
          Paused for a person ({state.pending_human}).{' '}
          <Link to="/inbox" className="font-medium underline">Open the approval inbox</Link>
        </Banner>
      )}
      {state.phase === 'escalated' && (
        <Banner tone="serious" icon={AlertTriangle}>
          Handed over to a person: {state.outcome_reason}
        </Banner>
      )}
      {state.phase === 'failed' && (
        <Banner tone="critical" icon={XCircle}>
          Failed: {state.outcome_reason}
        </Banner>
      )}

      <Card className="mb-6 px-5 pt-5 pb-3">
        <PhaseTracker phase={state.phase} visited={visited} live={live && !finished && state.phase !== 'awaiting_human'} />
      </Card>

      {state.summary && (
        <Card className="mb-6">
          <CardHeader title="Summary for the supervisor" icon={Sparkles} />
          <p className="px-5 py-4 text-sm leading-relaxed text-ink-2 whitespace-pre-line">{state.summary}</p>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-5">
        <div className="space-y-6 xl:col-span-3">
          <Card>
            <CardHeader
              title="Timeline"
              subtitle={live ? 'Streaming live from the audit log' : `${events.length} audit events`}
              action={<Tabs value={tab} onChange={setTab} tabs={[{ id: 'story', label: 'Decisions' }, { id: 'replay', label: 'Replay' }, { id: 'everything', label: 'Everything' }]} />}
            />
            {tab === 'replay' ? (
              <Replay events={events} />
            ) : (
              <Timeline events={events} everything={tab === 'everything'} live={live && !finished && state.phase !== 'awaiting_human'} />
            )}
          </Card>
          {report && report.changes.length > 0 && <Changes report={report} />}
          <Evidence runId={state.run_id} scope={scope} names={report?.evidence ?? []} />
        </div>

        <div className="space-y-6 xl:col-span-2">
          <LiveBrowser runId={state.run_id} scope={scope} working={!finished && state.phase !== 'awaiting_human'} url={lastUrl} />
          <Card>
            <CardHeader title="Task contract" subtitle="Written before acting; checked independently at the end" icon={FileSignature} />
            {state.contract ? (
              <div className="space-y-3 p-5">
                <p className="text-sm text-ink">{state.contract.outcome}</p>
                <ul className="space-y-2">
                  {state.contract.success_criteria.map((c) => (
                    <CriterionRow key={c.id} id={c.id} text={c.text} result={results.get(c.id)} />
                  ))}
                  {integrity.map((r) => (
                    <CriterionRow key={r.criterion_id} id={r.criterion_id} text="The same money movement never happened twice (checked in code)" result={r} />
                  ))}
                </ul>
                {state.contract.assumptions.length > 0 && (
                  <p className="text-xs text-ink-3">
                    <strong className="font-medium text-ink-2">Assumes:</strong> {state.contract.assumptions.join('; ')}
                  </p>
                )}
                {state.contract.sop_ids.length > 0 && (
                  <p className="flex flex-wrap gap-1 text-xs text-ink-3">
                    Following {state.contract.sop_ids.map((s) => <Mono key={s}>{s}</Mono>)}
                  </p>
                )}
              </div>
            ) : (
              <Empty icon={Loader2} title="Still understanding the request" />
            )}
          </Card>

          <Card>
            <CardHeader title={`Plan${state.plan ? ` (v${state.plan.version})` : ''}`} subtitle={state.plan?.rationale || 'Each step has a result that must be seen before it counts'} icon={ListChecks} />
            {state.plan ? (
              <ol className="space-y-1 p-3">
                {state.plan.steps.map((s) => (
                  <li key={s.id} className={cx('flex gap-2.5 rounded-lg px-2 py-2', s.id === state.current_step && !finished && 'bg-accent-soft')}>
                    <StepIcon status={s.status} current={s.id === state.current_step && !finished} />
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{humanise(s.id)}</p>
                      <p className="text-xs text-ink-3">{s.goal}</p>
                      {s.outcome && <p className="mt-0.5 text-xs text-ink-2">→ {s.outcome}</p>}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <Empty icon={Loader2} title="No plan yet" />
            )}
          </Card>

          {Object.keys(state.memory).length > 0 && (
            <Card>
              <CardHeader title="Working memory" subtitle="Facts the operator kept while working" icon={Brain} />
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 p-5 text-sm">
                {Object.entries(state.memory).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-ink-3">{humanise(k)}</dt>
                    <dd className="font-mono text-xs leading-5 text-ink">{v}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          )}

          {report && (
            <Card>
              <CardHeader title="Run statistics" icon={Cpu} />
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 p-5 text-sm">
                <Stat label="Tool calls" value={report.stats.tool_calls} />
                <Stat label="Decisions" value={report.stats.decisions} />
                <Stat label="Model calls" value={report.stats.llm_calls} />
                <Stat label="Tokens in / out" value={`${(report.stats.input_tokens / 1000).toFixed(0)}k / ${(report.stats.output_tokens / 1000).toFixed(1)}k`} />
                <Stat label="Replans" value={report.stats.replans} />
                <Stat label="Verification rounds" value={report.stats.verify_rounds} />
                <Stat label="Automatic retries" value={report.stats.retries} />
                <Stat label="Blocked by policy" value={report.stats.blocked} />
              </dl>
              {report.stats.models.length > 0 && <p className="border-t border-line px-5 py-2.5 text-xs text-ink-3">Models: {report.stats.models.join(', ')}</p>}
            </Card>
          )}
          {state.hands_off.length > 0 && (
            <Banner tone="serious" icon={Hand}>
              Left for a person (escalated by their sub-tasks): {state.hands_off.join(', ')}
            </Banner>
          )}
        </div>
      </div>
    </>
  )
}

function Banner({ tone, icon: Icon, children }: { tone: 'warning' | 'serious' | 'critical'; icon: LucideIcon; children: React.ReactNode }) {
  const tones = { warning: 'border-warning/40 bg-warning-soft text-warning-ink', serious: 'border-serious/40 bg-serious-soft text-serious-ink', critical: 'border-critical/40 bg-critical-soft text-critical-ink' }
  return (
    <div role="status" className={cx('mb-5 flex items-start gap-2 rounded-xl border px-4 py-3 text-sm', tones[tone])}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="tabular font-semibold">{value}</dd>
    </div>
  )
}

function StepIcon({ status, current }: { status: string; current: boolean }) {
  if (current) return <CircleDot className="mt-0.5 size-4 shrink-0 text-accent" aria-label="current step" />
  if (status === 'done') return <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good-ink" aria-label="done" />
  if (status === 'failed') return <XCircle className="mt-0.5 size-4 shrink-0 text-critical-ink" aria-label="failed" />
  if (status === 'skipped') return <Circle className="mt-0.5 size-4 shrink-0 text-ink-3" aria-label="skipped" />
  return <Circle className="mt-0.5 size-4 shrink-0 text-line-strong" aria-label="pending" />
}

function CriterionRow({ id, text, result }: { id: string; text: string; result?: { passed: boolean; evidence: string } }) {
  const [open, setOpen] = useState(false)
  const Icon = !result ? Circle : result.passed ? ShieldCheck : ShieldX
  return (
    <li className="rounded-lg border border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left" aria-expanded={open}>
        <Icon className={cx('mt-0.5 size-4 shrink-0', !result ? 'text-ink-3' : result.passed ? 'text-good-ink' : 'text-critical-ink')} aria-hidden />
        <span className="min-w-0 flex-1 text-sm text-ink-2">
          <Mono className="mr-1.5">{id.replace('integrity.', '')}</Mono>
          {text}
        </span>
        <span className="sr-only">{!result ? 'not yet verified' : result.passed ? 'passed' : 'failed'}</span>
      </button>
      {open && result && (
        <p className="border-t border-line bg-surface-2 px-3 py-2 text-xs text-ink-2">
          <strong className="font-medium text-ink">Auditor's evidence:</strong> {result.evidence}
        </p>
      )}
    </li>
  )
}

// ───────────────────────── timeline ─────────────────────────

const STORY = new Set([
  'contract.created', 'contract.revised', 'plan.created', 'plan.replan', 'decision', 'tool.result', 'policy.decision', 'guard.blocked',
  'adapt.decision', 'step.done', 'step.failed', 'verify.result', 'human.request', 'human.decided', 'human.answered', 'run.completed',
  'run.escalated', 'run.handover', 'run.paused', 'browser.unsaved_changes_lost', 'subtasks.escalated', 'decision.ignored',
])  // fmt: skip

/** The decisions view: what the operator decided and what came of it. While understanding it only reads
 * (no decisions yet), so those reads are shown and the story starts there. */
function storyOf(events: RunEvent[]): RunEvent[] {
  let phase = 'understand'
  return events.filter((e) => {
    if (e.type === 'phase') phase = e.data.to
    if (e.type === 'tool.call') return phase === 'understand'
    return STORY.has(e.type) && !(e.type === 'tool.result' && e.data.ok) && !(e.type === 'policy.decision' && e.data.outcome === 'allow' && !e.data.facts?.amount)
  })
}

function Timeline({ events, everything, live }: { events: RunEvent[]; everything: boolean; live: boolean }) {
  const shown = everything ? events : storyOf(events)
  if (!shown.length) return <Empty icon={live ? Loader2 : Bot} title={live ? 'Waiting for the first events…' : 'No events'} />
  return (
    <ol className="max-h-[900px] overflow-y-auto px-2 py-2">
      {shown.map((e) => (
        <TimelineItem key={e.seq} event={e} />
      ))}
      {live && (
        <li className="flex items-center gap-2 px-3 py-3 text-xs text-ink-3">
          <Loader2 className="size-3.5 animate-spin" aria-hidden /> working…
        </li>
      )}
    </ol>
  )
}

function TimelineItem({ event }: { event: RunEvent }) {
  const d = event.data
  let icon: LucideIcon = Circle
  let tone = 'text-ink-3 bg-surface-3'
  let title: React.ReactNode = event.type
  let detail: React.ReactNode = null

  const known = describe(event)
  if (known) {
    icon = known.icon
    tone = TONE[known.tone]
    title = known.title
    detail = known.detail
  }
  switch (event.type) {
    case 'decision': {
      if (d.kind === 'tool' && d.call) {
        icon = d.call.arguments?.action ? KeyRound : MousePointerClick
        tone = d.call.arguments?.action ? TONE.violet : TONE.accent
        const then = (d.then ?? []).map((c: { tool: string }) => c.tool)
        title = (
          <>
            {d.call.tool.replace('browser_', '')} {d.call.arguments?.action && <Mono>{d.call.arguments.action}</Mono>}
            {then.length > 0 && <span className="text-ink-3"> + {then.join(', ')}</span>}
          </>
        )
        detail = (
          <>
            {d.note && <span className="block italic text-ink-2">“{d.note}”</span>}
            <span className="font-mono text-[11px]">{compactArgs(d.call.arguments ?? {})}</span>
          </>
        )
      } else {
        icon = d.kind === 'step_done' ? CheckCircle2 : d.kind === 'replan' ? Sparkles : Circle
        tone = d.kind === 'step_done' ? TONE.good : TONE.neutral
        title = `${humanise(d.kind)}${d.step_id ? `: ${humanise(d.step_id)}` : ''}`
        detail = d.reason
      }
      break
    }
    case 'tool.result':
      icon = XCircle
      tone = d.error === 'needs_approval' ? TONE.warning : d.error === 'policy' ? TONE.critical : TONE.serious
      title = `${d.tool.replace('browser_', '')} → ${humanise(d.error)}`
      detail = noteOf(d.output ?? '')
      break
    case 'policy.decision':
      icon = d.outcome === 'allow' ? ShieldCheck : d.outcome === 'deny' ? Ban : Hand
      tone = d.outcome === 'allow' ? TONE.good : d.outcome === 'deny' ? TONE.critical : TONE.warning
      title = (
        <>
          Policy: <Mono>{d.action}</Mono> → {d.outcome.replace('_', ' ')}
        </>
      )
      detail = (
        <>
          {d.facts?.amount !== undefined && <span className="mr-2">{rupees(Number(d.facts.amount))}</span>}
          {(d.rules ?? []).length > 0 ? `rules: ${d.rules.join(', ')}` : (d.reasons ?? []).join('; ')}
        </>
      )
      break
    case 'llm.call':
      icon = Cpu
      tone = TONE.neutral
      title = `Model call: ${d.purpose}`
      detail = `${d.model} · ${d.input_tokens?.toLocaleString()} in / ${d.output_tokens} out · ${d.latency_s}s`
      break
    case 'phase':
      return (
        <li className="flex items-center gap-2 px-3 py-1 text-[11px] uppercase tracking-wider text-ink-3">
          <span className="h-px flex-1 bg-line" />
          {d.to}
          <span className="h-px flex-1 bg-line" />
        </li>
      )
    case 'tool.call':
      icon = MousePointerClick
      tone = TONE.neutral
      title = d.tool
      detail = <span className="font-mono text-[11px]">{compactArgs(d.arguments ?? {})}</span>
      break
    case 'browser.unsaved_changes_lost':
      icon = AlertTriangle
      tone = TONE.warning
      title = 'Changes in another form were not saved'
      detail = (d.fields ?? []).join(', ')
      break
    case 'evidence.saved':
      icon = ImageIcon
      tone = TONE.neutral
      title = `Evidence saved: ${String(d.path ?? '').split('/').pop()}`
      break
    case 'decision.ignored':
      icon = X
      tone = TONE.warning
      title = 'Decision ignored by the runtime'
      detail = d.reason
      break
  }
  const Icon = icon
  return (
    <li className="flex gap-3 rounded-lg px-3 py-2 hover:bg-surface-2">
      <span className={cx('mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg', tone)}>
        <Icon className="size-3.5" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-sm font-medium text-ink">{title}</p>
          <time className="shrink-0 font-mono text-[11px] text-ink-3">{clock(event.at)}</time>
        </div>
        {detail && <div className="mt-0.5 text-xs break-words text-ink-3">{detail}</div>}
      </div>
    </li>
  )
}

const TONE: Record<string, string> = {
  neutral: 'bg-surface-3 text-ink-2',
  accent: 'bg-accent-soft text-accent-ink',
  good: 'bg-good-soft text-good-ink',
  warning: 'bg-warning-soft text-warning-ink',
  serious: 'bg-serious-soft text-serious-ink',
  critical: 'bg-critical-soft text-critical-ink',
  violet: 'bg-violet-soft text-violet',
}

// ───────────────────────── report pieces ─────────────────────────

function Changes({ report }: { report: Report }) {
  return (
    <Card>
      <CardHeader title="Changes made in company systems" subtitle="Every declared, policy-checked change and what the system answered" icon={KeyRound} />
      <ul className="divide-y divide-line">
        {report.changes.map((c) => (
          <li key={c.seq} className="flex items-start gap-3 px-5 py-3">
            {c.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good-ink" aria-label="succeeded" />
            ) : c.error === 'needs_approval' ? (
              <Hourglass className="mt-0.5 size-4 shrink-0 text-warning-ink" aria-label="held for approval" />
            ) : (
              <XCircle className="mt-0.5 size-4 shrink-0 text-critical-ink" aria-label="failed" />
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm">
                <Mono>{c.action}</Mono>
                {c.facts.amount !== undefined && <span className="ml-2 font-semibold">{rupees(Number(c.facts.amount))}</span>}
              </p>
              <p className="mt-0.5 font-mono text-[11px] text-ink-3">{compactArgs(c.facts)}</p>
              <p className="mt-0.5 text-xs text-ink-2">{c.ok ? c.result : c.error === 'needs_approval' ? 'Held until a supervisor approves; nothing was changed.' : `${humanise(c.error)}: ${c.result}`}</p>
              {c.diff && c.diff.length > 0 && <RecordDiff diff={c.diff} />}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}

const DIFF_PREVIEW = 6

/** How the record read before the change and after it: removed lines, then added ones. */
function RecordDiff({ diff }: { diff: { op: '+' | '-'; text: string }[] }) {
  const [all, setAll] = useState(false)
  const created = diff.every((d) => d.op === '+')
  const shown = all ? diff : diff.slice(0, DIFF_PREVIEW)
  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-line">
      <p className="border-b border-line bg-surface-2 px-2.5 py-1 text-[11px] font-medium text-ink-3">
        {created ? 'New record, as the system shows it' : 'The record before → after'}
      </p>
      <ul className="font-mono text-[11px] leading-5">
        {shown.map((d, i) => (
          <li key={i} className={cx('flex gap-2 px-2.5', d.op === '+' ? 'bg-good-soft text-good-ink' : 'bg-critical-soft text-critical-ink')}>
            <span aria-label={d.op === '+' ? 'added' : 'removed'} className="select-none">{d.op === '+' ? '+' : '−'}</span>
            <span className="min-w-0 break-words whitespace-pre-wrap">{d.text.trim()}</span>
          </li>
        ))}
      </ul>
      {diff.length > DIFF_PREVIEW && (
        <button onClick={() => setAll(!all)} className="w-full border-t border-line px-2.5 py-1 text-left text-[11px] text-accent-ink hover:bg-surface-2">
          {all ? 'Show less' : `Show all ${diff.length} lines`}
        </button>
      )}
    </div>
  )
}

function Evidence({ runId, scope, names }: { runId: string; scope: string | null; names: string[] }) {
  const [open, setOpen] = useState<string | null>(null)
  const images = names.filter((n) => n.endsWith('.png'))
  if (!images.length) return null
  return (
    <Card>
      <CardHeader title="Evidence" subtitle="Screenshots taken by the verifier and at approval requests, plus customer attachments" icon={ImageIcon} />
      <div className="grid grid-cols-2 gap-3 p-5 md:grid-cols-3">
        {images.map((name) => (
          <button key={name} onClick={() => setOpen(name)} className="group overflow-hidden rounded-lg border border-line text-left">
            <img src={api.evidenceUrl(runId, name, scope)} alt={name} loading="lazy" className="aspect-[4/3] w-full bg-surface-2 object-cover object-top transition group-hover:opacity-90" />
            <p className="truncate border-t border-line px-2 py-1.5 font-mono text-[10px] text-ink-3">{name}</p>
          </button>
        ))}
      </div>
      {open && (
        <div role="dialog" aria-modal aria-label={open} className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={() => setOpen(null)}>
          <div className="max-h-full max-w-5xl overflow-auto rounded-xl bg-surface" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-line px-4 py-2">
              <p className="font-mono text-xs">{open}</p>
              <button onClick={() => setOpen(null)} aria-label="Close" className="rounded p-1 hover:bg-surface-3">
                <X className="size-4" />
              </button>
            </div>
            <img src={api.evidenceUrl(runId, open, scope)} alt={open} className="block w-full" />
          </div>
        </div>
      )}
    </Card>
  )
}
