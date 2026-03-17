import { NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import './App.css'
import {
  fetchAgents,
  fetchCheckpoints,
  fetchCompute,
  fetchEvents,
  fetchFeature,
  fetchFeatures,
  fetchTriage,
  type Checkpoint,
  type EventLog,
  type Feature,
  type FeatureColumn,
  type FeatureDetails,
  type TriageItem,
} from './lib/api'

function Icon({ label }: { label: string }) {
  return (
    <span
      aria-hidden="true"
      style={{
        width: 18,
        height: 18,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        border: '1px solid var(--border)',
        borderRadius: 6,
        fontSize: 11,
        color: 'var(--muted)',
        background: 'var(--bg)',
      }}
    >
      {label}
    </span>
  )
}

function PageTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <div style={{ fontWeight: 650, letterSpacing: '-0.01em' }}>{title}</div>
      {subtitle ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>{subtitle}</div> : null}
    </div>
  )
}

function Placeholder({ title }: { title: string }) {
  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 12,
        background: 'var(--panel)',
        padding: 14,
        maxWidth: 920,
      }}
    >
      <div style={{ fontWeight: 650, marginBottom: 6 }}>{title}</div>
      <div style={{ color: 'var(--muted)', fontSize: 13 }}>
        UI scaffold is live. Next: wire to the local API and implement the Kanban/DAG/tri-pane flows.
      </div>
    </div>
  )
}

const KANBAN_COLUMNS: Array<{ id: FeatureColumn; label: string; help: string }> = [
  { id: 'SCOPING', label: 'Scoping', help: 'Helix parsing / compiling intent' },
  { id: 'ORCHESTRATING', label: 'Orchestrating', help: 'Agents building' },
  { id: 'BLOCKED', label: 'Blocked', help: 'Human needed' },
  { id: 'VERIFYING', label: 'Verifying', help: 'Judges running' },
  { id: 'READY_FOR_REVIEW', label: 'Ready for Review', help: 'Awaiting sign-off' },
]

function formatEta(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  const m = Math.round(seconds / 60)
  return `${m}m`
}

function FeatureCard({ feature }: { feature: Feature }) {
  const navigate = useNavigate()
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => navigate(`/features/${encodeURIComponent(feature.feature_id)}`)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') navigate(`/features/${encodeURIComponent(feature.feature_id)}`)
      }}
      style={{
        border: '1px solid var(--border)',
        borderRadius: 12,
        background: 'var(--panel)',
        padding: 12,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        boxShadow: 'var(--shadow)',
        cursor: 'pointer',
      }}
    >
      <div style={{ fontWeight: 650, letterSpacing: '-0.01em', fontSize: 13 }}>
        {feature.title}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 12 }}>
        <span className="pill">Conf {Math.round(feature.confidence * 100)}%</span>
        <span className="pill">ETA {formatEta(feature.eta_seconds)}</span>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 12 }}>
        <span>Passed: {feature.node_status_counts.PASSED_VERIFICATION ?? 0}</span>
        <span>Failed: {feature.node_status_counts.FAILED ?? 0}</span>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 12 }}>
        <a
          className="pill"
          href={`/features/${encodeURIComponent(feature.feature_id)}`}
          onClick={(e) => {
            e.preventDefault()
            navigate(`/features/${encodeURIComponent(feature.feature_id)}`)
          }}
        >
          View DAG Progress
        </a>
        <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
          View Trace Logs
        </a>
        <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
          Review & Merge
        </a>
      </div>
    </div>
  )
}

function NodePill({ label, tone }: { label: string; tone: 'green' | 'yellow' | 'red' | 'gray' }) {
  const colors: Record<typeof tone, { bg: string; border: string; text: string }> = {
    green: { bg: 'rgba(46, 160, 67, 0.14)', border: 'rgba(46, 160, 67, 0.35)', text: 'rgba(46, 160, 67, 1)' },
    yellow: { bg: 'rgba(187, 128, 9, 0.14)', border: 'rgba(187, 128, 9, 0.35)', text: 'rgba(187, 128, 9, 1)' },
    red: { bg: 'rgba(220, 38, 38, 0.12)', border: 'rgba(220, 38, 38, 0.35)', text: 'rgba(220, 38, 38, 1)' },
    gray: { bg: 'rgba(120, 120, 120, 0.12)', border: 'rgba(120, 120, 120, 0.28)', text: 'var(--muted)' },
  }
  const c = colors[tone]
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 10px',
        borderRadius: 999,
        border: `1px solid ${c.border}`,
        background: c.bg,
        color: c.text,
        fontSize: 12,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  )
}

