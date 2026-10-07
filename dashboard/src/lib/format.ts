export function rupees(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return '₹' + value.toLocaleString('en-IN', { minimumFractionDigits: value % 1 ? 2 : 0, maximumFractionDigits: 2 })
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function humanise(id: string | null | undefined): string {
  if (!id) return ''
  return id.replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function compactArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .filter(([key]) => key !== 'ref' && !key.startsWith('_'))
    .map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(' · ')
}

export function noteOf(output: string): string {
  const note = output.split('\n').find((line) => line.startsWith('Note: '))
  return note ? note.slice(6) : output.split('\n')[0]
}

export const PRIORITY_LABEL: Record<number, string> = { 0: 'Low', 1: 'Normal', 2: 'High', 3: 'Urgent' }

/** A share as a whole percentage, or a dash when there is nothing to measure yet. */
export function percent(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`
}

/** Minutes as "45s", "12 min" or "1 h 5 min". */
export function duration(minutes: number): string {
  if (minutes < 1) return `${Math.round(minutes * 60)}s`
  if (minutes < 60) return `${Math.round(minutes)} min`
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return m ? `${h} h ${m} min` : `${h} h`
}
