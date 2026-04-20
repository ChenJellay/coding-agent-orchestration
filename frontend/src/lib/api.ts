export type FeatureColumn =
  | 'SCOPING'
  | 'ORCHESTRATING'
  | 'BLOCKED'
  | 'VERIFYING'
  | 'READY_FOR_REVIEW'
  | 'SUCCESSFUL_COMMIT'

export type Feature = {
  feature_id: string
  dag_id: string
  title: string
  column: FeatureColumn
  confidence: number
  eta_seconds: number | null
  node_status_counts: Record<string, number>
}

export type FeatureDetails = {
  feature_id: string
  dag: {
    dag_id: string
    macro_intent: string
    /** Short dashboard command for titles; falls back to macro_intent when absent. */
    user_intent_label?: string
    nodes: Record<
      string,
      {
        node_id: string
        description: string
        task: {
          task_id: string
          intent: string
          target_file: string
          acceptance_criteria: string
          repo_path: string
        }
      }
    >
    edges: Array<[string, string]>
  }
  state: {
    dag_id: string
    nodes: Record<
      string,
      {
        node_id: string
        status: string
        attempts: number
        verification_status: string | null
        verification_cycle?: number
      }
    >
  } | null
  metrics: {
    node_status_counts: Record<string, number>
    confidence: number
    eta_seconds: number | null
    column: FeatureColumn
  }
}

export type TriageItem = {
  feature_id: string
  dag_id: string
  title: string
  severity: 'HIGH' | 'MEDIUM' | 'LOW'
  summary: string
  timestamp: number | null
}

export type TriageResponse = { items: TriageItem[] }

export type EventLog = {
  id?: string
  timestamp?: number
  message?: string
  location?: string
  runId?: string
  hypothesisId?: string
  traceId?: string
  dagId?: string
  data?: Record<string, unknown>
}

export type Checkpoint = {
  checkpoint_id: string
  task_id: string
  status: string
  pre_state_ref: string
  post_state_ref: string | null
  diff: string | null
  tool_logs: Record<string, unknown>
  created_at: number
  updated_at: number
}

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8001'

