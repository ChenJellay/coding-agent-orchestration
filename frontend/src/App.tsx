import { NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import './App.css'
import { LlmTracePanel } from './LlmTracePanel'
import {
  fetchAgentDetail,
  fetchAgents,
  fetchCheckpoints,
  fetchCompute,
  fetchHealth,
  fetchRepoMap,
  fetchRules,
  applyAndRerun,
  fetchEvents,
  fetchFeature,
  fetchFeatures,
  editDagIntent,
  fetchTriage,
  deleteFeature,
  type AgentDetail,
  type AgentSummary,
  type HealthResponse,
  type Checkpoint,
  type EventLog,
  type Feature,
  type FeatureColumn,
  type FeatureDetails,
  type TriageItem,
  type RepoMapResponse,
  type RulesResponse,
  updateAgentPrompt,
  mergeTask,
  applyNodeSignoff,
  resumeDag,
  startDagFromDashboard,
  type PipelineMode,
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
      <div style={{ color: 'var(--muted)', fontSize: 13 }}>Page not found.</div>
      <div style={{ marginTop: 10 }}>
        <a className="pill" href="/">
          Go to Dashboard
        </a>
      </div>
    </div>
  )
}

function ErrorBox({ title, error }: { title?: string; error: string }) {
  return (
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
      {title ? `${title}\n\n` : null}
      {error}
    </div>
  )
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1100)
    } catch {
      // Best-effort; clipboard might be blocked depending on browser context.
    }
  }
  return (
    <button
      type="button"
      onClick={() => {
        void handleCopy()
      }}
      style={{
        borderRadius: 999,
        border: '1px solid var(--border)',
        background: 'var(--bg)',
        padding: '6px 12px',
        fontSize: 12,
        cursor: 'pointer',
      }}
    >
      {copied ? 'Copied' : label}
    </button>
  )
}

function DashboardPage() {
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
  const [docUploadName, setDocUploadName] = useState<string | null>(null)
  const [docUploadText, setDocUploadText] = useState<string | null>(null)
  const [docUrlField, setDocUrlField] = useState<string>('')
  const docFileInputRef = useRef<HTMLInputElement | null>(null)
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
      SUCCESSFUL_COMMIT: 0,
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

  async function handleRunCommand() {
    const trimmedRepo = repoPath.trim()
    const trimmedIntent = macroIntent.trim()
    setRunError(null)
    setQueuedDagId(null)

    const _DOC_MAX = 400_000
    if (docUploadText != null && docUploadText.length > _DOC_MAX) {
      setRunError(`Documentation file is too large (max ${_DOC_MAX} characters).`)
      return
    }

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
      // #region agent log
      fetch('http://127.0.0.1:7320/ingest/69fc216a-c981-4ea3-a323-547dec11fac3', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'f6751c' },
        body: JSON.stringify({
          sessionId: 'f6751c',
          location: 'App.tsx:handleRunCommand',
          message: 'submit_start',
          hypothesisId: 'H1',
          data: {
            pipelineMode,
            intentLen: trimmedIntent.length,
            hasDocUpload: Boolean(docUploadText && docUploadText.length > 0),
            docUrlLen: docUrlField.trim().length,
          },
          timestamp: Date.now(),
        }),
      }).catch(() => {})
      // #endregion
      const resolvedPipeline: PipelineMode | null =
        pipelineMode === 'orchestrator' ? null : pipelineMode
      const res = await startDagFromDashboard({
        repo_path: trimmedRepo,
        macro_intent: trimmedIntent,
        pipeline_mode: resolvedPipeline,
        ...(docUploadText != null && docUploadText !== ''
          ? { doc_text: docUploadText, doc_filename: docUploadName ?? undefined }
          : docUrlField.trim() !== ''
            ? { doc_url: docUrlField.trim() }
            : {}),
      })
      // #region agent log
      fetch('http://127.0.0.1:7320/ingest/69fc216a-c981-4ea3-a323-547dec11fac3', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'f6751c' },
        body: JSON.stringify({
          sessionId: 'f6751c',
          location: 'App.tsx:handleRunCommand',
          message: 'submit_ok',
          hypothesisId: 'H1',
          data: { dagId: res.dag_id, pipelineMode },
          timestamp: Date.now(),
        }),
      }).catch(() => {})
      // #endregion
      setQueuedDagId(res.dag_id)
      // Immediately refresh UI; execution may take a while.
      await loadAll()
    } catch (e) {
      // #region agent log
      fetch('http://127.0.0.1:7320/ingest/69fc216a-c981-4ea3-a323-547dec11fac3', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'f6751c' },
        body: JSON.stringify({
          sessionId: 'f6751c',
          location: 'App.tsx:handleRunCommand',
          message: 'submit_error',
          hypothesisId: 'H1',
          data: { err: e instanceof Error ? e.message : String(e), pipelineMode },
          timestamp: Date.now(),
        }),
      }).catch(() => {})
      // #endregion
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
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
              gap: 12,
              alignItems: 'stretch',
            }}
          >
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
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
                borderRadius: 12,
                border: '1px solid var(--border)',
                background: 'var(--bg)',
                padding: 10,
                minHeight: 120,
              }}
            >
              <div style={{ fontWeight: 650, fontSize: 12 }}>Documentation (optional)</div>
              <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.35 }}>
                For Product engineering (doc-first), attach a PRD or spec (.md / .txt), or paste a URL.
              </div>
              <input
                ref={docFileInputRef}
                type="file"
                accept=".md,.txt,.markdown,text/markdown,text/plain"
                style={{ fontSize: 11, maxWidth: '100%' }}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (!f) {
                    setDocUploadName(null)
                    setDocUploadText(null)
                    return
                  }
                  const reader = new FileReader()
                  reader.onload = () => {
                    const t = typeof reader.result === 'string' ? reader.result : ''
                    setDocUploadName(f.name)
                    setDocUploadText(t)
                    setDocUrlField('')
                  }
                  reader.onerror = () => {
                    setDocUploadName(null)
                    setDocUploadText(null)
                  }
                  reader.readAsText(f)
                }}
              />
              {docUploadName ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', fontSize: 11 }}>
                  <span className="pill" style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {docUploadName}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setDocUploadName(null)
                      setDocUploadText(null)
                      if (docFileInputRef.current) docFileInputRef.current.value = ''
                    }}
                    style={{
                      borderRadius: 8,
                      border: '1px solid var(--border)',
                      background: 'var(--panel)',
                      fontSize: 11,
                      padding: '4px 8px',
                      cursor: 'pointer',
                    }}
                  >
                    Remove
                  </button>
                </div>
              ) : null}
              <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 2 }}>Doc URL (if no file)</div>
              <input
                value={docUrlField}
                onChange={(e) => setDocUrlField(e.target.value)}
                disabled={Boolean(docUploadText)}
                placeholder="https://…"
                style={{
                  width: '100%',
                  borderRadius: 8,
                  border: '1px solid var(--border)',
                  background: docUploadText ? 'var(--bg-muted)' : 'var(--panel)',
                  padding: '6px 8px',
                  color: 'var(--text)',
                  font: 'inherit',
                  fontSize: 11,
                }}
              />
            </div>
          </div>
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
                    id: 'product_eng' as const,
                    label: 'Product engineering (doc-first)',
                    agents: 'doc_fetcher → librarian → sdet → builder | governor → diff_validator → judge_evaluator',
                    desc: 'Fetches optional doc URL (task context or .agenti_helix/doc_url), then runs the full TDD stack with a diff scope gate before evaluation.',
                  },
                  {
                    id: 'diff_guard_patch' as const,
                    label: 'Quick patch + diff gate',
                    agents: 'coder_patch_v1 → diff_validator_v1 → judge_v1',
                    desc: 'Fast single-file patch with a git-diff scope check before the snippet judge. Uses the same sign-off semantics as Quick patch.',
                  },
                  {
                    id: 'secure_build_plus' as const,
                    label: 'Secure full build',
                    agents: 'full TDD coder | governor → diff_validator → judge_evaluator',
                    desc: 'Same multi-file TDD coder as Full build, with an extra diff-validator gate between security review and evaluation.',
                  },
                  {
                    id: 'lint_type_gate' as const,
                    label: 'Lint + type gate (build)',
                    agents: 'full TDD coder | linter → type_checker → judge_evaluator',
                    desc: 'Full TDD implementation with static analysis agents surfacing eslint/ruff and mypy/tsc signals into the evaluator.',
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
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>
            Avg of DAG scores: weighted by node status (running and sign-off earn partial credit; failures reduce it).
          </div>
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
        <div
          style={{
            border: '1px solid rgba(22, 163, 74, 0.35)',
            borderRadius: 14,
            background: 'rgba(22, 163, 74, 0.08)',
            padding: 12,
          }}
        >
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Successful commits</div>
          <div style={{ fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em', color: 'rgba(22, 163, 74, 1)' }}>
            {counts.SUCCESSFUL_COMMIT}
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>Signed off (all nodes passed)</div>
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
              <FeatureCard key={f.feature_id} feature={f} onDeleted={() => void loadAll()} />
            ))}
            {!features ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>Loading…</div> : null}
          </div>

          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 12 }}>
            Trust dashboard summarizes Helix DAG state transitions; use “Features” for detailed DAG progress and task intervention.
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
                blockedItems.map((it) => (
                  <TriageTaskCard key={`${it.feature_id}:${it.dag_id}:${it.summary}`} item={it} onDeleted={() => void loadAll()} />
                ))
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

