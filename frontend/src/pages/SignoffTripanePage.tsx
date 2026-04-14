import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  applyNodeSignoff,
  editDagIntent,
  fetchCheckpoints,
  fetchEvents,
  fetchFeature,
  fetchMemory,
  mergeTask,
  type Checkpoint,
  type EventLog,
  type FeatureDetails,
  type MemoryResponse,
} from '../lib/api'
import { ErrorBox, ExtractedDiff, formatTs, PageTitle } from '../components/shared'

function SignoffDiffBlock({
  feature,
  onLatestCheckpoint,
}: {
  feature: FeatureDetails | null
  onLatestCheckpoint?: (cp: Checkpoint | null) => void
}) {
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
        const all: Checkpoint[] = []
        for (const tid of taskIds.slice(0, 10)) {
          const cps = await fetchCheckpoints({ task_id: tid, limit: 1 })
          if (cps[0]) all.push(cps[0])
        }
        all.sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0))
        const chosen = all[0] ?? null
        if (!cancelled) {
          setCp(chosen)
          onLatestCheckpoint?.(chosen)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [feature?.feature_id, feature?.dag.nodes, onLatestCheckpoint])

  if (error) return <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 10 }}>{error}</div>
  return (
    <div style={{ marginTop: 10 }}>
      <ExtractedDiff cp={cp} />
    </div>
  )
}