function formatTs(ts?: number | null): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return String(ts)
  }
}

function extractBriefingFromCheckpoint(cp: Checkpoint | null): string | null {
  if (!cp) return null
  const judge = (cp.tool_logs as any)?.judge
  const justification = judge?.justification
  if (typeof justification === 'string' && justification.trim()) return justification.trim()
  return null
}

function ExtractedDiff({ cp }: { cp: Checkpoint | null }) {
  if (!cp) {
    return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No checkpoint found.</div>
  }
  const pre = cp.pre_state_ref ?? ''
  const post = cp.post_state_ref ?? ''
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
      <div>
        <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Pre</div>
        <pre
          style={{
            margin: 0,
            padding: 10,
            borderRadius: 12,
            border: '1px solid var(--border)',
            background: 'var(--bg)',
            overflow: 'auto',
            maxHeight: 380,
            fontSize: 12,
          }}
        >
          {pre}
        </pre>
      </div>
      <div>
        <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Post</div>
        <pre
          style={{
            margin: 0,
            padding: 10,
            borderRadius: 12,
            border: '1px solid var(--border)',
            background: 'var(--bg)',
            overflow: 'auto',
            maxHeight: 380,
            fontSize: 12,
          }}
        >
          {post}
        </pre>
      </div>
    </div>
  )
}

