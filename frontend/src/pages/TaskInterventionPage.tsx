import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  abortTask,
  applyAndRerun,
  fetchCheckpoints,
  fetchEvents,
  fetchFeature,
  rerunTask,
  type Checkpoint,
  type EventLog,
  type EventsResult,
  type FeatureDetails,
} from '../lib/api'
import { ErrorBox, ExtractedDiff, extractBriefingFromCheckpoint, formatTs, PageTitle } from '../components/shared'

export function TaskInterventionPage() {
  const params = useParams()
  const featureId = params.featureId
  const nodeId = params.nodeId
  const navigate = useNavigate()

  const [feature, setFeature] = useState<FeatureDetails | null>(null)
  const [events, setEvents] = useState<EventLog[] | null>(null)
  const [checkpoints, setCheckpoints] = useState<Checkpoint[] | null>(null)
  const [guidance, setGuidance] = useState('')
  const [docUrl, setDocUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionNote, setActionNote] = useState<string | null>(null)
  const [refreshTick, setRefreshTick] = useState(0)
  const lastEventAt = useRef<number>(Date.now())
  const [isStalled, setIsStalled] = useState(false)

  useEffect(() => {
    if (!featureId || !nodeId) return
    let cancelled = false

    async function load() {
      try {
        setError(null)
        const f = await fetchFeature(featureId!)
        if (cancelled) return
        setFeature(f)

        const taskId = f.dag.nodes?.[nodeId!]?.task?.task_id
        // Show both node-level orchestration events (runId=featureId, hypothesisId=nodeId)
        // and the underlying execution events emitted by the verification loop (runId=taskId).
        const [evNodeResult, evTaskResult] = await Promise.all([
          fetchEvents({ runId: featureId, hypothesisId: nodeId, limit: 2000 }),
          taskId ? fetchEvents({ runId: taskId, limit: 5000 }) : Promise.resolve<EventsResult>({ items: [], fileSize: null }),
        ])
        if (!cancelled) {
          const merged = [...(evNodeResult.items ?? []), ...(evTaskResult.items ?? [])].sort(
            (a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0),
          )
          setEvents(merged)
          if (merged.length > 0) lastEventAt.current = Date.now()
          // Stall: RUNNING for > 30 s with no new events
          const nodeStatus = feature?.state?.nodes?.[nodeId!]?.status
          setIsStalled(nodeStatus === 'RUNNING' && Date.now() - lastEventAt.current > 30_000)
        }

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
  }, [featureId, nodeId, refreshTick])

  const task = feature?.dag.nodes?.[nodeId ?? '']?.task
  const latestCp = (checkpoints ?? [])[0] ?? null
  const briefing =
    extractBriefingFromCheckpoint(latestCp) ??
    (error ? null : 'No agent briefing available yet (v1 derives from judge/coder failure logs).')

  async function handleRerunFromCheckpoint() {
    if (!featureId || !nodeId) return
    if (!task) {
      setError('Task not loaded yet.')
      return
    }
    if (!latestCp) {
      setError('No checkpoint found to rerun from.')
      return
    }
    setError(null)
    setActionNote(null)
    setBusy(true)
    try {
      const res = await rerunTask({
        task_id: task.task_id,
        checkpoint_id: latestCp.checkpoint_id,
        guidance: guidance.trim() || undefined,
        feature_id: featureId,
        node_id: nodeId,
      })
      setActionNote(`Re-run scheduled${res.reRunId ? `: ${res.reRunId}` : '.'}`)
      setRefreshTick((t) => t + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleAbortTask() {
    if (!featureId || !nodeId) return
    if (!task) {
      setError('Task not loaded yet.')
      return
    }
    setError(null)
    setActionNote(null)
    setBusy(true)
    try {
      await abortTask({
        task_id: task.task_id,
        feature_id: featureId,
        node_id: nodeId,
        abort_reason: 'Aborted by user',
      })
      setActionNote('Abort requested.')
      setRefreshTick((t) => t + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleApplyAndRerun() {
    if (!featureId || !nodeId) return
    if (!task) {
      setError('Task not loaded yet.')
      return
    }
    if (!latestCp) {
      setError('No checkpoint found to apply + rerun.')
      return
    }
    setError(null)
    setActionNote(null)
    setBusy(true)
    try {
      await applyAndRerun({
        task_id: task.task_id,
        checkpoint_id: latestCp.checkpoint_id,
        guidance: guidance.trim() || undefined,
        doc_url: docUrl.trim() || undefined,
        feature_id: featureId,
        node_id: nodeId,
      })
      setActionNote('Apply + re-run scheduled.')
      setRefreshTick((t) => t + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <PageTitle title={`Task Intervention: ${nodeId ?? ''}`} subtitle={task ? `${task.task_id} · ${task.target_file}` : 'Loading…'} />
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
          <a
            className="pill"
            href="#"
            onClick={(e) => {
              e.preventDefault()
              if (busy) return
              void handleRerunFromCheckpoint()
            }}
          >
            {busy ? 'Rerun in progress…' : 'Re-run from checkpoint'}
          </a>
          <a
            className="pill"
            href="#"
            onClick={(e) => {
              e.preventDefault()
              if (busy) return
              void handleAbortTask()
            }}
          >
            {busy ? 'Aborting…' : 'Abort task'}
          </a>
        </div>
      </div>

      {error ? <ErrorBox title="Failed to load intervention context" error={error} /> : null}
      {isStalled ? (
        <div
          role="alert"
          style={{
            border: '1px solid #f59e0b',
            borderRadius: 12,
            background: '#fffbeb',
            padding: '10px 14px',
            color: '#92400e',
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span style={{ fontSize: 16 }}>⚠</span>
          <span>
            <strong>Agent may be stalled</strong> — no new events received in the last 30 s while status is RUNNING.
            The model inference may have hung. You can abort and rerun, or wait longer.
          </span>
        </div>
      ) : null}
      {actionNote ? (
        <div style={{ border: '1px solid var(--border)', borderRadius: 12, background: 'var(--panel)', padding: 10, color: 'var(--muted)', fontSize: 12 }}>
          {actionNote}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr 360px', gap: 12, alignItems: 'start' }}>
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12, minHeight: 340 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Agent briefing</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, whiteSpace: 'pre-wrap' }}>{briefing}</div>
          <div style={{ marginTop: 10, color: 'var(--muted)', fontSize: 12 }}>
            Last checkpoint: {latestCp ? `${latestCp.checkpoint_id} · ${latestCp.status}` : '—'}
          </div>
        </section>

        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12, minHeight: 340 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Execution logs</div>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              maxHeight: 520,
              overflow: 'auto',
              paddingRight: 2,
            }}
          >
            {(events ?? []).length === 0 ? (
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{events ? 'No events.' : 'Loading…'}</div>
            ) : null}
            {(events ?? []).slice(-80).map((e, idx) => (
              <div key={`${e.timestamp ?? idx}:${idx}`} style={{ border: '1px solid var(--border)', borderRadius: 12, background: 'var(--bg)', padding: 10 }}>
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

        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12, minHeight: 340 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Context injector</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 8 }}>
            Add guidance for the next retry, and optionally attach a doc link (persisted server-side).
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
          <div style={{ marginTop: 10, color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Doc link (optional)</div>
          <input
            value={docUrl}
            onChange={(e) => setDocUrl(e.target.value)}
            placeholder="https://example.com/spec-or-prd"
            style={{
              width: '100%',
              borderRadius: 12,
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              padding: 10,
              color: 'var(--text)',
              font: 'inherit',
            }}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <a
              className="pill"
              href="#"
              onClick={(e) => {
                e.preventDefault()
                if (busy) return
                void handleApplyAndRerun()
              }}
            >
              Apply + re-run
            </a>
          </div>
        </section>
      </div>

      <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
        <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Latest diff (checkpoint)</div>
        <ExtractedDiff cp={latestCp} />
      </section>
    </div>
  )
}
