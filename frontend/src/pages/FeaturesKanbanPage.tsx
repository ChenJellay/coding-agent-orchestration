import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fetchFeatures, type Feature, type FeatureColumn } from '../lib/api'
import { ErrorBox, FeatureCard, KANBAN_COLUMNS, PageTitle } from '../components/shared'

export function FeaturesKanbanPage() {
  const [features, setFeatures] = useState<Feature[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // D6: Read search query from URL params (?q=...) set by the Topbar search.
  const [searchParams] = useSearchParams()
  const searchQuery = (searchParams.get('q') ?? '').toLowerCase().trim()

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
      <PageTitle title="Features" subtitle="System-state Kanban: Scoping → Orchestrating → Blocked → Verifying → Ready for Review" />

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
                <FeatureCard key={f.feature_id} feature={f} />
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