function RepositoryContextPage() {
  const [tab, setTab] = useState<'repo_map' | 'rules'>('repo_map')
  const [repoMap, setRepoMap] = useState<RepoMapResponse | null>(null)
  const [rules, setRules] = useState<RulesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [query, setQuery] = useState('')
  const [maxLines, setMaxLines] = useState<number | 'all'>(1000)

  async function loadAll() {
    setBusy(true)
    try {
      setError(null)
      const [rm, rr] = await Promise.all([fetchRepoMap(), fetchRules()])
      setRepoMap(rm)
      setRules(rr)
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
    return () => {
      cancelled = true
    }
  }, [])

  const repoLines = useMemo(() => {
    return (repoMap?.content ?? '').split('\n')
  }, [repoMap])

  const filteredLines = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return repoLines
    return repoLines.filter((l) => l.toLowerCase().includes(q))
  }, [repoLines, query])

  const effectiveMax = maxLines === 'all' ? filteredLines.length : maxLines
  const shownText = filteredLines.slice(0, effectiveMax).join('\n')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <PageTitle title="Repository Context" subtitle="Repo map + rules.json (context pruning + governance)" />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
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
      </div>

      {error ? <ErrorBox title="Failed to load repository context" error={error} /> : null}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <a
          className="pill"
          href="#"
          onClick={(e) => {
            e.preventDefault()
            setTab('repo_map')
          }}
          style={{
            cursor: 'pointer',
            borderColor: tab === 'repo_map' ? 'var(--primary)' : undefined,
            color: tab === 'repo_map' ? 'var(--primary)' : undefined,
            background: tab === 'repo_map' ? 'var(--primary-bg)' : undefined,
          }}
        >
          Repo map
        </a>
        <a
          className="pill"
          href="#"
          onClick={(e) => {
            e.preventDefault()
            setTab('rules')
          }}
          style={{
            cursor: 'pointer',
            borderColor: tab === 'rules' ? 'var(--primary)' : undefined,
            color: tab === 'rules' ? 'var(--primary)' : undefined,
            background: tab === 'rules' ? 'var(--primary-bg)' : undefined,
          }}
        >
          Rules
        </a>
      </div>

      {tab === 'repo_map' ? (
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 6 }}>Index (compressed repo map)</div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                {repoMap ? `${repoMap.path} · ${repoMap.format}` : 'Loading…'}
              </div>
            </div>
            {repoMap ? (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'flex-end' }}>
                <CopyButton text={repoMap.content} label="Copy raw" />
              </div>
            ) : null}
          </div>

          <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ flex: '1 1 260px' }}>
              <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Filter lines</div>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. function signature, class name, path segment…"
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
            </div>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <div style={{ minWidth: 180 }}>
                <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Max lines</div>
                <select
                  value={String(maxLines)}
                  onChange={(e) => {
                    const v = e.target.value
                    setMaxLines(v === 'all' ? 'all' : Number(v))
                  }}
                  style={{
                    width: '100%',
                    borderRadius: 12,
                    border: '1px solid var(--border)',
                    background: 'var(--bg)',
                    padding: 10,
                    color: 'var(--text)',
                    font: 'inherit',
                  }}
                >
                  <option value={200}>200</option>
                  <option value={500}>500</option>
                  <option value={800}>800</option>
                  <option value={1000}>1000</option>
                  <option value={2000}>2000</option>
                  <option value={5000}>5000</option>
                  <option value="all">All</option>
                </select>
              </div>
            </div>
          </div>

          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 12 }}>
            Showing {Math.min(effectiveMax, filteredLines.length)} / {filteredLines.length} lines
          </div>

          <pre
            style={{
              margin: '12px 0 0',
              padding: 10,
              borderRadius: 12,
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              overflow: 'auto',
              maxHeight: 520,
              fontSize: 11,
              whiteSpace: 'pre',
              wordBreak: 'break-word',
            }}
          >
            {repoMap ? shownText : 'Loading…'}
          </pre>
        </section>
      ) : null}

      {tab === 'rules' ? (
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 6 }}>Governance rules (rules.json)</div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{rules ? 'Read-only in UI (persisted server-side)' : 'Loading…'}</div>
            </div>
            {rules ? <CopyButton text={JSON.stringify(rules, null, 2)} label="Copy rules" /> : null}
          </div>

          <textarea
            value={rules ? JSON.stringify(rules, null, 2) : ''}
            disabled
            style={{
              width: '100%',
              marginTop: 12,
              minHeight: 520,
              resize: 'vertical',
              borderRadius: 12,
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              padding: 10,
              color: 'var(--muted)',
              font: 'inherit',
              fontFamily: 'var(--mono)',
              fontSize: 12,
              whiteSpace: 'pre',
            }}
          />
        </section>
      ) : null}
    </div>
  )
}

function SettingsPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [rules, setRules] = useState<RulesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function loadAll() {
    setBusy(true)
    try {
      setError(null)
      const [h, r] = await Promise.all([fetchHealth(), fetchRules()])
      setHealth(h)
      setRules(r)
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
    return () => {
      cancelled = true
    }
  }, [])

  const apiBase = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8001'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <PageTitle title="Settings" subtitle="Backend connectivity + governance artifacts" />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
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
      </div>

      {error ? <ErrorBox title="Settings load failed" error={error} /> : null}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 12, alignItems: 'start' }}>
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 8 }}>API connection</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 12 }}>Health + repo root used for `.agenti_helix/` artifacts.</div>

          <div style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 10, background: 'var(--bg)' }}>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>API base URL</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 12, wordBreak: 'break-word' }}>{apiBase}</div>
          </div>

          <div style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 10, background: 'var(--bg)', marginTop: 10 }}>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>repo_root (from /api/health)</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 12, wordBreak: 'break-word' }}>
              {health ? health.repo_root : 'Loading…'}
            </div>
          </div>
        </section>
      </div>

      <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
        <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 8 }}>Governance rules</div>
        <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 12 }}>
          `rules.json` is shown on the Repository Context page.
        </div>
        {rules ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)' }}>keys: {Object.keys(rules).length}</div>
            <a className="pill" href="/repo#rules">
              Open rules.json
            </a>
          </div>
        ) : (
          <div style={{ color: 'var(--muted)', fontSize: 12 }}>Loading…</div>
        )}
      </section>
    </div>
  )
}

const KANBAN_COLUMNS: Array<{ id: FeatureColumn; label: string; help: string }> = [
  { id: 'SCOPING', label: 'Scoping', help: 'Helix parsing / compiling intent' },
  { id: 'ORCHESTRATING', label: 'Orchestrating', help: 'Agents building' },
  { id: 'BLOCKED', label: 'Blocked', help: 'Human needed' },
  { id: 'VERIFYING', label: 'Verifying', help: 'Judges running' },
  { id: 'READY_FOR_REVIEW', label: 'Ready for Review', help: 'Awaiting sign-off' },
  { id: 'SUCCESSFUL_COMMIT', label: 'Successful commits', help: 'Signed off — all nodes passed verification' },
]

function formatEta(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  const m = Math.round(seconds / 60)
  return `${m}m`
}

function triageSeverityTone(sev: TriageItem['severity']): { border: string; text: string; bg: string } {
  if (sev === 'HIGH') return { border: 'rgba(220, 38, 38, 0.35)', text: 'rgba(220, 38, 38, 1)', bg: 'rgba(220, 38, 38, 0.12)' }
  if (sev === 'MEDIUM') return { border: 'rgba(187, 128, 9, 0.35)', text: 'rgba(187, 128, 9, 1)', bg: 'rgba(187, 128, 9, 0.12)' }
  return { border: 'rgba(46, 160, 67, 0.35)', text: 'rgba(46, 160, 67, 1)', bg: 'rgba(46, 160, 67, 0.12)' }
}

