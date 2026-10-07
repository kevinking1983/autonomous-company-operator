import { useEffect, useState } from 'react'

type Health = { status: string; version: string; company_pack: string }

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setHealth)
      .catch(() => setError(true))
  }, [])

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
      <div className="text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Autonomous Company Operator</h1>
        <p className="mt-2 text-slate-400">Dashboard scaffold. The full UI is built in step 10.</p>
        <p className="mt-6 text-sm">
          {health && (
            <span className="text-emerald-400">
              Operator API online · v{health.version} · company pack: {health.company_pack}
            </span>
          )}
          {error && <span className="text-amber-400">Operator API not reachable. Run `make api`.</span>}
          {!health && !error && <span className="text-slate-500">Checking operator API…</span>}
        </p>
      </div>
    </main>
  )
}
