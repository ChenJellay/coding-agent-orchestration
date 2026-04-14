import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchFeature, type FeatureDetails } from '../lib/api'
import { ErrorBox, formatEta, NodePill, PageTitle } from '../components/shared'

export function FeatureDagPage() {
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