function FeatureCard({ feature, onDeleted }: { feature: Feature; onDeleted?: () => void }) {
  const navigate = useNavigate()
  const success = feature.column === 'SUCCESSFUL_COMMIT'
  const [removing, setRemoving] = useState(false)

  async function handleRemove(e: React.MouseEvent) {
    e.stopPropagation()
    e.preventDefault()
    if (
      !window.confirm(
        `Remove DAG "${feature.title}" (${feature.dag_id}) from the system? This deletes the DAG, its state, and related checkpoints. This cannot be undone.`,
      )
    ) {
      return
    }
    setRemoving(true)
    try {
      await deleteFeature(feature.feature_id)
      onDeleted?.()
    } catch (err) {
      window.alert(err instanceof Error ? err.message : String(err))
    } finally {
      setRemoving(false)
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => navigate(`/features/${encodeURIComponent(feature.feature_id)}`)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') navigate(`/features/${encodeURIComponent(feature.feature_id)}`)
      }}
      style={{
        position: 'relative',
        border: success ? '1px solid rgba(22, 163, 74, 0.55)' : '1px solid var(--border)',
        borderRadius: 12,
        background: success ? 'rgba(22, 163, 74, 0.11)' : 'var(--panel)',
        padding: 12,
        paddingTop: 14,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        boxShadow: 'var(--shadow)',
        cursor: 'pointer',
      }}
    >
      <button
        type="button"
        title="Remove DAG from system"
        aria-label="Remove DAG from system"
        disabled={removing}
        onClick={handleRemove}
        style={{
          position: 'absolute',
          top: 6,
          right: 6,
          zIndex: 2,
          width: 28,
          height: 28,
          borderRadius: 8,
          border: '1px solid var(--border)',
          background: 'var(--bg)',
          color: 'var(--muted)',
          fontSize: 18,
          lineHeight: 1,
          cursor: removing ? 'wait' : 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 0,
          opacity: removing ? 0.6 : 1,
        }}
      >
        ×
      </button>
      <div style={{ fontWeight: 650, letterSpacing: '-0.01em', fontSize: 13, paddingRight: 32 }}>{feature.title}</div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 12 }}>
        <span className="pill">Conf {Math.round(feature.confidence * 100)}%</span>
        <span className="pill">ETA {formatEta(feature.eta_seconds)}</span>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 12 }}>
        <span>Passed: {feature.node_status_counts.PASSED_VERIFICATION ?? 0}</span>
        <span>Failed: {feature.node_status_counts.FAILED ?? 0}</span>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 12 }} onClick={(e) => e.stopPropagation()}>
        <a
          className="pill"
          href={`/features/${encodeURIComponent(feature.feature_id)}`}
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            navigate(`/features/${encodeURIComponent(feature.feature_id)}`)
          }}
        >
          View DAG Progress
        </a>
        <a
          className="pill"
          href={`/features/${encodeURIComponent(feature.feature_id)}/signoff`}
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            navigate(`/features/${encodeURIComponent(feature.feature_id)}/signoff`)
          }}
        >
          Review & Merge
        </a>
      </div>
    </div>
  )
}

function TriageTaskCard({ item, onDeleted }: { item: TriageItem; onDeleted?: () => void }) {
  const navigate = useNavigate()
  const [removing, setRemoving] = useState(false)
  const t = triageSeverityTone(item.severity)

  async function handleRemove(e: React.MouseEvent) {
    e.stopPropagation()
    e.preventDefault()
    if (
      !window.confirm(
        `Remove DAG "${item.title}" (${item.dag_id}) from the system? This deletes the DAG, its state, and related checkpoints. This cannot be undone.`,
      )
    ) {
      return
    }
    setRemoving(true)
    try {
      await deleteFeature(item.feature_id)
      onDeleted?.()
    } catch (err) {
      window.alert(err instanceof Error ? err.message : String(err))
    } finally {
      setRemoving(false)
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => navigate(`/features/${encodeURIComponent(item.feature_id)}`)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') navigate(`/features/${encodeURIComponent(item.feature_id)}`)
      }}
      style={{
        position: 'relative',
        border: '1px solid var(--border)',
        borderRadius: 12,
        background: 'var(--bg)',
        padding: 10,
        paddingTop: 12,
        cursor: 'pointer',
      }}
    >
      <button
        type="button"
        title="Remove DAG from system"
        aria-label="Remove DAG from system"
        disabled={removing}
        onClick={handleRemove}
        style={{
          position: 'absolute',
          top: 6,
          right: 6,
          zIndex: 2,
          width: 28,
          height: 28,
          borderRadius: 8,
          border: '1px solid var(--border)',
          background: 'var(--panel)',
          color: 'var(--muted)',
          fontSize: 18,
          lineHeight: 1,
          cursor: removing ? 'wait' : 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 0,
          opacity: removing ? 0.6 : 1,
        }}
      >
        ×
      </button>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, paddingRight: 32 }}>
        <div style={{ fontWeight: 650, fontSize: 12, minWidth: 0, flex: 1 }}>{item.title}</div>
        <span className="pill" style={{ flexShrink: 0, borderColor: t.border, background: t.bg, color: t.text }}>
          {item.severity}
        </span>
      </div>
      <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 6 }}>{item.summary}</div>
      <div style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--mono)', marginTop: 6 }}>{item.dag_id}</div>
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
  const toolLogs = cp.tool_logs as Record<string, unknown>
  const judgeVal = toolLogs['judge']
  const judgeObj = judgeVal && typeof judgeVal === 'object' ? (judgeVal as Record<string, unknown>) : null
  const justificationVal = judgeObj ? judgeObj['justification'] : null
  if (typeof justificationVal === 'string' && justificationVal.trim()) return justificationVal.trim()
  const humanEsc = toolLogs['human_escalation']
  if (typeof humanEsc === 'string' && humanEsc.trim()) return `Human escalation: ${humanEsc.trim()}`
  return null
}

function _normRelPath(p: string): string {
  let s = p.replace(/\\/g, '/')
  while (s.startsWith('./')) {
    s = s.slice(2)
  }
  return s
}

function _dedupeSnapshotsByPath<T extends { path?: string }>(snapshots: T[]): T[] {
  const seen = new Set<string>()
  const out: T[] = []
  for (const snap of snapshots) {
    const raw = typeof snap.path === 'string' ? snap.path : ''
    const key = raw ? _normRelPath(raw) : ''
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(snap)
  }
  return out
}

function _parseCheckpointDiffJson(cp: Checkpoint | null): unknown {
  if (!cp?.diff) return null
  try {
    return JSON.parse(cp.diff) as unknown
  } catch {
    return null
  }
}

function _isBuildPipelineDiffJson(d: unknown): d is {
  file_snapshots?: Array<{ path?: string; content?: string }>
  files_written?: string[]
  test_file_paths?: string[]
} {
  if (!d || typeof d !== 'object') return false
  const o = d as Record<string, unknown>
  const fs = o.file_snapshots
  const fw = o.files_written
  const tp = o.test_file_paths
  if (Array.isArray(fs) && fs.length > 0) return true
  if (Array.isArray(fw) && fw.length > 0) return true
  if (Array.isArray(tp) && tp.length > 0) return true
  return false
}

const _diffPreBoxStyle: CSSProperties = {
  margin: 0,
  padding: 10,
  borderRadius: 12,
  border: '1px solid var(--border)',
  background: 'var(--bg)',
  overflow: 'auto',
  maxHeight: 380,
  fontSize: 12,
}

