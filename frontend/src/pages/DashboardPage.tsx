import { useEffect, useMemo, useState } from 'react'
import {
  fetchCompute,
  fetchFeatures,
  fetchTriage,
  startDagFromDashboard,
  type Feature,
  type FeatureColumn,
  type PipelineMode,
  type TriageItem,
} from '../lib/api'
import { ErrorBox, FeatureCard, PageTitle } from '../components/shared'

export function DashboardPage() {
  const REPO_PRESETS = useMemo(
    () => [
      { label: 'demo-repo', value: '../demo-repo' },
      { label: 'workspace root', value: '..' },
    ],
    [],
  )

  const [features, setFeatures] = useState<Feature[] | null>(null)
  const [triage, setTriage] = useState<TriageItem[] | null>(null)
  const [compute, setCompute] = useState<{ event_count: number } | null>(null)

  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [pipelineMode, setPipelineMode] = useState<PipelineMode | 'orchestrator'>('patch')

  const [repoPath, setRepoPath] = useState<string>(REPO_PRESETS[0]?.value ?? '../demo-repo')
  const [macroIntent, setMacroIntent] = useState<string>('')
  const [runError, setRunError] = useState<string | null>(null)
  const [runBusy, setRunBusy] = useState(false)
  const [queuedDagId, setQueuedDagId] = useState<string | null>(null)

  async function loadAll() {
    setBusy(true)
    try {
      setError(null)
      const [f, t, c] = await Promise.all([fetchFeatures(), fetchTriage(), fetchCompute()])
      setFeatures(f)
      setTriage(t.items)
      setCompute(c)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    void (async () => {
      if (cancelled) return
      await loadAll()
    })()
    const t = window.setInterval(() => {
      if (cancelled) return
      void loadAll()
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  const trustScore = useMemo(() => {
    if (!features || features.length === 0) return null
    const sum = features.reduce((acc, f) => acc + (f.confidence ?? 0), 0)
    return sum / features.length
  }, [features])

  const counts = useMemo(() => {
    const base: Record<FeatureColumn, number> = {
      SCOPING: 0,
      ORCHESTRATING: 0,
      BLOCKED: 0,
      VERIFYING: 0,
      READY_FOR_REVIEW: 0,
    }
    for (const f of features ?? []) base[f.column] = (base[f.column] ?? 0) + 1
    return base
  }, [features])

  const topFeatures = useMemo(() => {
    return [...(features ?? [])].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0)).slice(0, 6)
  }, [features])

  const blockedItems = useMemo(() => {
    return [...(triage ?? [])].slice(0, 10)
  }, [triage])

  function severityTone(sev: TriageItem['severity']): { border: string; text: string; bg: string } {
    if (sev === 'HIGH') return { border: 'rgba(220, 38, 38, 0.35)', text: 'rgba(220, 38, 38, 1)', bg: 'rgba(220, 38, 38, 0.12)' }
    if (sev === 'MEDIUM') return { border: 'rgba(187, 128, 9, 0.35)', text: 'rgba(187, 128, 9, 1)', bg: 'rgba(187, 128, 9, 0.12)' }
    return { border: 'rgba(46, 160, 67, 0.35)', text: 'rgba(46, 160, 67, 1)', bg: 'rgba(46, 160, 67, 0.12)' }
  }

  async function handleRunCommand() {
    const trimmedRepo = repoPath.trim()
    const trimmedIntent = macroIntent.trim()
    setRunError(null)
    setQueuedDagId(null)

    if (!trimmedRepo) {
      setRunError('repo_path is required.')
      return
    }
    if (!trimmedIntent) {
      setRunError('Command / macro intent is required.')
      return
    }
    setRunBusy(true)
    try {
      const useLlm = import.meta.env.VITE_INTENT_USE_LLM === 'true'
      // "orchestrator" lets the LLM assign pipeline_mode per node; requires use_llm=true.
      const resolvedPipeline: PipelineMode | null =
        pipelineMode === 'orchestrator' ? null : pipelineMode
      const res = await startDagFromDashboard({
        repo_path: trimmedRepo,
        macro_intent: trimmedIntent,
        use_llm: useLlm || pipelineMode === 'orchestrator',
        pipeline_mode: resolvedPipeline,
      })
      setQueuedDagId(res.dag_id)
      // Immediately refresh UI; execution may take a while.
      await loadAll()
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <section
        style={{
          border: '1px solid var(--border)',
          borderRadius: 14,
          background: 'var(--panel)',
          padding: 14,
          margin: '-16px -16px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <PageTitle
            title="Trust Dashboard"
            subtitle="Run command → compile DAG → route coder/judge chains"
          />
          {queuedDagId ? <span className="pill">Queued DAG: {queuedDagId}</span> : null}
        </div>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: '1 1 280px', minWidth: 240 }}>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Local repository</div>
            <input
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              list="repo-presets"
              placeholder="e.g. ../demo-repo"
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
            <datalist id="repo-presets">
              {REPO_PRESETS.map((p) => (
                <option key={p.value} value={p.value} label={p.label} />
              ))}
            </datalist>
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }} />
        </div>

        <div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Command / macro intent</div>
          <textarea
            value={macroIntent}
            onChange={(e) => setMacroIntent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey)) return
              e.preventDefault()
              if (!runBusy) void handleRunCommand()
            }}
            placeholder='e.g. "Update header button background to green and keep accessibility."'
            style={{
              width: '100%',
              minHeight: 120,
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
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginTop: 10 }}>
            <button
              type="button"
              onClick={() => {
                void handleRunCommand()
              }}
              disabled={runBusy}
              style={{
                borderRadius: 999,
                border: '1px solid var(--primary)',
                background: runBusy ? 'var(--bg-muted)' : 'var(--primary-bg)',
                color: 'var(--primary)',
                padding: '10px 18px',
                fontSize: 13,
                fontWeight: 600,
                cursor: runBusy ? 'default' : 'pointer',
              }}
            >
              {runBusy ? 'Scheduling…' : 'Submit command'}
            </button>
            <span style={{ color: 'var(--muted)', fontSize: 12 }}>⌘/Ctrl+Enter in the box also runs.</span>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 12, alignItems: 'start' }}>
          <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--bg)', padding: 12 }}>
            <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 8 }}>Execution pipeline</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(
                [
                  {
                    id: 'patch' as const,
                    label: 'Quick patch',
                    agents: 'coder_patch_v1 → judge_v1',
                    desc: 'Fast single-file line-patch. Best for cosmetic changes, small bug fixes, config tweaks.',
                  },
                  {
                    id: 'build' as const,
                    label: 'Full TDD build',
                    agents: 'librarian → sdet → coder_builder → governor → judge_evaluator',
                    desc: 'Full test-driven pipeline with context discovery, test coverage, and security audit. Best for new features and multi-file changes.',
                  },
                  {
                    id: 'orchestrator' as const,
                    label: 'Orchestrator decides',
                    agents: 'intent_compiler_v1 assigns pipeline per node',
                    desc: 'LLM orchestrator analyses each subtask and picks "patch" or "build" per node. Requires LLM inference.',
                  },
                ] as { id: PipelineMode | 'orchestrator'; label: string; agents: string; desc: string }[]
              ).map((opt) => (
                <label
                  key={opt.id}
                  style={{
                    display: 'flex',
                    gap: 10,
                    alignItems: 'flex-start',
                    cursor: 'pointer',
                    padding: '8px 10px',
                    borderRadius: 10,
                    border: `1px solid ${pipelineMode === opt.id ? 'var(--primary)' : 'var(--border)'}`,
                    background: pipelineMode === opt.id ? 'var(--primary-bg)' : 'transparent',
                  }}
                >
                  <input
                    type="radio"
                    name="pipeline_mode"
                    checked={pipelineMode === opt.id}
                    onChange={() => setPipelineMode(opt.id)}
                    style={{ marginTop: 2 }}
                  />
                  <div>
                    <div style={{ fontWeight: 650, fontSize: 12 }}>{opt.label}</div>
                    <div style={{ color: 'var(--primary)', fontSize: 11, fontFamily: 'var(--mono)', marginTop: 1 }}>
                      {opt.agents}
                    </div>
                    <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>{opt.desc}</div>
                  </div>
                </label>
              ))}
            </div>
          </section>
        </div>
      </section>

      {runError ? <ErrorBox title="Run failed" error={runError} /> : null}
      {error ? <ErrorBox title="Dashboard load failed" error={error} /> : null}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 650, letterSpacing: '-0.01em' }}>Overview</div>
        <a
          className="pill"
          href="#"
          onClick={(e) => {
            e.preventDefault()
            if (busy) return
            void loadAll()
          }}
          style={{ cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.7 : 1 }}
        >
          {busy ? 'Refreshing…' : 'Refresh'}
        </a>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
        <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Trust score</div>
          <div style={{ fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em' }}>
            {trustScore == null ? '—' : `${Math.round(trustScore * 100)}%`}
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>Avg confidence across in-flight DAGs</div>
        </div>
        <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>In-flight DAGs</div>
          <div style={{ fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em' }}>{features?.length ?? '—'}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>Persisted DAG specs</div>
        </div>
        <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Blocked</div>
          <div style={{ fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em' }}>{counts.BLOCKED}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>Needs human intervention</div>
        </div>
        <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Verifying</div>
          <div style={{ fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em' }}>{counts.VERIFYING}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>Judges running</div>
        </div>
        <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Ready for review</div>
          <div style={{ fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em' }}>{counts.READY_FOR_REVIEW}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>Awaiting sign-off</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12, alignItems: 'start' }}>
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
            <div style={{ fontWeight: 650, fontSize: 13 }}>DAG progress (top confidence)</div>
            <div style={{ color: 'var(--muted)', fontSize: 12 }}>Compute burn proxy: {compute?.event_count ?? '—'} events</div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12, marginTop: 12 }}>
            {(topFeatures.length === 0 && features) ? (
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>No in-flight features found.</div>
            ) : null}
            {topFeatures.map((f) => (
              <FeatureCard key={f.feature_id} feature={f} />
            ))}
            {!features ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>Loading…</div> : null}
          </div>

          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 12 }}>
            Trust dashboard summarizes Helix DAG state transitions; use "Features" for detailed DAG progress and task intervention.
          </div>
        </section>

        <aside style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline' }}>
              <div style={{ fontWeight: 650, fontSize: 13 }}>Management by exception</div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>Triage inbox</div>
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 6 }}>
              Blocked items aggregated across all in-flight features.
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
              {triage == null ? (
                <div style={{ color: 'var(--muted)', fontSize: 12 }}>Loading…</div>
              ) : blockedItems.length === 0 ? (
                <div style={{ color: 'var(--muted)', fontSize: 12 }}>No blocked items.</div>
              ) : (
                blockedItems.map((it) => {
                  const t = severityTone(it.severity)
                  return (
                    <div
                      key={`${it.feature_id}:${it.dag_id}:${it.summary}`}
                      style={{ border: '1px solid var(--border)', borderRadius: 12, background: 'var(--bg)', padding: 10 }}
                    >
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
                        <div style={{ fontWeight: 650, fontSize: 12 }}>{it.title}</div>
                        <span
                          className="pill"
                          style={{ borderColor: t.border, background: t.bg, color: t.text }}
                        >
                          {it.severity}
                        </span>
                      </div>
                      <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 6 }}>{it.summary}</div>
                      <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)', marginTop: 6 }}>{it.dag_id}</div>
                    </div>
                  )
                })
              )}
            </div>

            <div style={{ marginTop: 12 }}>
              <a className="pill" href="/features">
                Open Features Kanban
              </a>
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}
