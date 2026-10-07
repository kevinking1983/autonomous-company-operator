import { Bot, BookOpen, Brain, FlaskConical, History, Inbox, LayoutDashboard, ListTodo, Moon, Sun } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import type { Overview } from '../lib/api'
import { usePoll } from '../lib/hooks'
import { cx } from '../lib/ui-utils'

type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function Layout() {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  const { data: overview, error } = usePoll<Overview>('/overview', 5000)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('theme', theme)
    } catch {
      /* storage unavailable */
    }
  }, [theme])

  const waiting = overview?.waiting_for_people ?? 0
  const running = overview?.tasks.running ?? 0

  const items = (
    <>
      <Item to="/" icon={LayoutDashboard} label="Command centre" end />
      <Item to="/tasks" icon={ListTodo} label="Tasks" badge={running ? <Count tone="accent">{running} running</Count> : null} />
      <Item to="/runs" icon={History} label="Runs" />
      <Item to="/inbox" icon={Inbox} label="Approval inbox" badge={waiting ? <Count tone="warning">{waiting}</Count> : null} />
      <Item to="/reliability" icon={FlaskConical} label="Reliability lab" />
      <Item to="/memory" icon={Brain} label="Memory" />
      <Item to="/company" icon={BookOpen} label="Company Pack" />
    </>
  )
  const themeLabel = theme === 'dark' ? 'Light theme' : 'Dark theme'
  const ThemeIcon = theme === 'dark' ? Sun : Moon
  const toggleTheme = () => setTheme(theme === 'dark' ? 'light' : 'dark')

  return (
    <div className="flex min-h-screen max-md:flex-col">
      {/* The column stretches with the page; the sidebar inside it stays in view while scrolling. */}
      <div className="w-60 shrink-0 border-r border-line bg-surface max-md:hidden">
        <aside className="sticky top-0 flex h-screen flex-col px-3 py-4">
          <Brand />
          <nav className="flex flex-col gap-0.5" aria-label="Main">
            {items}
          </nav>
          <div className="mt-auto space-y-3 px-2">
            <div className="flex items-center gap-2 text-xs text-ink-3">
              <span className={cx('size-2 rounded-full', error ? 'bg-critical' : 'bg-good')} aria-hidden />
              {error ? 'Operator API unreachable' : 'Operator API online'}
            </div>
            <button
              onClick={toggleTheme}
              className="flex w-full items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-2 hover:bg-surface-2"
            >
              <ThemeIcon className="size-3.5" aria-hidden />
              {themeLabel}
            </button>
          </div>
        </aside>
      </div>
      <header className="sticky top-0 z-20 border-b border-line bg-surface md:hidden">
        <div className="flex items-center justify-between px-4 pt-3">
          <Brand />
          <button onClick={toggleTheme} aria-label={themeLabel} className="grid size-9 place-items-center rounded-lg border border-line text-ink-2">
            <ThemeIcon className="size-4" aria-hidden />
          </button>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-2 [&>a]:shrink-0 [&>a]:whitespace-nowrap" aria-label="Main">
          {items}
        </nav>
      </header>
      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-7xl px-4 py-6 md:px-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2 pb-5 max-md:px-0 max-md:pb-2">
      <span className="grid size-9 place-items-center rounded-xl bg-accent text-white">
        <Bot className="size-5" aria-hidden />
      </span>
      <div>
        <p className="text-sm font-semibold leading-tight">Company Operator</p>
        <p className="text-xs text-ink-3">QuickBite · Support Ops</p>
      </div>
    </div>
  )
}

function Item({ to, icon: Icon, label, badge, end }: { to: string; icon: typeof Bot; label: string; badge?: ReactNode; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cx(
          'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition',
          isActive ? 'bg-accent-soft font-medium text-accent-ink' : 'text-ink-2 hover:bg-surface-2 hover:text-ink',
        )
      }
    >
      <Icon className="size-4" aria-hidden />
      <span className="flex-1">{label}</span>
      {badge}
    </NavLink>
  )
}

function Count({ children, tone }: { children: ReactNode; tone: 'accent' | 'warning' }) {
  return (
    <span className={cx('rounded-full px-1.5 py-0.5 text-[10px] font-semibold', tone === 'warning' ? 'bg-warning-soft text-warning-ink' : 'bg-accent-soft text-accent-ink')}>
      {children}
    </span>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-3">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}