function ExtractedDiff({ cp, targetFile }: { cp: Checkpoint | null; targetFile?: string | null }) {
  if (!cp) {
    return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No checkpoint found.</div>
  }
  const pre = cp.pre_state_ref ?? ''
  const post = cp.post_state_ref ?? ''
  const toolLogs = cp.tool_logs as Record<string, unknown>
  const gitUnifiedRaw = toolLogs['git_unified_diff']
  const gitUnified = typeof gitUnifiedRaw === 'string' ? gitUnifiedRaw.trim() : ''
  const parsedDiff = _parseCheckpointDiffJson(cp)
  const buildPipeline = _isBuildPipelineDiffJson(parsedDiff)

  if (!gitUnified && !buildPipeline) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Pre</div>
          <pre style={_diffPreBoxStyle}>{pre}</pre>
        </div>
        <div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Post</div>
          <pre style={_diffPreBoxStyle}>{post}</pre>
        </div>
      </div>
    )
  }

  type FileSnap = { path?: string; content?: string }
  const buildObj =
    buildPipeline && parsedDiff && typeof parsedDiff === 'object'
      ? (parsedDiff as { file_snapshots?: FileSnap[] })
      : null
  const snapshots: FileSnap[] = _dedupeSnapshotsByPath(
    Array.isArray(buildObj?.file_snapshots) ? buildObj.file_snapshots : [],
  )

  // When git unified diff is present it already includes new/untracked files; rendering file_snapshots
  // as well duplicates the same content (especially for TDD-added test files).
  const showSnapshotFallback = buildPipeline && !gitUnified
  const targetNorm = showSnapshotFallback && targetFile ? _normRelPath(targetFile) : ''

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {gitUnified ? (
        <div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Unified diff (git)</div>
          <pre style={{ ..._diffPreBoxStyle, maxHeight: 420 }}>{gitUnified}</pre>
        </div>
      ) : null}

      {showSnapshotFallback ? (
        <div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Files (checkpoint snapshots)</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {snapshots.length === 0 ? (
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                Paths recorded in this checkpoint have no embedded snapshots (see unified diff above when available).
              </div>
            ) : (
              snapshots.map((snap: FileSnap, idx: number) => {
                const path = typeof snap.path === 'string' ? snap.path : ''
                const content = typeof snap.content === 'string' ? snap.content : ''
                const pathNorm = path ? _normRelPath(path) : ''
                const isPrimary = Boolean(targetNorm && pathNorm && pathNorm === targetNorm)
                if (isPrimary) {
                  return (
                    <div key={`${path}:${idx}`}>
                      <div style={{ fontWeight: 650, fontSize: 12, marginBottom: 6, fontFamily: 'var(--mono)' }}>{path}</div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                        <div>
                          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Pre</div>
                          <pre style={_diffPreBoxStyle}>{pre}</pre>
                        </div>
                        <div>
                          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>Post</div>
                          <pre style={_diffPreBoxStyle}>{content || post}</pre>
                        </div>
                      </div>
                    </div>
                  )
                }
                return (
                  <div key={`${path}:${idx}`}>
                    <div style={{ fontWeight: 650, fontSize: 12, marginBottom: 6, fontFamily: 'var(--mono)' }}>{path || '(unknown path)'}</div>
                    <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 6 }}>Post (new or auxiliary file)</div>
                    <pre style={_diffPreBoxStyle}>{content}</pre>
                  </div>
                )
              })
            )}
          </div>
        </div>
      ) : null}
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
          title={
            (data?.dag.user_intent_label || data?.dag.macro_intent)
              ? String(data.dag.user_intent_label || data.dag.macro_intent)
              : `Feature: ${featureId ?? ''}`
          }
          subtitle={
            data ? `Confidence ${Math.round(data.metrics.confidence * 100)}% · ETA ${formatEta(data.metrics.eta_seconds)}` : 'Loading…'
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

      {error ? <ErrorBox title="Failed to load feature" error={error} /> : null}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 360px', gap: 12, alignItems: 'start' }}>
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12, minHeight: 340 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>DAG</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
            {Object.keys(nodes).length === 0 ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>No DAG nodes found.</div> : null}
            {Object.keys(nodes).map((nodeId) => {
              const s = stateNodes[nodeId]
              const tone = nodeTone(s?.status, s?.verification_status ?? null)
              const tryLabel =
                typeof s?.verification_cycle === 'number' && s.verification_cycle > 0
                  ? ` · try ${s.verification_cycle}`
                  : s?.attempts
                    ? ` · ${s.attempts}x`
                    : ''
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
                  <NodePill tone={tone} label={`${nodeId}${tryLabel}`} />
                </span>
              )
            })}
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 12 }}>Click a node to open Task Intervention.</div>
        </section>

        <aside style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Feature documentation</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Intent</div>
              <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{data?.dag.macro_intent ?? '—'}</div>
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Architecture (allowed touch)</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>v1 derives from node task target files:</div>
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
  const [checkpoints, setCheckpoints] = useState<Checkpoint[] | null>(null)
  const [guidance, setGuidance] = useState('')
  const [docUrl, setDocUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionNote, setActionNote] = useState<string | null>(null)
  const [refreshTick, setRefreshTick] = useState(0)

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
        </div>
      </div>

      {error ? <ErrorBox title="Failed to load intervention context" error={error} /> : null}
      {actionNote ? (
        <div style={{ border: '1px solid var(--border)', borderRadius: 12, background: 'var(--panel)', padding: 10, color: 'var(--muted)', fontSize: 12 }}>
          {actionNote}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 380px) 1fr', gap: 12, alignItems: 'start' }}>
        <section style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 12, minHeight: 340 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 10 }}>Agent briefing</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, whiteSpace: 'pre-wrap' }}>{briefing}</div>
          <div style={{ marginTop: 10, color: 'var(--muted)', fontSize: 12 }}>
            Last checkpoint: {latestCp ? `${latestCp.checkpoint_id} · ${latestCp.status}` : '—'}
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
        <ExtractedDiff cp={latestCp} targetFile={task?.target_file} />
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
  const [busy, setBusy] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [macroIntentDraft, setMacroIntentDraft] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [mergeCandidate, setMergeCandidate] = useState<Checkpoint | null>(null)
  const [pendingSignoff, setPendingSignoff] = useState<{ checkpoint: Checkpoint; nodeId: string } | null>(null)
  const [mergeCelebration, setMergeCelebration] = useState(false)
  const mergeCelebrationTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (mergeCelebrationTimerRef.current != null) {
        window.clearTimeout(mergeCelebrationTimerRef.current)
      }
    }
  }, [])

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

  async function handleApplySignoff() {
    if (!featureId || !pendingSignoff) {
      setActionError(
        'No staged judge-approved checkpoint found for sign-off. Ensure the node is AWAITING_SIGNOFF and the latest checkpoint is PASSED_PENDING_SIGNOFF (refresh the page).',
      )
      return
    }
    setActionError(null)
    setBusy(true)
    try {
      await applyNodeSignoff({
        dag_id: featureId,
        node_id: pendingSignoff.nodeId,
        task_id: pendingSignoff.checkpoint.task_id,
        checkpoint_id: pendingSignoff.checkpoint.checkpoint_id,
      })
      await resumeDag(featureId)
      const [f, ev] = await Promise.all([
        fetchFeature(featureId),
        fetchEvents({ runId: featureId, limit: 5000 }),
      ])
      setFeature(f)
      setEvents(ev)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
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
    setBusy(true)
    try {
      await mergeTask({
        task_id: mergeCandidate.task_id,
        checkpoint_id: mergeCandidate.checkpoint_id,
        target_branch: 'main',
        commit_message: 'Merge verified checkpoint',
      })
      setMergeCelebration(true)
      if (mergeCelebrationTimerRef.current != null) window.clearTimeout(mergeCelebrationTimerRef.current)
      mergeCelebrationTimerRef.current = window.setTimeout(() => {
        setMergeCelebration(false)
        mergeCelebrationTimerRef.current = null
      }, 2800)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {mergeCelebration ? (
        <div className="merge-celebration-overlay" aria-live="polite">
          <div className="merge-celebration-burst" aria-hidden="true">
            {Array.from({ length: 14 }, (_, i) => (
              <span
                key={i}
                className="merge-celebration-particle"
                style={
                  {
                    ['--rot']: `${i * 26}deg`,
                    animationDelay: `${i * 32}ms`,
                  } as CSSProperties & { ['--rot']: string }
                }
              />
            ))}
          </div>
          <div className="merge-celebration-card">
            <div className="merge-celebration-title">Merged</div>
            <div className="merge-celebration-sub">Verified changes are written to your repo files.</div>
          </div>
        </div>
      ) : null}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <PageTitle title="Sign-Off" subtitle={feature?.dag.macro_intent ?? (featureId ? `Feature ${featureId}` : '—')} />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
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
              void handleApplySignoff()
            }}
          >
            {busy ? 'Working…' : 'Sign off on changes'}
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
              borderColor: 'rgba(22, 163, 74, 0.55)',
              color: 'rgba(22, 163, 74, 1)',
              background: 'rgba(22, 163, 74, 0.12)',
            }}
          >
            Merge to main
          </a>
        </div>
      </div>

      {error ? <ErrorBox title="Failed to load sign-off view" error={error} /> : null}
      {actionError ? <ErrorBox title="Sign-off or merge" error={actionError} /> : null}

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
          {!editMode ? (
            <div style={{ marginTop: 12 }}>
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
            </div>
          ) : null}
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
          <SignoffDiffBlock
            feature={feature}
            onLatestCheckpoint={setMergeCandidate}
            onPendingSignoff={setPendingSignoff}
          />
        </section>
      </div>

    </div>
  )
}

