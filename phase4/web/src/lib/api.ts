export type FeatureColumn =
  | 'SCOPING'
  | 'ORCHESTRATING'
  | 'BLOCKED'
  | 'VERIFYING'
  | 'READY_FOR_REVIEW'

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
      { node_id: string; status: string; attempts: number; verification_status: string | null }
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
  timestamp?: number
  message?: string
  location?: string
  runId?: string
  hypothesisId?: string
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

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8001'

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status} ${path}${text ? `: ${text}` : ''}`)
  }
  return (await res.json()) as T
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

export async function fetchAgents(): Promise<{ agents: unknown[] }> {
  return await getJson<{ agents: unknown[] }>('/api/agents')
}

