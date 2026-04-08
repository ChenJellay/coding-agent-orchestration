import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_BASE_URL, fetchEvents, type EventLog } from './lib/api'

type LlmTracePanelProps = {
  collapsed: boolean
  onToggleCollapsed: () => void
}

function isLlmTraceEvent(e: EventLog): boolean {
  const d = e.data
  return Boolean(d && typeof d === 'object' && (d as { kind?: string }).kind === 'llm_trace')
}

function formatTs(ts: number | undefined): string {
  if (ts == null || !Number.isFinite(ts)) return '—'
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return '—'
  }
}

function eventKey(e: EventLog): string {
  const id = (e as { id?: string }).id
  if (typeof id === 'string' && id) return id
  const agent = (e.data as { agent_id?: string } | undefined)?.agent_id ?? ''
  return `${e.timestamp ?? 0}:${e.runId ?? ''}:${e.message ?? ''}:${agent}`
}

function TraceCopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 900)
    } catch {
      /* ignore */
    }
  }, [text])
  return (
    <button type="button" className="llmTraceCopy" onClick={() => void onCopy()}>
      {copied ? 'Copied' : label}
    </button>
  )
}

function Section({
  title,
  body,
  warning,
  copyLabel,
}: {
  title: string
  body: string
  warning?: string
  copyLabel: string
}) {
  return (
    <section className="llmTraceSection">
      <div className="llmTraceSectionHead">
        <span className="llmTraceSectionTitle">{title}</span>
        {warning ? <span className="llmTraceWarn">{warning}</span> : null}
        <TraceCopyButton text={body} label={copyLabel} />
      </div>
      <pre className="llmTracePre">{body || '—'}</pre>
    </section>
  )
}

