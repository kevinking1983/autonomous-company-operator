import { AlertTriangle, CheckCircle2, ExternalLink, FileText, Hourglass, Inbox as InboxIcon, MessageSquare, Search, Split, ThumbsDown, ThumbsUp, UserRoundCheck } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/Layout'
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, Mono, Skeleton, Tabs } from '../components/ui'
import { cx } from '../lib/ui-utils'
import { api, type HumanRequest, type RequestContext } from '../lib/api'
import { humanise, rupees, timeAgo } from '../lib/format'
import { usePoll } from '../lib/hooks'

const KIND = {
  approval: { label: 'Approval', icon: UserRoundCheck },
  clarification: { label: 'Question', icon: MessageSquare },
  external_reply: { label: 'Waiting for customer', icon: Hourglass },
  subtasks: { label: 'Waiting for sub-tasks', icon: Split },
} as const

export function Inbox() {
  const [view, setView] = useState<'pending' | 'decided'>('pending')
  const { data: requests, refresh } = usePoll<HumanRequest[]>(view === 'pending' ? '/requests?status=pending' : '/requests?status=', 3000)
  const list = (requests ?? []).filter((r) => (view === 'pending' ? true : r.status !== 'pending')).reverse()
  const [selected, setSelected] = useState<string | null>(null)
  const current = list.find((r) => r.id === selected) ?? list.find((r) => r.kind === 'approval') ?? list[0]

  return (
    <>
      <PageHeader
        title="Approval inbox"
        subtitle="Decisions only a person may make. Check the operator's claims against what it actually saw."
        actions={<Tabs value={view} onChange={setView} tabs={[{ id: 'pending', label: 'Waiting for you' }, { id: 'decided', label: 'History' }]} />}
      />
      {!requests ? (
        <Skeleton className="h-96" />
      ) : list.length === 0 ? (
        <Card>
          <Empty icon={InboxIcon} title={view === 'pending' ? 'Nothing is waiting for you' : 'No decisions yet'}>
            When a refund or coupon goes beyond policy, or the operator has a question, it lands here.
          </Empty>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
          <Card className="h-fit">
            <ul className="divide-y divide-line">
              {list.map((r) => {
                const k = KIND[r.kind]
                return (
                  <li key={r.id}>
                    <button onClick={() => setSelected(r.id)} className={cx('flex w-full gap-3 px-4 py-3 text-left hover:bg-surface-2', current?.id === r.id && 'bg-accent-soft hover:bg-accent-soft')}>
                      <k.icon className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs font-semibold">{r.id}</span>
                          <span className="text-[11px] text-ink-3">{timeAgo(r.created_at)}</span>
                        </div>
                        <p className="mt-0.5 text-sm font-medium">{r.action ? humanise(r.action.split('.')[1]) : k.label}{r.facts.amount !== undefined && <span className="ml-1.5">{rupees(Number(r.facts.amount))}</span>}</p>
                        <p className="line-clamp-2 text-xs text-ink-3">{r.question}</p>
                        {r.status !== 'pending' && <DecisionBadge request={r} />}
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          </Card>
          {current && <RequestDetail key={current.id} id={current.id} onDecided={() => void refresh()} />}
        </div>
      )}
    </>
  )
}

function DecisionBadge({ request }: { request: HumanRequest }) {
  if (request.status === 'approved') return <Badge tone="good" icon={ThumbsUp} className="mt-1">Approved by {request.responder}</Badge>
  if (request.status === 'rejected') return <Badge tone="critical" icon={ThumbsDown} className="mt-1">Rejected by {request.responder}</Badge>
  return <Badge tone="accent" icon={CheckCircle2} className="mt-1">Answered</Badge>
}

function RequestDetail({ id, onDecided }: { id: string; onDecided: () => void }) {
  const { data, error, refresh } = usePoll<RequestContext>(`/requests/${id}/context`)
  if (error) return <ErrorNote message={error} />
  if (!data) return <Skeleton className="h-96" />
  const r = data.request
  const k = KIND[r.kind]
  const approvalShot = data.evidence.find((n) => n === `approval-${r.id}.png`)
  const amount = r.facts.amount !== undefined ? Number(r.facts.amount) : null

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <k.icon className="size-4" aria-hidden /> {k.label} <Mono>{r.id}</Mono>
            </span>
          }
          subtitle={`Asked ${timeAgo(r.created_at)}${data.run ? ` · run ${data.run.run_id}` : ''}`}
          action={data.run && <Link to={`/runs/${data.run.run_id}`} className="inline-flex items-center gap-1 text-xs text-accent-ink hover:underline">Open the run <ExternalLink className="size-3" /></Link>}
        />
        <div className="grid grid-cols-1 gap-6 p-5 md:grid-cols-2">
          {r.action && (
            <div>
              <h3 className="text-xs font-semibold tracking-wide text-ink-3 uppercase">The operator wants to</h3>
              <p className="mt-2 flex items-baseline gap-2">
                <Mono>{r.action}</Mono>
                {amount !== null && <span className="text-2xl font-semibold tracking-tight">{rupees(amount)}</span>}
                {typeof r.context?.requested_amount === 'number' && (
                  <span className="text-sm text-ink-3">
                    approved instead of <s>{rupees(r.context.requested_amount)}</s>
                  </span>
                )}
              </p>
              <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {Object.entries(r.facts).filter(([key]) => key !== 'amount').map(([key, value]) => (
                  <div key={key} className="contents">
                    <dt className="text-ink-3">{humanise(key)}</dt>
                    <dd className="font-mono text-xs leading-5">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
          {r.rules.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold tracking-wide text-ink-3 uppercase">Why it needs you</h3>
              <ul className="mt-2 space-y-2">
                {r.rules.map((rule, i) => (
                  <li key={rule} className="flex gap-2 text-sm">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning-ink" aria-hidden />
                    <span>
                      <Mono>{rule}</Mono> <span className="text-ink-2">{r.reasons[i]}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <div className="border-t border-line px-5 py-4">
          <h3 className="text-xs font-semibold tracking-wide text-ink-3 uppercase">{r.kind === 'approval' ? "The operator's justification" : 'Details'}</h3>
          <blockquote className="mt-2 border-l-2 border-line-strong pl-3 text-sm text-ink-2">{r.question}</blockquote>
          {r.kind === 'approval' && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-warning-ink">
              <Search className="size-3.5" aria-hidden /> This is the operator's own account. Check it against the pages it saw below before deciding.
            </p>
          )}
        </div>
      </Card>

      <EvidencePanel data={data} shot={approvalShot} />

      {r.status === 'pending' ? (
        r.kind === 'approval' ? (
          <DecisionForm id={r.id} requested={amount} onDone={() => { void refresh(); onDecided() }} />
        ) : r.kind === 'clarification' ? (
          <AnswerForm id={r.id} onDone={() => { void refresh(); onDecided() }} />
        ) : (
          <Card className="p-5 text-sm text-ink-2">Nothing to decide: the run resumes on its own when {r.kind === 'subtasks' ? 'its sub-tasks finish' : 'the customer replies on the ticket'}.</Card>
        )
      ) : (
        <Card className="p-5">
          <DecisionBadge request={r} />
          {r.response && <p className="mt-2 text-sm text-ink-2">“{r.response}”</p>}
        </Card>
      )}
    </div>
  )
}

function EvidencePanel({ data, shot }: { data: RequestContext; shot?: string }) {
  const [page, setPage] = useState(0)
  const [query, setQuery] = useState('')
  const current = data.pages[page]
  const lines = useMemo(() => (current ? current.text.split('\n').filter((l) => !/^(URL|Title|HTTP status|Note):/.test(l)) : []), [current])
  if (!data.pages.length && !shot) return null
  const q = query.trim().toLowerCase()
  return (
    <Card>
      <CardHeader title="What the operator saw" subtitle="The pages it read, exactly as it read them, and a screenshot taken when it asked" icon={FileText} />
      <div className="grid grid-cols-1 gap-5 p-5 xl:grid-cols-2">
        {shot && data.run && (
          <a href={api.evidenceUrl(data.run.run_id, shot)} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-lg border border-line">
            <img src={api.evidenceUrl(data.run.run_id, shot)} alt="Screenshot taken when approval was requested" className="w-full object-cover object-top" />
            <p className="border-t border-line px-2 py-1.5 font-mono text-[10px] text-ink-3">{shot}</p>
          </a>
        )}
        {current && (
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <select value={page} onChange={(e) => setPage(Number(e.target.value))} className="h-8 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-2 text-xs" aria-label="Page">
                {data.pages.map((p, i) => (
                  <option key={p.url} value={i}>{p.title}</option>
                ))}
              </select>
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find, e.g. PACKED" className="h-8 w-36 rounded-lg border border-line-strong bg-surface px-2 text-xs" aria-label="Find in page" />
            </div>
            <pre className="mt-2 max-h-[420px] overflow-auto rounded-lg border border-line bg-surface-2 p-3 font-mono text-[11px] leading-relaxed text-ink-2">
              {lines.map((line, i) => (
                <div key={i} className={cx(q && line.toLowerCase().includes(q) && 'bg-warning-soft text-ink')}>{line.replace(/\s*\[e\d+[^\]]*\]/g, '')}</div>
              ))}
            </pre>
          </div>
        )}
      </div>
    </Card>
  )
}

function DecisionForm({ id, requested, onDone }: { id: string; requested: number | null; onDone: () => void }) {
  const [approver, setApprover] = useState('priya.supervisor')
  const [lower, setLower] = useState('')
  const [note, setNote] = useState('')
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const lowerValue = Number(lower)
  const lowerInvalid = lower.trim() !== '' && requested !== null && !(lowerValue > 0 && lowerValue <= requested)

  async function decide(approved: boolean) {
    setBusy(true)
    setError(null)
    try {
      const amount = approved && lower.trim() ? Number(lower) : undefined
      await api.post(`/requests/${id}/decision`, { approved, approver, note, remember, amount })
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Your decision" subtitle="Approving unlocks exactly this action with exactly these facts, once" />
      <div className="space-y-3 p-5">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[200px_1fr]">
          <div>
            <label htmlFor="approver" className="text-xs font-medium text-ink-2">Approver</label>
            <input id="approver" value={approver} onChange={(e) => setApprover(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm" />
          </div>
          <div>
            <label htmlFor="note" className="text-xs font-medium text-ink-2">Reason (required to reject)</label>
            <textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm" placeholder="e.g. Do not refund repeat claimants when the packing log shows the item was packed." />
          </div>
        </div>
        {requested !== null && (
          <div className="rounded-lg border border-line bg-surface-2 p-3">
            <label htmlFor="lower" className="text-xs font-medium text-ink-2">Approve a lower amount (optional)</label>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="text-sm text-ink-3">₹</span>
              <input
                id="lower"
                type="number"
                inputMode="decimal"
                min={0.01}
                max={requested}
                step={0.01}
                value={lower}
                onChange={(e) => setLower(e.target.value)}
                placeholder={requested.toFixed(2)}
                className="h-9 w-36 rounded-lg border border-line-strong bg-surface px-3 font-mono text-sm"
              />
              <span className="text-xs text-ink-3">
                {lowerInvalid ? `Must be above ₹0 and at most ${rupees(requested)}.` : 'You can reduce the amount, never increase it. The operator is told and updates its reply.'}
              </span>
            </div>
          </div>
        )}
        <label className="flex items-start gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="mt-0.5 size-4 accent-[var(--accent)]" />
          <span>
            <strong className="font-medium text-ink">Teach the operator</strong>: keep this reason as a company rule it follows in every future run.
          </span>
        </label>
        {error && <ErrorNote message={error} />}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" disabled={busy || !note.trim()} onClick={() => void decide(false)}>
            <ThumbsDown className="size-4" aria-hidden /> Reject
          </Button>
          <Button variant="good" disabled={busy || !approver.trim() || lowerInvalid} onClick={() => void decide(true)}>
            <ThumbsUp className="size-4" aria-hidden /> {lower.trim() && !lowerInvalid && requested !== null && lowerValue < requested ? `Approve ${rupees(lowerValue)}` : 'Approve'}
          </Button>
        </div>
      </div>
    </Card>
  )
}

function AnswerForm({ id, onDone }: { id: string; onDone: () => void }) {
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState<string | null>(null)
  async function send() {
    try {
      await api.post(`/requests/${id}/answer`, { answer, responder: 'priya.supervisor' })
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <Card>
      <CardHeader title="Your answer" />
      <div className="space-y-3 p-5">
        <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={3} className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm" aria-label="Answer" />
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end">
          <Button disabled={!answer.trim()} onClick={() => void send()}>Send answer</Button>
        </div>
      </div>
    </Card>
  )
}
