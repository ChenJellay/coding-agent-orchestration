import { useEffect, useState } from 'react'
import { fetchTriage, type TriageItem } from '../lib/api'
import { ErrorBox, PageTitle } from '../components/shared'

export function TriageInboxPage() {
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
      <PageTitle title="Triage Inbox" subtitle="Aggregated blockers across all in-flight features (management by exception)" />

      {error ? <ErrorBox title="Failed to load triage" error={error} /> : null}

      <div style={{ border: '1px solid var(--border)', borderRadius: 14, background: 'var(--panel)', padding: 8 }}>
        {(items ?? []).length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 12, padding: 10 }}>{items ? 'No blocked items.' : 'Loading…'}</div>
        ) : null}
        {(items ?? []).map((it) => (
          <div key={`${it.feature_id}:${it.summary}`} style={{ padding: 10, borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 6 }}>
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