export function LlmTracePanel({ collapsed, onToggleCollapsed }: LlmTracePanelProps) {
  const [events, setEvents] = useState<EventLog[]>([])
  const [pollError, setPollError] = useState<string | null>(null)
  const [streamStatus, setStreamStatus] = useState<'connecting' | 'open' | 'closed' | 'fallback'>('connecting')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let es: EventSource | null = null
    let pollId: number | null = null

    async function pollOnce() {
      try {
        const batch = await fetchEvents({ limit: 800 })
        if (!cancelled) {
          setPollError(null)
          setEvents(batch)
        }
      } catch (e) {
        if (!cancelled) setPollError(e instanceof Error ? e.message : String(e))
      }
    }

    function startPollingFallback() {
      setStreamStatus('fallback')
      void pollOnce()
      pollId = window.setInterval(() => void pollOnce(), 2800)
    }

    try {
      // Keep initial state as "connecting"; avoid setState directly in effect body.
      es = new EventSource(`${API_BASE_URL}/api/events/stream`)
      es.onopen = () => {
        if (cancelled) return
        setPollError(null)
        setStreamStatus('open')
      }
      es.onerror = () => {
        if (cancelled) return
        setStreamStatus('closed')
        try {
          es?.close()
        } catch {
          /* ignore */
        }
        es = null
        // If streaming fails (proxy, CORS, server not updated), fall back to polling.
        startPollingFallback()
      }
      es.addEventListener('event', (ev) => {
        if (cancelled) return
        const data = (ev as MessageEvent).data
        if (typeof data !== 'string' || !data) return
        try {
          const parsed = JSON.parse(data) as EventLog
          setEvents((prev) => {
            const next = [...prev, parsed]
            // Keep memory bounded; LLM panel only needs recent history.
            return next.length > 1400 ? next.slice(-1400) : next
          })
        } catch (e) {
          setPollError(e instanceof Error ? e.message : String(e))
        }
      })

      // Also do a one-time fetch so the panel isn't empty until first stream tick.
      void pollOnce()
    } catch {
      // If EventSource construction fails synchronously, fall back to polling.
      startPollingFallback()
    }

    return () => {
      cancelled = true
      if (pollId != null) window.clearInterval(pollId)
      try {
        es?.close()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const traces = useMemo(() => {
    const list = events.filter(isLlmTraceEvent)
    const seen = new Set<string>()
    const dedup: EventLog[] = []
    for (let i = list.length - 1; i >= 0; i--) {
      const e = list[i]!
      const key = eventKey(e)
      if (seen.has(key)) continue
      seen.add(key)
      dedup.push(e)
    }
    dedup.reverse()
    return dedup
  }, [events])

  const latestKey = useMemo(() => {
    const e = traces[traces.length - 1]
    if (!e) return null
    return eventKey(e)
  }, [traces])

  const selectedKeyValid = useMemo(() => {
    if (!selectedKey) return null
    return traces.some((e) => eventKey(e) === selectedKey) ? selectedKey : null
  }, [traces, selectedKey])

  const activeKey = selectedKeyValid ?? latestKey

  const selected = useMemo(() => traces.find((e) => eventKey(e) === activeKey), [traces, activeKey])

  type TraceData = {
    agent_id?: string
    prompt?: string
    prompt_truncated?: boolean
    raw_output?: string
    raw_output_truncated?: boolean
    parsed_output_json?: string | null
    parsed_output_truncated?: boolean
    error?: string
  }

  const data = (selected?.data ?? {}) as TraceData

  if (collapsed) {
    return (
      <aside className="llmTraceRail" aria-label="LLM I/O trace panel collapsed">
        <button type="button" className="llmTraceRailBtn" onClick={onToggleCollapsed} title="Show LLM I/O">
          <span className="llmTraceRailLabel">LLM</span>
        </button>
      </aside>
    )
  }

  return (
    <aside className="llmTracePanel" aria-label="LLM input and output trace">
      <header className="llmTraceHeader">
        <div>
          <div className="llmTraceHeaderTitle">LLM I/O</div>
          <div className="llmTraceHeaderSub">Full rendered prompt and model output (from event log).</div>
        </div>
        <button type="button" className="llmTraceCollapseBtn" onClick={onToggleCollapsed} title="Collapse panel">
          Hide
        </button>
      </header>

      {pollError ? <div className="llmTraceError">{pollError}</div> : null}
      {streamStatus !== 'open' ? (
        <div className="llmTraceError">
          {streamStatus === 'connecting'
            ? 'Connecting to live stream…'
            : streamStatus === 'fallback'
              ? 'Live stream unavailable — using polling.'
              : 'Live stream disconnected.'}
        </div>
      ) : null}

      <div className="llmTracePicker">
        <label className="llmTracePickerLabel" htmlFor="llm-trace-select">
          Recent call
        </label>
        <select
          id="llm-trace-select"
          className="llmTraceSelect"
          value={activeKey ?? ''}
          onChange={(ev) => setSelectedKey(ev.target.value || null)}
        >
          {[...traces].reverse().map((e) => {
            const key = eventKey(e)
            const agent = (e.data as { agent_id?: string })?.agent_id ?? 'agent'
            const line = `${formatTs(e.timestamp)} · ${agent}`
            return (
              <option key={key} value={key}>
                {line}
              </option>
            )
          })}
        </select>
      </div>

      {selected ? (
        <div className="llmTraceBody">
          <div className="llmTraceMeta">
            <div>
              <span className="llmTraceMetaMuted">Agent</span> {data.agent_id ?? '—'}
            </div>
            <div>
              <span className="llmTraceMetaMuted">Run</span> {selected.runId ?? '—'}
            </div>
            <div>
              <span className="llmTraceMetaMuted">Hypothesis</span> {selected.hypothesisId ?? '—'}
            </div>
            <div>
              <span className="llmTraceMetaMuted">When</span> {formatTs(selected.timestamp)}
            </div>
            <div className="llmTraceMetaWide">
              <span className="llmTraceMetaMuted">Location</span> {selected.location ?? '—'}
            </div>
            {data.error ? (
              <div className="llmTraceMetaError">
                <span className="llmTraceMetaMuted">Parse / validation</span> {data.error}
              </div>
            ) : null}
          </div>

          <Section
            title="Rendered prompt (full)"
            body={data.prompt ?? ''}
            warning={data.prompt_truncated ? 'Truncated in log' : undefined}
            copyLabel="Copy prompt"
          />
          <Section
            title="Raw model output"
            body={data.raw_output ?? ''}
            warning={data.raw_output_truncated ? 'Truncated in log' : undefined}
            copyLabel="Copy raw"
          />
          <Section
            title="Parsed JSON (validated)"
            body={
              data.parsed_output_json ??
              (data.error ? '(not available — see Parse / validation above)' : '')
            }
            warning={data.parsed_output_truncated ? 'Truncated in log' : undefined}
            copyLabel="Copy JSON"
          />
        </div>
      ) : (
        <div className="llmTraceEmpty">No LLM traces yet. Run a DAG or agent with tracing enabled on the API host.</div>
      )}
    </aside>
  )
}