function FeatureDagPage() {
  const params = useParams()
  const featureId = params.featureId
  const navigate = useNavigate()

  const [data, setData] = useState<FeatureDetails | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!featureId) return
    let cancelled = false

    async function load() {
      try {
        setError(null)
        const d = await fetchFeature(featureId!)
        if (!cancelled) setData(d)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }

    load()
    const t = window.setInterval(load, 2500)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [featureId])

  const nodes = data?.dag.nodes ?? {}
  const stateNodes = data?.state?.nodes ?? {}

  function nodeTone(status?: string | null, verification?: string | null): 'green' | 'yellow' | 'red' | 'gray' {
    if (status === 'PASSED_VERIFICATION') return 'green'
    if (status === 'RUNNING') return 'yellow'
    if (status === 'FAILED' || status === 'ESCALATED' || verification === 'BLOCKED') return 'red'
    if (status === 'PENDING') return 'gray'
    return 'gray'
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <PageTitle
          title={data?.dag.macro_intent ? data.dag.macro_intent : `Feature: ${featureId ?? ''}`}
          subtitle={
            data
              ? `Confidence ${Math.round(data.metrics.confidence * 100)}% · ETA ${formatEta(data.metrics.eta_seconds)}`
              : 'Loading…'
          }
        />
        <a
          className="pill"
          href="/features"
          onClick={(e) => {
            e.preventDefault()
            navigate('/features')
          }}
        >
          Back to Kanban
        </a>
      </div>

      {error ? (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            background: 'var(--panel)',
            padding: 12,
            color: 'var(--muted)',
            fontFamily: 'var(--mono)',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          Failed to load feature.\n\n{error}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 360px', gap: 12, alignItems: 'start' }}>
        <section
          style={{
            border: '1px solid var(--border)',
            borderRadius: 14,
            background: 'var(--panel)',
            padding: 12,
            minHeight: 340,
          }}
        >
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>DAG</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
            {Object.keys(nodes).length === 0 ? (
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>No DAG nodes found.</div>
            ) : null}
            {Object.keys(nodes).map((nodeId) => {
              const s = stateNodes[nodeId]
              const tone = nodeTone(s?.status, s?.verification_status ?? null)
              const retry = s?.attempts ? ` · ${s.attempts}x` : ''
              return (
                <span
                  key={nodeId}
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/features/${encodeURIComponent(featureId ?? '')}/nodes/${encodeURIComponent(nodeId)}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      navigate(`/features/${encodeURIComponent(featureId ?? '')}/nodes/${encodeURIComponent(nodeId)}`)
                    }
                  }}
                  style={{ cursor: 'pointer' }}
                >
                  <NodePill tone={tone} label={`${nodeId}${retry}`} />
                </span>
              )
            })}
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 12 }}>
            Click a node to open Task Intervention.
          </div>
        </section>

        <aside
          style={{
            border: '1px solid var(--border)',
            borderRadius: 14,
            background: 'var(--panel)',
            padding: 12,
          }}
        >
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Feature documentation</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Intent</div>
              <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{data?.dag.macro_intent ?? '—'}</div>
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Architecture (allowed touch)</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                v1 derives from node task target files:
              </div>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12 }}>
                {Object.values(nodes).map((n) => (
                  <li key={n.node_id} style={{ marginBottom: 4 }}>
                    <span style={{ fontFamily: 'var(--mono)' }}>{n.task.target_file}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </aside>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <a
          className="pill"
          href={`/features/${encodeURIComponent(featureId ?? '')}/signoff`}
          onClick={(e) => {
            e.preventDefault()
            navigate(`/features/${encodeURIComponent(featureId ?? '')}/signoff`)
          }}
        >
          Review & Merge (tri-pane)
        </a>
      </div>
    </div>
  )
}

function TaskInterventionPage() {
  const params = useParams()
  const featureId = params.featureId
  const nodeId = params.nodeId
  const navigate = useNavigate()

  const [feature, setFeature] = useState<FeatureDetails | null>(null)
  const [events, setEvents] = useState<EventLog[] | null>(null)
  const [checkpoints, setCheckpoints] = useState<Checkpoint[] | null>(null)
  const [guidance, setGuidance] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!featureId || !nodeId) return
    let cancelled = false

    async function load() {
      try {
        setError(null)
        const f = await fetchFeature(featureId!)
        if (cancelled) return
        setFeature(f)

        const ev = await fetchEvents({ runId: featureId, hypothesisId: nodeId, limit: 5000 })
        if (!cancelled) setEvents(ev)

        const taskId = f.dag.nodes?.[nodeId!]?.task?.task_id
        if (taskId) {
          const cps = await fetchCheckpoints({ task_id: taskId, limit: 50 })
          if (!cancelled) setCheckpoints(cps)
        } else {
          if (!cancelled) setCheckpoints([])
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }

    load()
    const t = window.setInterval(load, 3000)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [featureId, nodeId])

  const task = feature?.dag.nodes?.[nodeId ?? '']?.task
  const latestCp = (checkpoints ?? [])[0] ?? null
  const briefing =
    extractBriefingFromCheckpoint(latestCp) ??
    (error ? null : 'No agent briefing available yet (v1 derives from judge/coder failure logs).')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <PageTitle
          title={`Task Intervention: ${nodeId ?? ''}`}
          subtitle={task ? `${task.task_id} · ${task.target_file}` : 'Loading…'}
        />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a
            className="pill"
            href={`/features/${encodeURIComponent(featureId ?? '')}`}
            onClick={(e) => {
              e.preventDefault()
              navigate(`/features/${encodeURIComponent(featureId ?? '')}`)
            }}
          >
            Back to DAG
          </a>
          <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
            Re-run from checkpoint (stub)
          </a>
          <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
            Abort task (stub)
          </a>
        </div>
      </div>

      {error ? (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            background: 'var(--panel)',
            padding: 12,
            color: 'var(--muted)',
            fontFamily: 'var(--mono)',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          Failed to load intervention context.\n\n{error}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr 360px', gap: 12, alignItems: 'start' }}>
        <section
          style={{
            border: '1px solid var(--border)',
            borderRadius: 14,
            background: 'var(--panel)',
            padding: 12,
            minHeight: 340,
          }}
        >
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Agent briefing</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, whiteSpace: 'pre-wrap' }}>{briefing}</div>
          <div style={{ marginTop: 10, color: 'var(--muted)', fontSize: 12 }}>
            Last checkpoint: {latestCp ? `${latestCp.checkpoint_id} · ${latestCp.status}` : '—'}
          </div>
        </section>

        <section
          style={{
            border: '1px solid var(--border)',
            borderRadius: 14,
            background: 'var(--panel)',
            padding: 12,
            minHeight: 340,
          }}
        >
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Execution logs</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(events ?? []).length === 0 ? (
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{events ? 'No events.' : 'Loading…'}</div>
            ) : null}
            {(events ?? []).slice(-80).map((e, idx) => (
              <div
                key={`${e.timestamp ?? idx}:${idx}`}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 12,
                  background: 'var(--bg)',
                  padding: 10,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <div style={{ fontWeight: 650, fontSize: 12 }}>{e.message ?? 'Event'}</div>
                  <div style={{ color: 'var(--muted)', fontSize: 12 }}>{formatTs(e.timestamp ?? null)}</div>
                </div>
                {e.location ? (
                  <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)' }}>{e.location}</div>
                ) : null}
              </div>
            ))}
          </div>
        </section>

        <section
          style={{
            border: '1px solid var(--border)',
            borderRadius: 14,
            background: 'var(--panel)',
            padding: 12,
            minHeight: 340,
          }}
        >
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Context injector</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 8 }}>
            Add guidance for the next retry (v1: stored locally in UI only).
          </div>
          <textarea
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            placeholder='e.g. "Use the updated test key from .env.local; avoid touching /core/auth."'
            style={{
              width: '100%',
              minHeight: 160,
              resize: 'vertical',
              borderRadius: 12,
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              padding: 10,
              color: 'var(--text)',
              font: 'inherit',
            }}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
              Attach doc link (stub)
            </a>
            <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
              Apply + re-run (stub)
            </a>
          </div>
        </section>
      </div>

      <section
        style={{
          border: '1px solid var(--border)',
          borderRadius: 14,
          background: 'var(--panel)',
          padding: 12,
        }}
      >
        <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Latest diff (checkpoint)</div>
        <ExtractedDiff cp={latestCp} />
      </section>
    </div>
  )
}

