import { useEffect, useState } from 'react'
import {
  fetchAgentDetail,
  fetchAgents,
  updateAgentPrompt,
  type AgentDetail,
  type AgentSummary,
} from '../lib/api'
import { PageTitle } from '../components/shared'

export function AgentRosterPage() {
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