export function SignoffTripanePage() {
  const params = useParams()
  const featureId = params.featureId
  const navigate = useNavigate()

  const [feature, setFeature] = useState<FeatureDetails | null>(null)
  const [events, setEvents] = useState<EventLog[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [macroIntentDraft, setMacroIntentDraft] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [showMemory, setShowMemory] = useState(false)
  const [memory, setMemory] = useState<MemoryResponse | null>(null)
  const [memoryError, setMemoryError] = useState<string | null>(null)
  const [mergeCandidate, setMergeCandidate] = useState<Checkpoint | null>(null)
  const [mergeResult, setMergeResult] = useState<{ mergeRef?: string; sha?: string; simulated?: boolean } | null>(null)

  useEffect(() => {
    if (!featureId) return
    let cancelled = false

    async function load() {
      try {
        setError(null)
        const f = await fetchFeature(featureId!)
        if (!cancelled) setFeature(f)
        const evResult = await fetchEvents({ runId: featureId, limit: 5000 })
        if (!cancelled) setEvents(evResult.items)
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

  useEffect(() => {
    if (!feature || editMode) return
    setMacroIntentDraft(feature.dag.macro_intent ?? '')
  }, [feature, editMode])

  async function handleEditIntent() {
    if (!featureId) return
    setActionError(null)
    setEditMode(true)
    setMacroIntentDraft(feature?.dag.macro_intent ?? '')
  }

  async function handleSaveIntent() {
    if (!featureId) return
    if (!macroIntentDraft.trim()) {
      setActionError('macro_intent cannot be empty.')
      return
    }
    setActionError(null)
    setBusy(true)
    try {
      await editDagIntent(featureId, { macro_intent: macroIntentDraft })
      setEditMode(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleViewMemory() {
    if (!featureId) return
    setMemoryError(null)
    setMemory(null)
    setShowMemory(true)
    setBusy(true)
    try {
      const res = await fetchMemory({ runId: featureId })
      setMemory(res)
    } catch (e) {
      setMemoryError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleMergeToMain() {
    if (!mergeCandidate) {
      setActionError('No verified checkpoint available yet for merge.')
      return
    }
    setActionError(null)
    setMergeResult(null)
    setBusy(true)
    try {
      // The patch pipeline produces PASSED_PENDING_SIGNOFF checkpoints.
      // signoff-apply must be called first to materialise the staged file on disk
      // and transition the checkpoint to PASSED before the merge endpoint will accept it.
      if (mergeCandidate.status === 'PASSED_PENDING_SIGNOFF') {
        // task_id format is "{dag_id}:{node_id}" e.g. "header-color-update:N1"
        const colonIdx = mergeCandidate.task_id.lastIndexOf(':')
        const dag_id = colonIdx > 0 ? mergeCandidate.task_id.slice(0, colonIdx) : mergeCandidate.task_id
        const node_id = colonIdx > 0 ? mergeCandidate.task_id.slice(colonIdx + 1) : 'N1'
        await applyNodeSignoff({
          dag_id,
          node_id,
          task_id: mergeCandidate.task_id,
          checkpoint_id: mergeCandidate.checkpoint_id,
          signed_by: 'ui-user',
        })
      }
      const res = await mergeTask({
        task_id: mergeCandidate.task_id,
        checkpoint_id: mergeCandidate.checkpoint_id,
        target_branch: 'main',
        commit_message: `feat(agenti): ${feature?.dag.macro_intent?.slice(0, 72) ?? mergeCandidate.task_id}`,
      })
      setMergeResult(res)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

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
          <a
            className="pill"
            href="#"
            onClick={(e) => {
              e.preventDefault()
              if (busy) return
              void handleEditIntent()
            }}
          >
            Edit intent
          </a>
          <a
            className="pill"
            href="#"
            onClick={(e) => {
              e.preventDefault()
              if (busy) return
              void handleViewMemory()
            }}
          >
            View episodic memory
          </a>
          <a
            className="pill"
            href="#"
            onClick={(e) => {
              e.preventDefault()
              if (busy) return
              void handleMergeToMain()
            }}
            style={{
              borderColor: 'rgba(220, 38, 38, 0.55)',
              color: 'rgba(220, 38, 38, 1)',
              background: 'rgba(220, 38, 38, 0.10)',
            }}
          >
            Merge to main
          </a>
        </div>
      </div>

      {error ? <ErrorBox title="Failed to load sign-off view" error={error} /> : null}
      {actionError ? <ErrorBox title="Action failed" error={actionError} /> : null}
      {mergeResult ? (
        <div style={{
          margin: '0 0 4px',
          padding: '10px 14px',
          borderRadius: 12,
          border: '1px solid rgba(46, 160, 67, 0.4)',
          background: 'rgba(46, 160, 67, 0.08)',
          fontSize: 12,
          color: 'var(--text)',
          display: 'flex',
          gap: 10,
          alignItems: 'center',
        }}>
          <span style={{ color: 'rgba(46, 160, 67, 1)', fontWeight: 700 }}>✓ Merged</span>
          {mergeResult.simulated
            ? <span style={{ color: 'var(--muted)' }}>Simulated merge (set AGENTI_HELIX_GIT_COMMIT_ENABLED=true for real git commits)</span>
            : <span>Real git commit: <code style={{ fontFamily: 'var(--mono)' }}>{mergeResult.sha?.slice(0, 8)}</code></span>}
          {mergeResult.mergeRef ? <span style={{ color: 'var(--muted)', fontFamily: 'var(--mono)', fontSize: 11 }}>{mergeResult.mergeRef}</span> : null}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, alignItems: 'start' }}>
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Original intent (Helix)</div>
          {editMode ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <textarea
                value={macroIntentDraft}
                onChange={(e) => setMacroIntentDraft(e.target.value)}
                style={{
                  width: '100%',
                  minHeight: 140,
                  resize: 'vertical',
                  borderRadius: 12,
                  border: '1px solid var(--border)',
                  background: 'var(--bg)',
                  padding: 10,
                  color: 'var(--text)',
                  font: 'inherit',
                  fontFamily: 'var(--mono)',
                  fontSize: 12,
                  whiteSpace: 'pre-wrap',
                }}
              />
              {actionError ? (
                <ErrorBox error={actionError} />
              ) : null}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <a
                  className="pill"
                  href="#"
                  onClick={(e) => {
                    e.preventDefault()
                    void handleSaveIntent()
                  }}
                >
                  Save
                </a>
                <a
                  className="pill"
                  href="#"
                  onClick={(e) => {
                    e.preventDefault()
                    setEditMode(false)
                    setActionError(null)
                    setMacroIntentDraft(feature?.dag.macro_intent ?? '')
                  }}
                >
                  Cancel
                </a>
              </div>
            </div>
          ) : (
            <div style={{ color: 'var(--muted)', fontSize: 12, whiteSpace: 'pre-wrap' }}>{feature?.dag.macro_intent ?? '—'}</div>
          )}
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
                {e.hypothesisId ? <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)' }}>{e.hypothesisId}</div> : null}
              </div>
            ))}
          </div>
        </section>

        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Verified execution (Code)</div>
          <div style={{ color: 'var(--muted)', fontSize: 12 }}>
            v1 shows the latest checkpoint diff per task (starting with the most recent checkpoint across all tasks).
          </div>
          <SignoffDiffBlock feature={feature} onLatestCheckpoint={setMergeCandidate} />
        </section>
      </div>

      {showMemory ? (
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Episodic memory</div>
          {memoryError ? (
            <ErrorBox error={memoryError} />
          ) : null}
          {memory ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ color: 'var(--muted)', fontSize: 12, whiteSpace: 'pre-wrap' }}>{memory.summary}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {(memory.items ?? []).slice(-10).map((it, idx) => (
                  <div key={`${it.timestamp ?? idx}:${idx}`} style={{ border: '1px solid var(--border)', borderRadius: 12, background: 'var(--bg)', padding: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                      <div style={{ fontWeight: 650, fontSize: 12 }}>{it.message ?? 'Event'}</div>
                      <div style={{ color: 'var(--muted)', fontSize: 12 }}>{formatTs(it.timestamp ?? null)}</div>
                    </div>
                    {it.location ? <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)', marginTop: 4 }}>{it.location}</div> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ color: 'var(--muted)', fontSize: 12 }}>{'Loading…'}</div>
          )}
        </section>
      ) : null}
    </div>
  )
}