function _checkpointIsPendingSignoff(c: Checkpoint): boolean {
  const s = String(c.status ?? '').trim()
  return s === 'PASSED_PENDING_SIGNOFF'
}

function SignoffDiffBlock({
  feature,
  onLatestCheckpoint,
  onPendingSignoff,
}: {
  feature: FeatureDetails | null
  onLatestCheckpoint?: (cp: Checkpoint | null) => void
  onPendingSignoff?: (pick: { checkpoint: Checkpoint; nodeId: string } | null) => void
}) {
  const [cp, setCp] = useState<Checkpoint | null>(null)
  const [displayTargetFile, setDisplayTargetFile] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const nodeEntries = Object.entries(feature?.dag.nodes ?? {})
        if (nodeEntries.length === 0) {
          setCp(null)
          setDisplayTargetFile(null)
          onLatestCheckpoint?.(null)
          onPendingSignoff?.(null)
          return
        }

        const awaitingIds = new Set(
          feature?.state?.nodes
            ? Object.entries(feature.state.nodes)
                .filter(([, sn]) => (sn as { status?: string })?.status === 'AWAITING_SIGNOFF')
                .map(([id]) => id)
            : [],
        )

        const ordered: typeof nodeEntries = []
        if (awaitingIds.size > 0) {
          for (const [nodeId, n] of nodeEntries) {
            if (awaitingIds.has(nodeId)) ordered.push([nodeId, n])
          }
          for (const [nodeId, n] of nodeEntries) {
            if (!awaitingIds.has(nodeId)) ordered.push([nodeId, n])
          }
        } else {
          ordered.push(...nodeEntries)
        }

        let pending: { checkpoint: Checkpoint; nodeId: string } | null = null
        let mergeEligible: Checkpoint | null = null
        const latestPerNode: Array<{ nodeId: string; cp: Checkpoint }> = []

        for (const [nodeId, n] of ordered.slice(0, 12)) {
          const cps = await fetchCheckpoints({ task_id: n.task.task_id, limit: 24 })
          if (cps[0]) latestPerNode.push({ nodeId, cp: cps[0] })
          for (const c of cps) {
            if (_checkpointIsPendingSignoff(c) && !pending) {
              pending = { checkpoint: c, nodeId }
            }
            if (c.status === 'PASSED' && !mergeEligible) {
              mergeEligible = c
            }
          }
        }

        latestPerNode.sort((a, b) => (b.cp.updated_at ?? 0) - (a.cp.updated_at ?? 0))
        const display = pending?.checkpoint ?? latestPerNode[0]?.cp ?? null
        const displayNodeId = pending?.nodeId ?? latestPerNode[0]?.nodeId ?? null
        const tf =
          displayNodeId && feature?.dag?.nodes?.[displayNodeId]
            ? feature.dag.nodes[displayNodeId].task.target_file
            : null

        if (!cancelled) {
          setCp(display)
          setDisplayTargetFile(tf)
          onLatestCheckpoint?.(mergeEligible)
          onPendingSignoff?.(pending)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [feature?.feature_id, feature?.state, feature?.dag.nodes, onLatestCheckpoint, onPendingSignoff])

  if (error) return <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 10 }}>{error}</div>
  return (
    <div style={{ marginTop: 10 }}>
      <ExtractedDiff cp={cp} targetFile={displayTargetFile} />
    </div>
  )
}

