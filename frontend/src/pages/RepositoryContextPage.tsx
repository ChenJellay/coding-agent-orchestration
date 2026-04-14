import { useEffect, useMemo, useState } from 'react'
import { fetchRepoMap, fetchRules, type RepoMapResponse, type RulesResponse } from '../lib/api'
import { CopyButton, ErrorBox, PageTitle } from '../components/shared'

export function RepositoryContextPage() {
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