function SignoffTripanePage() {
  const params = useParams()
  const featureId = params.featureId
  const navigate = useNavigate()

  const [feature, setFeature] = useState<FeatureDetails | null>(null)
  const [events, setEvents] = useState<EventLog[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!featureId) return
    let cancelled = false

    async function load() {
      try {
        setError(null)
        const f = await fetchFeature(featureId!)
        if (!cancelled) setFeature(f)
        const ev = await fetchEvents({ runId: featureId, limit: 5000 })
        if (!cancelled) setEvents(ev)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }

    load()
    const t = window.setInterval(load, 3000)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [featureId])

  const tasks = Object.values(feature?.dag.nodes ?? {}).map((n) => n.task)
  const acceptance = tasks.map((t) => `- ${t.acceptance_criteria}`).join('\n')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <PageTitle title="Sign-Off" subtitle={feature?.dag.macro_intent ?? (featureId ? `Feature ${featureId}` : '—')} />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a
            className="pill"
            href={`/features/${encodeURIComponent(featureId ?? '')}`}
            onClick={(e) => {
              e.preventDefault()
              navigate(`/features/${encodeURIComponent(featureId ?? '')}`)
            }}
          >
            Back to DAG
          </a>
          <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
            Edit intent (stub)
          </a>
          <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
            View episodic memory (stub)
          </a>
          <a className="pill" href="#" onClick={(e) => e.preventDefault()}>
            Merge to main (stub)
          </a>
        </div>
      </div>

      {error ? (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            background: 'var(--panel)',
            padding: 12,
            color: 'var(--muted)',
            fontFamily: 'var(--mono)',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          Failed to load sign-off view.\n\n{error}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, alignItems: 'start' }}>
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Original intent (Helix)</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, whiteSpace: 'pre-wrap' }}>{feature?.dag.macro_intent ?? '—'}</div>
          <div style={{ fontWeight: 650, fontSize: 13, margin: '14px 0 8px' }}>Acceptance criteria</div>
          <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--muted)' }}>{acceptance || '—'}</pre>
        </section>

        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Semantic trace (Agenti)</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(events ?? []).length === 0 ? (
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{events ? 'No events.' : 'Loading…'}</div>
            ) : null}
            {(events ?? []).slice(-80).map((e, idx) => (
              <div key={`${e.timestamp ?? idx}:${idx}`} style={{ border: '1px solid var(--border)', borderRadius: 12, background: 'var(--bg)', padding: 10 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <div style={{ fontWeight: 650, fontSize: 12 }}>{e.message ?? 'Event'}</div>
                  <div style={{ color: 'var(--muted)', fontSize: 12 }}>{formatTs(e.timestamp ?? null)}</div>
                </div>
                {e.hypothesisId ? (
                  <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)' }}>{e.hypothesisId}</div>
                ) : null}
              </div>
            ))}
          </div>
        </section>

        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Verified execution (Code)</div>
          <div style={{ color: 'var(--muted)', fontSize: 12 }}>
            v1 shows the latest checkpoint diff per task (starting with the most recent checkpoint across all tasks).
          </div>
          <SignoffDiffBlock feature={feature} />
        </section>
      </div>
    </div>
  )
}

