import { useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import './App.css'
import { LlmTracePanel } from './LlmTracePanel'
import { Sidebar, Topbar } from './components/layout'
import { Placeholder } from './components/shared'
import { AgentRosterPage } from './pages/AgentRosterPage'
import { DashboardPage } from './pages/DashboardPage'
import { FeatureDagPage } from './pages/FeatureDagPage'
import { FeaturesKanbanPage } from './pages/FeaturesKanbanPage'
import { RepositoryContextPage } from './pages/RepositoryContextPage'
import { SettingsPage } from './pages/SettingsPage'
import { SignoffTripanePage } from './pages/SignoffTripanePage'
import { TaskInterventionPage } from './pages/TaskInterventionPage'
import { TriageInboxPage } from './pages/TriageInboxPage'

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
