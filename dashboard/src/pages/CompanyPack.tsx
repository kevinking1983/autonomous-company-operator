import { Ban, BookOpen, Coins, KeyRound, Server, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import Markdown from 'react-markdown'
import { PageHeader } from '../components/Layout'
import { Badge, Card, CardHeader, Mono, Skeleton, Tabs } from '../components/ui'
import { cx } from '../lib/ui-utils'
import { humanise, rupees } from '../lib/format'
import { usePoll } from '../lib/hooks'

interface Pack {
  company: { name: string; description: string; role: { title: string; mission: string; reports_to: string } }
  systems: Record<string, { name: string; base_path: string; purpose: string; holds: string[] }>
  actions: { id: string; system: string; effect: string; description: string; allowed: boolean; requests: { method: string; path: string }[] }[]
  forbidden: { id: string; rule: string }[]
  compensation: { refund_settlement: string; coupon_validity_days: number; issues: Record<string, { remedy: string; amount?: { basis: string; percent: number }; tiers?: { min_minutes_late: number; max_minutes_late: number | null; coupon: number }[]; follow_up: { action: string; target: string; category: string }[] }> }
  approvals: { approvers: string[]; rules: { id: string; description: string; applies_to: string[]; when: { fact: string; op: string; value: unknown } }[] }
  sops: { id: string; title: string; applies_to: string[]; success_criteria: string[]; body: string }[]
}

type Section = 'sops' | 'policy' | 'permissions' | 'systems'

const EFFECT_TONE = { read: 'neutral', write: 'accent', money: 'violet', irreversible: 'serious' } as const
const OPS: Record<string, string> = { gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=', ne: '≠' }

export function CompanyPack() {
  const { data: pack } = usePoll<Pack>('/company-pack')
  const [section, setSection] = useState<Section>('sops')
  const [sop, setSop] = useState(0)
  if (!pack) return <Skeleton className="h-96" />
  const current = pack.sops[sop]

  return (
    <>
      <PageHeader
        title="Company Pack"
        subtitle={`Everything the operator knows about how ${pack.company.name} works, as data, not prompts. Read-only here.`}
        actions={<Tabs value={section} onChange={setSection} tabs={[{ id: 'sops', label: 'Procedures' }, { id: 'policy', label: 'Policy & approvals' }, { id: 'permissions', label: 'Permissions' }, { id: 'systems', label: 'Systems' }]} />}
      />
      <Card className="mb-6 p-5">
        <p className="text-sm"><strong className="font-semibold">{pack.company.role.title}</strong> <span className="text-ink-3">· reports to {pack.company.role.reports_to}</span></p>
        <p className="mt-1 text-sm text-ink-2">{pack.company.role.mission}</p>
      </Card>

      {section === 'sops' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[260px_1fr]">
          <Card className="h-fit">
            <ul className="p-1.5">
              {pack.sops.map((s, i) => (
                <li key={s.id}>
                  <button onClick={() => setSop(i)} className={cx('w-full rounded-lg px-3 py-2 text-left text-sm', i === sop ? 'bg-accent-soft font-medium text-accent-ink' : 'text-ink-2 hover:bg-surface-2')}>
                    {s.title}
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {current && (
            <Card>
              <CardHeader title={current.title} subtitle={<span className="flex flex-wrap gap-1">applies to {current.applies_to.map((a) => <Mono key={a}>{a}</Mono>)}</span>} icon={BookOpen} />
              <div className="grid grid-cols-1 gap-6 p-5 xl:grid-cols-[1fr_300px]">
                <div className="prose-sop"><Markdown>{current.body}</Markdown></div>
                <div>
                  <h3 className="text-xs font-semibold tracking-wide text-ink-3 uppercase">Success criteria</h3>
                  <p className="mt-1 text-xs text-ink-3">Become the task contract the verifier checks</p>
                  <ul className="mt-2 space-y-2">
                    {current.success_criteria.map((c) => (
                      <li key={c} className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-xs text-ink-2">{c}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </Card>
          )}
        </div>
      )}

      {section === 'policy' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader title="Compensation policy" subtitle={`Refunds settle in ${pack.compensation.refund_settlement}`} icon={Coins} />
            <ul className="divide-y divide-line">
              {Object.entries(pack.compensation.issues).map(([issue, p]) => (
                <li key={issue} className="px-5 py-3">
                  <p className="text-sm font-medium">{humanise(issue)}</p>
                  <p className="mt-0.5 text-sm text-ink-2">
                    {p.remedy === 'none' ? 'No money' : p.amount ? `${humanise(p.remedy)} ${p.amount.percent}% of the ${p.amount.basis.replace('_', ' ')}` : humanise(p.remedy)}
                  </p>
                  {p.tiers && (
                    <table className="mt-2 w-full text-xs">
                      <thead><tr className="text-left text-ink-3"><th className="py-1 font-medium">Measured delay</th><th className="py-1 font-medium">Coupon</th></tr></thead>
                      <tbody>
                        {p.tiers.map((t) => (
                          <tr key={t.min_minutes_late} className="border-t border-line">
                            <td className="tabular py-1">{t.max_minutes_late === null ? `${t.min_minutes_late}+ min` : `${t.min_minutes_late}–${t.max_minutes_late} min`}</td>
                            <td className="tabular py-1 font-medium">{t.coupon ? rupees(t.coupon) : 'apology only'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {p.follow_up.length > 0 && <p className="mt-1 text-xs text-ink-3">Then: {p.follow_up.map((f) => `${f.action} against the ${f.target} (${f.category})`).join('; ')}</p>}
                </li>
              ))}
            </ul>
          </Card>
          <Card className="h-fit">
            <CardHeader title="When a supervisor must approve" subtitle={`Approvers: ${pack.approvals.approvers.join(', ')}`} icon={ShieldAlert} />
            <ul className="divide-y divide-line">
              {pack.approvals.rules.map((r) => (
                <li key={r.id} className="px-5 py-3">
                  <p className="flex items-center gap-2 text-sm"><Mono>{r.id}</Mono> {r.description}</p>
                  <p className="mt-1 font-mono text-xs text-ink-3">{r.when.fact} {OPS[r.when.op] ?? r.when.op} {String(r.when.value)} · on {r.applies_to.join(', ')}</p>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      {section === 'permissions' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Actions the operator may declare" subtitle="Each is tied to the exact requests that perform it; the browser blocks anything undeclared" icon={KeyRound} />
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <tbody>
                  {pack.actions.map((a) => (
                    <tr key={a.id} className="border-b border-line last:border-0">
                      <td className="px-5 py-2.5 align-top"><Mono>{a.id}</Mono></td>
                      <td className="px-2 py-2.5 align-top"><Badge tone={a.allowed ? EFFECT_TONE[a.effect as keyof typeof EFFECT_TONE] : 'critical'}>{a.allowed ? a.effect : 'forbidden'}</Badge></td>
                      <td className="px-5 py-2.5 align-top text-xs text-ink-2">
                        {a.description}
                        {a.requests.map((r) => <span key={r.path} className="mt-0.5 block font-mono text-[11px] text-ink-3">{r.method} {r.path}</span>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card className="h-fit">
            <CardHeader title="Never" icon={Ban} />
            <ul className="space-y-2 p-5">
              {pack.forbidden.map((f) => <li key={f.id} className="text-sm text-ink-2">{f.rule}</li>)}
            </ul>
          </Card>
        </div>
      )}

      {section === 'systems' && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {Object.entries(pack.systems).map(([id, s]) => (
            <Card key={id}>
              <CardHeader title={s.name} subtitle={<Mono>{s.base_path}</Mono>} icon={Server} />
              <div className="space-y-2 p-5 text-sm">
                <p className="text-ink-2">{s.purpose}</p>
                <ul className="flex flex-wrap gap-1">{s.holds.map((h) => <Badge key={h}>{h}</Badge>)}</ul>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  )
}