function SignoffDiffBlock({ feature }: { feature: FeatureDetails | null }) {
  const [cp, setCp] = useState<Checkpoint | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const taskIds = Object.values(feature?.dag.nodes ?? {}).map((n) => n.task.task_id)
        if (taskIds.length === 0) {
          setCp(null)
          return
        }
        // Grab the newest checkpoint across tasks by fetching each task's latest quickly (v1).
        const all: Checkpoint[] = []
        for (const tid of taskIds.slice(0, 10)) {
          const cps = await fetchCheckpoints({ task_id: tid, limit: 1 })
          if (cps[0]) all.push(cps[0])
        }
        all.sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0))
        if (!cancelled) setCp(all[0] ?? null)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [feature?.feature_id])

  if (error) return <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 10 }}>{error}</div>
  return (
    <div style={{ marginTop: 10 }}>
      <ExtractedDiff cp={cp} />
    </div>
  )
}

function TriageInboxPage() {
  const [items, setItems] = useState<TriageItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        setError(null)
        const res = await fetchTriage()
        if (!cancelled) setItems(res.items)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }

    load()
    const t = window.setInterval(load, 2500)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <PageTitle
        title="Triage Inbox"
        subtitle="Aggregated blockers across all in-flight features (management by exception)"
      />

      {error ? (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            background: 'var(--panel)',
            padding: 12,
            color: 'var(--muted)',
            fontFamily: 'var(--mono)',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          Failed to load triage.\n\n{error}
        </div>
      ) : null}

      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: 14,
          background: 'var(--panel)',
          padding: 8,
        }}
      >
        {(items ?? []).length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 12, padding: 10 }}>
            {items ? 'No blocked items.' : 'Loading…'}
          </div>
        ) : null}
        {(items ?? []).map((it) => (
          <div
            key={`${it.feature_id}:${it.summary}`}
            style={{
              padding: 10,
              borderBottom: '1px solid var(--border)',
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
            }}
          >
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontWeight: 650, fontSize: 13 }}>{it.title}</div>
              <span className="pill">{it.severity}</span>
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 12 }}>{it.summary}</div>
            <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)' }}>{it.dag_id}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function AgentRosterPage() {
  const [res, setRes] = useState<{ agents: unknown[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const r = await fetchAgents()
        if (!cancelled) setRes(r)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    const t = window.setInterval(load, 5000)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <PageTitle title="Agent roster" subtitle="v1 is heuristic; agent identity isn’t emitted consistently yet." />
      {error ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>{error}</div> : null}
      <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
        <div style={{ color: 'var(--muted)', fontSize: 12 }}>
          Agents: {(res?.agents ?? []).length}
        </div>
        <pre style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--muted)' }}>
          {JSON.stringify(res?.agents ?? [], null, 2)}
        </pre>
      </div>
    </div>
  )
}

function ComputePage() {
  const [res, setRes] = useState<{ event_count: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const r = await fetchCompute()
        if (!cancelled) setRes(r)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    const t = window.setInterval(load, 5000)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <PageTitle title="Compute" subtitle="v1 uses event volume as a proxy for burn rate." />
      {error ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>{error}</div> : null}
      <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
        <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 6 }}>Event volume</div>
        <div style={{ color: 'var(--muted)', fontSize: 12 }}>events.jsonl count: {res?.event_count ?? '—'}</div>
      </div>
    </div>
  )
}