// D1: Bearer token injected centrally from VITE_API_KEY env var.
// Set VITE_API_KEY in frontend/.env.local for authenticated deployments.
function _authHeaders(): Record<string, string> {
  const key = import.meta.env.VITE_API_KEY
  if (!key) return {}
  return { Authorization: `Bearer ${key}` }
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { headers: _authHeaders() })
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new Error(`Unauthorized (${res.status}): check VITE_API_KEY in frontend/.env.local`)
    }
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} ${path}${text ? `: ${text}` : ''}`)
  }
  return (await res.json()) as T
}

function _httpErrorDetail(status: number, path: string, method: string, text: string): Error {
  if (status === 401 || status === 403) {
    return new Error(`Unauthorized (${status}): check VITE_API_KEY in frontend/.env.local`)
  }
  try {
    const j = JSON.parse(text) as { error?: { message?: string; code?: string }; detail?: unknown }
    const msg = j?.error?.message
    if (typeof msg === 'string' && msg.trim()) {
      return new Error(msg)
    }
  } catch {
    /* use raw text */
  }
  return new Error(`HTTP ${status} ${method} ${path}${text ? `: ${text}` : ''}`)
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw _httpErrorDetail(res.status, path, 'POST', text)
  }
  return (await res.json()) as T
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw _httpErrorDetail(res.status, path, 'PUT', text)
  }
  return (await res.json()) as T
}

async function deleteJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'DELETE',
    headers: { ..._authHeaders() },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw _httpErrorDetail(res.status, path, 'DELETE', text)
  }
  return (await res.json()) as T
}

/** Permanently removes DAG spec, state, and related checkpoints/merge records (editor role). */
export async function deleteFeature(featureId: string): Promise<{ ok: true }> {
  return await deleteJson<{ ok: true }>(`/api/features/${encodeURIComponent(featureId)}`)
}

export async function fetchFeatures(): Promise<Feature[]> {
  return await getJson<Feature[]>('/api/features')
}

export async function fetchFeature(featureId: string): Promise<FeatureDetails> {
  return await getJson<FeatureDetails>(`/api/features/${encodeURIComponent(featureId)}`)
}

export async function fetchTriage(): Promise<TriageResponse> {
  return await getJson<TriageResponse>('/api/triage')
}

export async function fetchEvents(params: {
  runId?: string
  hypothesisId?: string
  sinceTs?: number
  limit?: number
}): Promise<EventLog[]> {
  const qs = new URLSearchParams()
  if (params.runId) qs.set('runId', params.runId)
  if (params.hypothesisId) qs.set('hypothesisId', params.hypothesisId)
  if (params.sinceTs != null) qs.set('sinceTs', String(params.sinceTs))
  if (params.limit != null) qs.set('limit', String(params.limit))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return await getJson<EventLog[]>(`/api/events${suffix}`)
}

export async function fetchCheckpoints(params: { task_id?: string; limit?: number }): Promise<Checkpoint[]> {
  const qs = new URLSearchParams()
  if (params.task_id) qs.set('task_id', params.task_id)
  if (params.limit != null) qs.set('limit', String(params.limit))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return await getJson<Checkpoint[]>(`/api/checkpoints${suffix}`)
}

export async function fetchCompute(): Promise<{ event_count: number }> {
  return await getJson<{ event_count: number }>('/api/compute')
}

export type AgentSummary = {
  id: string
  description: string
  prompt: string
  input_model: string
  output_model: string
}

export type AgentDetail = {
  id: string
  description: string
  prompt: string
  prompt_filename: string
  input_model: string
  output_model: string
  input_schema: unknown | null
  output_schema: unknown
}

export async function fetchAgents(): Promise<{ agents: AgentSummary[] }> {
  return await getJson<{ agents: AgentSummary[] }>('/api/agents')
}

export async function fetchAgentDetail(agentId: string): Promise<AgentDetail> {
  return await getJson<AgentDetail>(`/api/agents/${encodeURIComponent(agentId)}`)
}

export async function updateAgentPrompt(agentId: string, prompt: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/agents/${encodeURIComponent(agentId)}/prompt`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify({ prompt }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} PUT /api/agents/${agentId}${text ? `: ${text}` : ''}`)
  }
}

export async function rerunTask(params: {
  task_id: string
  checkpoint_id: string
  guidance?: string
  feature_id?: string
  node_id?: string
}): Promise<{ ok: true; reRunId?: string }> {
  return await postJson('/api/tasks/rerun', params)
}

export async function abortTask(params: {
  task_id: string
  feature_id?: string
  node_id?: string
  abort_reason?: string
}): Promise<{ ok: true; aborted?: boolean }> {
  return await postJson('/api/tasks/abort', params)
}

export async function attachTaskContext(params: { task_id: string; doc_url: string; notes?: string }): Promise<{ ok: true }> {
  return await postJson('/api/tasks/context', params)
}

export async function applyAndRerun(params: {
  task_id: string
  checkpoint_id: string
  guidance?: string
  doc_url?: string
  feature_id?: string
  node_id?: string
}): Promise<{ ok: true }> {
  return await postJson('/api/tasks/apply-and-rerun', params)
}

export async function editDagIntent(dag_id: string, params: { macro_intent: string }): Promise<{ ok: true }> {
  return await putJson(`/api/dags/${encodeURIComponent(dag_id)}/intent`, params)
}

export type MemoryResponse = {
  summary: string
  items?: Array<{ timestamp?: number; message?: string; location?: string | null }>
}

export type HealthResponse = {
  ok: true
  repo_root: string
}

export type RepoMapResponse = {
  path: string
  format: string
  content: string
}

// Backend returns the JSON content of `.agenti_helix/rules.json` (best-effort).
// We keep it flexible so the UI can render arbitrary rule structures.
export type RulesResponse = Record<string, unknown>

export async function fetchMemory(params: { runId: string; limit?: number }): Promise<MemoryResponse> {
  const qs = new URLSearchParams()
  if (params.limit != null) qs.set('limit', String(params.limit))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return await getJson<MemoryResponse>(`/api/memory?runId=${encodeURIComponent(params.runId)}${suffix}`)
}

export async function mergeTask(params: {
  task_id: string
  checkpoint_id: string
  target_branch?: string
  commit_message?: string
}): Promise<{ ok: true; mergeRef?: string; sha?: string | null; simulated?: boolean }> {
  return await postJson('/api/tasks/merge', params)
}

export async function applyNodeSignoff(params: {
  dag_id: string
  node_id: string
  task_id: string
  checkpoint_id: string
  signed_by?: string
}): Promise<{ ok: true }> {
  const { dag_id, node_id, ...body } = params
  return await postJson(
    `/api/dags/${encodeURIComponent(dag_id)}/nodes/${encodeURIComponent(node_id)}/signoff-apply`,
    body,
  )
}

export async function resumeDag(dag_id: string): Promise<{ ok: true }> {
  return await postJson(`/api/dags/${encodeURIComponent(dag_id)}/resume`, {})
}

export type PipelineMode =
  | 'patch'
  | 'build'
  | 'product_eng'
  | 'diff_guard_patch'
  | 'secure_build_plus'
  | 'lint_type_gate'

export async function startDagFromDashboard(params: {
  repo_path: string
  macro_intent: string
  agent_ids?: string[]
  dag_id?: string
  use_llm?: boolean
  pipeline_mode?: PipelineMode | null
  /** Raw documentation text (from upload); server writes under target repo `.agenti_helix/`. */
  doc_text?: string
  doc_filename?: string
  /** Remote doc URL for doc_fetcher when not uploading text. */
  doc_url?: string
}): Promise<{ ok: true; dag_id: string }> {
  const body = {
    repo_path: params.repo_path,
    macro_intent: params.macro_intent,
    agent_ids: params.agent_ids ?? ['coder_patch_v1', 'judge_v1'],
    ...(params.dag_id != null ? { dag_id: params.dag_id } : {}),
    use_llm: params.use_llm ?? false,
    pipeline_mode: params.pipeline_mode ?? null,
    ...(params.doc_text != null && params.doc_text !== ''
      ? { doc_text: params.doc_text, ...(params.doc_filename ? { doc_filename: params.doc_filename } : {}) }
      : params.doc_url != null && params.doc_url.trim() !== ''
        ? { doc_url: params.doc_url.trim() }
        : {}),
  }
  return await postJson<{ ok: true; dag_id: string }>('/api/dags/run', body)
}

export async function fetchHealth(): Promise<HealthResponse> {
  return await getJson<HealthResponse>('/api/health')
}

export async function fetchRepoMap(): Promise<RepoMapResponse> {
  return await getJson<RepoMapResponse>('/api/repo-map')
}

export async function fetchRules(): Promise<RulesResponse> {
  return await getJson<RulesResponse>('/api/rules')
}