function TriageInboxPage() {
  const [items, setItems] = useState<TriageItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reloadTriage = useCallback(async () => {
    try {
      setError(null)
      const res = await fetchTriage()
      setItems(res.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

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

    void load()
    const t = window.setInterval(() => {
      if (!cancelled) void load()
    }, 2500)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <PageTitle title="Triage Inbox" subtitle="Aggregated blockers across all in-flight features (management by exception)" />

      {error ? <ErrorBox title="Failed to load triage" error={error} /> : null}

      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: 14,
          background: 'var(--panel)',
          padding: 8,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        {(items ?? []).length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 12, padding: 10 }}>{items ? 'No blocked items.' : 'Loading…'}</div>
        ) : null}
        {(items ?? []).map((it) => (
          <TriageTaskCard key={`${it.feature_id}:${it.dag_id}:${it.summary}`} item={it} onDeleted={() => void reloadTriage()} />
        ))}
      </div>
    </div>
  )
}

function AgentRosterPage() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [promptDraft, setPromptDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const res = await fetchAgents()
        if (!cancelled) {
          setAgents(res.agents)
          setSelectedId((prev) => prev ?? (res.agents[0]?.id ?? null))
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    async function loadDetail() {
      try {
        setLoading(true)
        setError(null)
        const d = await fetchAgentDetail(selectedId!)
        if (!cancelled) {
          setDetail(d)
          setPromptDraft(d.prompt)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    loadDetail()
    return () => {
      cancelled = true
    }
  }, [selectedId])

  async function handleSave() {
    if (!detail) return
    try {
      setSaving(true)
      setError(null)
      await updateAgentPrompt(detail.id, promptDraft)
      const d = await fetchAgentDetail(detail.id)
      setDetail(d)
      setPromptDraft(d.prompt)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <PageTitle title="Agent roster" subtitle="Inspect and edit system prompts + schemas for each agent." />
      {error ? (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            background: 'var(--panel)',
            padding: 10,
            color: 'var(--muted)',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          {error}
        </div>
      ) : null}
      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: 14,
          background: 'var(--panel)',
          padding: 12,
          display: 'grid',
          gridTemplateColumns: '260px minmax(0, 1.4fr) minmax(0, 1fr)',
          gap: 12,
          alignItems: 'start',
        }}
      >
        <section style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 4 }}>Agents</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>
            {agents ? `${agents.length} configured` : 'Loading…'}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 360, overflow: 'auto' }}>
            {(agents ?? []).map((a) => {
              const active = a.id === selectedId
              return (
                <button
                  key={a.id}
                  type="button"
                  onClick={() => setSelectedId(a.id)}
                  style={{
                    textAlign: 'left',
                    borderRadius: 10,
                    border: active ? '1px solid var(--accent)' : '1px solid var(--border)',
                    background: active ? 'rgba(88, 80, 236, 0.09)' : 'var(--bg)',
                    padding: 8,
                    cursor: 'pointer',
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 650 }}>{a.id}</div>
                  <div style={{ color: 'var(--muted)', marginTop: 2 }}>{a.description}</div>
                </button>
              )
            })}
            {!agents && (
              <div style={{ color: 'var(--muted)', fontSize: 12, padding: 4 }}>Loading agent roster…</div>
            )}
          </div>
        </section>

        <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <div>
              <div style={{ fontWeight: 650, fontSize: 13 }}>System prompt</div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                {detail
                  ? `${detail.id} · ${detail.prompt_filename}`
                  : loading
                  ? 'Loading…'
                  : 'Select an agent to inspect its prompt.'}
              </div>
            </div>
            <button
              type="button"
              onClick={handleSave}
              disabled={!detail || saving}
              style={{
                borderRadius: 999,
                border: '1px solid var(--border)',
                background: saving ? 'var(--bg-muted)' : 'var(--bg)',
                padding: '6px 12px',
                fontSize: 12,
                cursor: detail && !saving ? 'pointer' : 'default',
              }}
            >
              {saving ? 'Saving…' : 'Save prompt'}
            </button>
          </div>
          <textarea
            value={promptDraft}
            onChange={(e) => setPromptDraft(e.target.value)}
            placeholder="System prompt will appear here."
            style={{
              width: '100%',
              minHeight: 260,
              resize: 'vertical',
              borderRadius: 12,
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              padding: 10,
              color: 'var(--text)',
              font: 'inherit',
              fontFamily: 'var(--mono)',
              fontSize: 12,
              whiteSpace: 'pre',
            }}
          />
        </section>

        <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontWeight: 650, fontSize: 13 }}>Pydantic schemas</div>
          <div style={{ color: 'var(--muted)', fontSize: 12 }}>
            {detail ? `${detail.input_model} → ${detail.output_model}` : 'Select an agent to view its schemas.'}
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 8,
              alignItems: 'start',
            }}
          >
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Input schema</div>
              <pre
                style={{
                  margin: 0,
                  padding: 8,
                  borderRadius: 10,
                  border: '1px solid var(--border)',
                  background: 'var(--bg)',
                  fontSize: 11,
                  maxHeight: 220,
                  overflow: 'auto',
                }}
              >
                {detail?.input_schema ? JSON.stringify(detail.input_schema, null, 2) : 'null'}
              </pre>
            </div>
            <div>
              <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Output schema</div>
              <pre
                style={{
                  margin: 0,
                  padding: 8,
                  borderRadius: 10,
                  border: '1px solid var(--border)',
                  background: 'var(--bg)',
                  fontSize: 11,
                  maxHeight: 220,
                  overflow: 'auto',
                }}
              >
                {detail?.output_schema ? JSON.stringify(detail.output_schema, null, 2) : 'null'}
              </pre>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

