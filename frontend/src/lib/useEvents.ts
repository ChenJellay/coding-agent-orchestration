import { useEffect, useState } from 'react'
import { API_BASE_URL } from './api'

export interface EventTickFilter {
  /** Filter SSE stream by DAG (a.k.a. feature) id. */
  dagId?: string | null
  /** Filter SSE stream by run id (often the same as dag id, but separate per re-runs). */
  runId?: string | null
}

/**
 * Subscribe to `/api/events/stream` and return a counter that increments every
 * time a new event matching the optional filter arrives. Components use the
 * counter as a `useEffect` dependency to refetch their data without polling.
 *
 * Why a counter instead of the event payload?
 * - Components already have their own `fetchFeature` / `fetchTriage` calls and
 *   only need a "something changed, reload me" signal.
 * - One SSE connection per page replaces 2.5–5 s `setInterval` polls and brings
 *   submit-to-visible latency down from ~5 s to <500 ms.
 *
 * Falls back to a slow poll if `EventSource` fails (proxy strip, CORS, etc.).
 */
export function useEventTick(filter?: EventTickFilter, fallbackPollMs = 10000): number {
  const [tick, setTick] = useState(0)
  const dagId = filter?.dagId ?? null
  const runId = filter?.runId ?? null

  useEffect(() => {
    let cancelled = false
    let es: EventSource | null = null
    let pollId: number | null = null

    function startPollingFallback() {
      if (pollId !== null) return
      pollId = window.setInterval(() => {
        if (!cancelled) setTick((t) => t + 1)
      }, fallbackPollMs)
    }

    const qs = new URLSearchParams()
    if (dagId) qs.set('dagId', dagId)
    if (runId) qs.set('runId', runId)
    // Start the cursor at "now" so the initial connection doesn't replay every
    // historical event in events.jsonl (which would cause N immediate tick++).
    qs.set('sinceTs', String(Math.floor(Date.now() / 1000)))
    const url = `${API_BASE_URL}/api/events/stream?${qs.toString()}`

    try {
      es = new EventSource(url)
      es.addEventListener('event', () => {
        if (!cancelled) setTick((t) => t + 1)
      })
      es.onerror = () => {
        if (cancelled) return
        es?.close()
        es = null
        startPollingFallback()
      }
    } catch {
      startPollingFallback()
    }

    return () => {
      cancelled = true
      es?.close()
      if (pollId !== null) window.clearInterval(pollId)
    }
  }, [dagId, runId, fallbackPollMs])

  return tick
}
