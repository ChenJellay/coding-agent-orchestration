import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Checkpoint, Feature, FeatureColumn } from '../lib/api'

// ---------------------------------------------------------------------------
// Primitive UI components
// ---------------------------------------------------------------------------

export function Icon({ label }: { label: string }) {
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

export function PageTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <div style={{ fontWeight: 650, letterSpacing: '-0.01em' }}>{title}</div>
      {subtitle ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>{subtitle}</div> : null}
    </div>
  )
}

export function Placeholder({ title }: { title: string }) {
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

export function ErrorBox({ title, error }: { title?: string; error: string }) {
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

export function CopyButton({ text, label }: { text: string; label: string }) {
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

// ---------------------------------------------------------------------------
// Kanban / feature helpers
// ---------------------------------------------------------------------------

export const KANBAN_COLUMNS: Array<{ id: FeatureColumn; label: string; help: string }> = [
  { id: 'SCOPING', label: 'Scoping', help: 'Helix parsing / compiling intent' },
  { id: 'ORCHESTRATING', label: 'Orchestrating', help: 'Agents building' },
  { id: 'BLOCKED', label: 'Blocked', help: 'Human needed' },
  { id: 'VERIFYING', label: 'Verifying', help: 'Judges running' },
  { id: 'READY_FOR_REVIEW', label: 'Ready for Review', help: 'Awaiting sign-off' },
]

export function formatEta(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  const m = Math.round(seconds / 60)
  return `${m}m`
}

export function formatTs(ts?: number | null): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return String(ts)
  }
}

export function extractBriefingFromCheckpoint(cp: Checkpoint | null): string | null {
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

export function FeatureCard({ feature }: { feature: Feature }) {
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
      <div style={{ fontWeight: 650, letterSpacing: '-0.01em', fontSize: 13 }}>{feature.title}</div>
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
        <a
          className="pill"
          href={`/features/${encodeURIComponent(feature.feature_id)}/signoff`}
          onClick={(e) => {
            e.preventDefault()
            navigate(`/features/${encodeURIComponent(feature.feature_id)}/signoff`)
          }}
        >
          Review & Merge
        </a>
      </div>
    </div>
  )
}

export function NodePill({ label, tone }: { label: string; tone: 'green' | 'yellow' | 'red' | 'gray' }) {
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

export function ExtractedDiff({ cp }: { cp: Checkpoint | null }) {
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
