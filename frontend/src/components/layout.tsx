import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Icon } from './shared'

export function Sidebar() {
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

export function Topbar() {
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
