import { useEffect, useState } from 'react'
import { fetchHealth, fetchRules, type HealthResponse, type RulesResponse } from '../lib/api'
import { ErrorBox, PageTitle } from '../components/shared'

export function SettingsPage() {
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