function FeaturesKanbanPage() {
  const [features, setFeatures] = useState<Feature[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // D6: Read search query from URL params (?q=...) set by the Topbar search.
  const [searchParams] = useSearchParams()
  const searchQuery = (searchParams.get('q') ?? '').toLowerCase().trim()

  const reloadFeatures = useCallback(async () => {
    try {
      setError(null)
      const data = await fetchFeatures()
      setFeatures(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

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

    void load()
    const t = window.setInterval(() => {
      if (!cancelled) void load()
    }, 2500)
    return () => {
      cancelled = true
      window.clearInterval(t)
    }
  }, [])

  // D6: Filter features by the search query (matches title or macro_intent).
  const filteredFeatures = useMemo(() => {
    if (!searchQuery) return features ?? []
    return (features ?? []).filter(
      (f) =>
        f.title?.toLowerCase().includes(searchQuery) ||
        f.dag_id?.toLowerCase().includes(searchQuery),
    )
  }, [features, searchQuery])

  const grouped = useMemo(() => {
    const map: Record<string, Feature[]> = {}
    for (const c of KANBAN_COLUMNS) map[c.id] = []
    for (const f of filteredFeatures) map[f.column]?.push(f)
    return map as Record<FeatureColumn, Feature[]>
  }, [filteredFeatures])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <PageTitle
        title="Features"
        subtitle="System-state Kanban: Scoping → Orchestrating → Blocked → Verifying → Ready for Review → Successful commits"
      />

      {error ? <ErrorBox title="Failed to load features from API. Is the API server running?" error={error} /> : null}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, alignItems: 'start' }}>
        {KANBAN_COLUMNS.map((col) => (
          <section key={col.id} style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 10, minHeight: 360 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '6px 6px 10px' }}>
              <div style={{ fontWeight: 650, fontSize: 13 }}>{col.label}</div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{col.help}</div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 6 }}>
              {(grouped[col.id] ?? []).map((f) => (
                <FeatureCard key={f.feature_id} feature={f} onDeleted={() => void reloadFeatures()} />
              ))}
              {features && (grouped[col.id] ?? []).length === 0 ? <div style={{ color: 'var(--muted)', fontSize: 12, padding: 6 }}>No items</div> : null}
              {!features && !error ? <div style={{ color: 'var(--muted)', fontSize: 12, padding: 6 }}>Loading…</div> : null}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}

function Sidebar() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('helix_sidebar_collapsed') === '1'
    } catch {
      return false
    }
  })

  function toggleCollapsed() {
    setCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem('helix_sidebar_collapsed', next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }

  return (
    <aside className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`}>
      <div className="sidebarHeader">
        <Icon label="AH" />
        <div className="sidebarHeaderTitle">Agenti-Helix</div>
        <button type="button" className="sidebarCollapseBtn" onClick={toggleCollapsed} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? '→' : '←'}
        </button>
      </div>

      <div className="navSectionLabel">Control plane</div>
      <NavLink to="/" end className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}>
        <Icon label="D" />
        <span className="navItemLabel">Dashboard</span>
      </NavLink>
      <NavLink to="/features" className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}>
        <Icon label="K" />
        <span className="navItemLabel">Features</span>
      </NavLink>
      <NavLink to="/triage" className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}>
        <Icon label="T" />
        <span className="navItemLabel">Triage Inbox</span>
      </NavLink>
      <NavLink to="/agents" className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}>
        <Icon label="A" />
        <span className="navItemLabel">Agent Roster</span>
      </NavLink>
      <NavLink to="/repo" className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}>
        <Icon label="R" />
        <span className="navItemLabel">Repository Context</span>
      </NavLink>
      <NavLink to="/settings" className={({ isActive }) => `navItem ${isActive ? 'navItemActive' : ''}`}>
        <Icon label="S" />
        <span className="navItemLabel">Settings</span>
      </NavLink>
    </aside>
  )
}

function Topbar() {
  const navigate = useNavigate()
  const location = useLocation()
  const qRef = useRef('')

  const hint = useMemo(() => {
    if (location.pathname.startsWith('/features')) return 'Search features, DAGs, tasks...'
    if (location.pathname.startsWith('/triage')) return 'Search triage items...'
    return 'Search...'
  }, [location.pathname])

  useEffect(() => {
    // Reset query on navigation without touching React state.
    qRef.current = ''
  }, [location.pathname])

  return (
    <header className="topbar">
      <div className="search" role="search">
        <Icon label="⌘" />
        <input
          defaultValue=""
          onChange={(e) => {
            qRef.current = e.target.value
          }}
          placeholder={hint}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const trimmed = qRef.current.trim()
              if (trimmed) navigate(`/features?q=${encodeURIComponent(trimmed)}`)
            }
          }}
        />
      </div>
      <div className="topbarRight" />
    </header>
  )
}

function App() {
  const [llmPanelCollapsed, setLlmPanelCollapsed] = useState(() => {
    try {
      return localStorage.getItem('helix_llm_panel_collapsed') === '1'
    } catch {
      return false
    }
  })

  function toggleLlmPanel() {
    setLlmPanelCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem('helix_llm_panel_collapsed', next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }

  const [sidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('helix_sidebar_collapsed') === '1'
    } catch {
      return false
    }
  })

  return (
    <div
      className={`appShell${llmPanelCollapsed ? ' appShell--traceCollapsed' : ''}${sidebarCollapsed ? ' appShell--sidebarCollapsed' : ''}`}
    >
      <Sidebar />
      <div className="main">
        <Topbar />
        <main className="content">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/features" element={<FeaturesKanbanPage />} />
            <Route path="/features/:featureId" element={<FeatureDagPage />} />
            <Route path="/features/:featureId/nodes/:nodeId" element={<TaskInterventionPage />} />
            <Route path="/features/:featureId/signoff" element={<SignoffTripanePage />} />
            <Route path="/triage" element={<TriageInboxPage />} />
            <Route path="/agents" element={<AgentRosterPage />} />
            <Route path="/repo" element={<RepositoryContextPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Placeholder title="Not found" />} />
          </Routes>
        </main>
      </div>
      <LlmTracePanel collapsed={llmPanelCollapsed} onToggleCollapsed={toggleLlmPanel} />
    </div>
  )
}

export default App