function FeaturesKanbanPage() {
  const [features, setFeatures] = useState<Feature[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        setError(null)
        const data = await fetchFeatures()
        if (!cancelled) setFeatures(data)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }

    load()
    const t = window.setInterval(load, 2500)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  const grouped = useMemo(() => {
    const map: Record<string, Feature[]> = {}
    for (const c of KANBAN_COLUMNS) map[c.id] = []
    for (const f of features ?? []) map[f.column]?.push(f)
    return map as Record<FeatureColumn, Feature[]>
  }, [features])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <PageTitle
        title="Features"
        subtitle="System-state Kanban: Scoping → Orchestrating → Blocked → Verifying → Ready for Review"
      />

      {error ? (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            background: 'var(--panel)',
            padding: 12,
            color: 'var(--muted)',
            fontFamily: 'var(--mono)',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          Failed to load features from API. Is the Phase 4 server running?\n\n{error}
        </div>
      ) : null}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(5, minmax(220px, 1fr))',
          gap: 12,
          alignItems: 'start',
          minWidth: 1100,
        }}
      >
        {KANBAN_COLUMNS.map((col) => (
          <section
            key={col.id}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 14,
              background: 'var(--panel)',
              padding: 10,
              minHeight: 360,
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '6px 6px 10px' }}>
              <div style={{ fontWeight: 650, fontSize: 13 }}>{col.label}</div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{col.help}</div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 6 }}>
              {(grouped[col.id] ?? []).map((f) => (
                <FeatureCard key={f.feature_id} feature={f} />
              ))}
              {features && (grouped[col.id] ?? []).length === 0 ? (
                <div style={{ color: 'var(--muted)', fontSize: 12, padding: 6 }}>No items</div>
              ) : null}
              {!features && !error ? (
                <div style={{ color: 'var(--muted)', fontSize: 12, padding: 6 }}>Loading…</div>
              ) : null}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}

function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebarHeader">
        <Icon label="AH" />
        <div className="sidebarHeaderTitle">Agenti-Helix</div>
      </div>

      <div className="navSectionLabel">Control plane</div>
      <NavLink
        to="/"
        end
        className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}
      >
        <Icon label="D" />
        Dashboard
      </NavLink>
      <NavLink
        to="/features"
        className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}
      >
        <Icon label="K" />
        Features
      </NavLink>
      <NavLink
        to="/triage"
        className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}
      >
        <Icon label="T" />
        Triage Inbox
      </NavLink>
      <NavLink
        to="/agents"
        className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}
      >
        <Icon label="A" />
        Agent Roster
      </NavLink>
      <NavLink
        to="/compute"
        className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}
      >
        <Icon label="C" />
        Compute
      </NavLink>
      <NavLink
        to="/repo"
        className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}
      >
        <Icon label="R" />
        Repository Context
      </NavLink>
      <NavLink
        to="/settings"
        className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}
      >
        <Icon label="S" />
        Settings
      </NavLink>
    </aside>
  )
}

function Topbar() {
  const navigate = useNavigate()
  const location = useLocation()
  const [q, setQ] = useState('')

  const hint = useMemo(() => {
    if (location.pathname.startsWith('/features')) return 'Search features, DAGs, tasks...'
    if (location.pathname.startsWith('/triage')) return 'Search triage items...'
    return 'Search...'
  }, [location.pathname])

  useEffect(() => {
    setQ('')
  }, [location.pathname])

  return (
    <header className="topbar">
      <div className="search" role="search">
        <Icon label="⌘" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={hint}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const trimmed = q.trim()
              if (trimmed) navigate(`/features?q=${encodeURIComponent(trimmed)}`)
            }
          }}
        />
      </div>
      <div className="topbarRight">
        <span className="pill">Burn: —</span>
        <span className="pill">Profile</span>
      </div>
    </header>
  )
}

function App() {
  return (
    <div className="appShell">
      <Sidebar />
      <div className="main">
        <Topbar />
        <main className="content">
          <Routes>
            <Route
              path="/"
              element={<Placeholder title="Dashboard" />}
            />
            <Route
              path="/features"
              element={
                <FeaturesKanbanPage />
              }
            />
            <Route path="/features/:featureId" element={<FeatureDagPage />} />
            <Route path="/features/:featureId/nodes/:nodeId" element={<TaskInterventionPage />} />
            <Route path="/features/:featureId/signoff" element={<SignoffTripanePage />} />
            <Route path="/triage" element={<TriageInboxPage />} />
            <Route path="/agents" element={<AgentRosterPage />} />
            <Route path="/compute" element={<ComputePage />} />
            <Route path="/repo" element={<Placeholder title="Repository Context" />} />
            <Route path="/settings" element={<Placeholder title="Settings" />} />
            <Route path="*" element={<Placeholder title="Not found" />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default App
