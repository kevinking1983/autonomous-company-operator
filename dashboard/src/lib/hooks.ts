import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

/** GET a path now and every `intervalMs` (0 = once). Keeps showing the last good data while refreshing. */
export function usePoll<T>(path: string | null, intervalMs = 0) {
  // Results are keyed by path, so switching paths shows a loading state without resetting state in an effect.
  const [result, setResult] = useState<{ path: string; data: T | null; error: string | null } | null>(null)
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    if (!path) return
    try {
      const value = await api.get<T>(path)
      if (alive.current) setResult({ path, data: value, error: null })
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      if (alive.current) setResult((previous) => ({ path, data: previous?.path === path ? previous.data : null, error: message }))
    }
  }, [path])

  useEffect(() => {
    alive.current = true
    const first = window.setTimeout(() => void refresh(), 0)
    const timer = intervalMs ? window.setInterval(() => void refresh(), intervalMs) : undefined
    return () => {
      alive.current = false
      window.clearTimeout(first)
      if (timer) window.clearInterval(timer)
    }
  }, [refresh, intervalMs])

  const current = result && result.path === path ? result : null
  return { data: current?.data ?? null, error: current?.error ?? null, loading: Boolean(path) && !current, refresh }
}

/** Subscribe to a run's live Server-Sent Events stream, accumulating events in order. */
export function useRunStream<E extends { seq: number }>(url: string | null) {
  // Keyed by url so a new stream starts empty without resetting state in the effect.
  const [stream, setStream] = useState<{ url: string; events: E[]; ended: boolean } | null>(null)

  useEffect(() => {
    if (!url) return
    const source = new EventSource(url)
    const onAny = (message: MessageEvent) => {
      const event = JSON.parse(message.data) as E
      setStream((previous) => {
        const events = previous?.url === url ? previous.events : []
        if (events.length && events[events.length - 1].seq >= event.seq) return previous
        return { url, events: [...events, event], ended: false }
      })
    }
    const end = () => {
      setStream((previous) => ({ url, events: previous?.url === url ? previous.events : [], ended: true }))
      source.close()
    }
    // Every audit event type arrives as a named SSE event; listen generically.
    const types = [
      'phase', 'contract.created', 'contract.revised', 'plan.created', 'plan.replan', 'decision', 'decision.ignored',
      'tool.call', 'tool.result', 'policy.decision', 'policy.grant', 'guard.allowed', 'guard.blocked', 'adapt.decision',
      'step.done', 'step.failed', 'verify.started', 'verify.page', 'verify.result', 'human.request', 'human.answered',
      'human.decided', 'run.started', 'run.paused', 'run.completed', 'run.escalated', 'run.handover', 'run.failed',
      'browser.login', 'browser.retry', 'browser.uncertain', 'browser.unsaved_changes_lost', 'evidence.saved',
      'llm.call', 'llm.retry', 'llm.cooldown', 'llm.fallback', 'brain.error', 'batch.truncated', 'subtasks.escalated',
      'understand.refused', 'run.finish_requested', 'policy.grant_released',
    ]
    for (const type of types) source.addEventListener(type, onAny as EventListener)
    source.addEventListener('end', end)
    source.onerror = end
    return () => source.close()
  }, [url])

  const current = stream && stream.url === url ? stream : null
  return { events: current?.events ?? [], live: Boolean(url) && !current?.ended }
}
