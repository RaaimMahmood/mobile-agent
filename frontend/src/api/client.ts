import axios from 'axios'
import { getApiKey } from './apiKey'

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 10_000,
})

api.interceptors.request.use((config) => {
  const key = getApiKey()
  if (key) config.headers['X-API-Key'] = key
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg = err.response?.data?.detail ?? err.message ?? 'Unknown error'
    return Promise.reject(new Error(msg))
  },
)

// ── Types ─────────────────────────────────────────────────────────────────────

export interface DeviceStatus {
  connected: boolean
  serial: string | null
  resolution: [number, number] | null
  message: string
}

export interface SessionResponse {
  session_id: string
  message: string
}

export interface ChatRequest {
  message: string
  provider?: string
  device_serial?: string
}

export interface ChatResponse {
  session_id: string
  app_name: string
  task: string
  message: string
}

export interface AgentStatus {
  session_id: string
  status: string
  round_num: number
  task_complete: boolean
  failure_reason: string | null
  errors: string[]
  pending_confirmation?: Record<string, unknown> | null
}

export interface KBDoc {
  id: string
  app_name: string
  element_sig: string
  class_name: string
  resource_id: string
  content_desc: string
  text: string
  documentation: string
  observed_result: string
  last_explored_at: string
}

export interface KBList {
  app_name: string
  count: number
  docs: KBDoc[]
}

export interface DeviceListEntry {
  serial: string
  resolution: [number, number] | null
  busy: boolean
}

export interface HealthDetailed {
  devices: { count: number; serials: string[] }
  chromadb: { reachable: boolean; mode?: 'local' }
  neo4j: { reachable: boolean }
  langfuse: { enabled: boolean; configured: boolean }
  ollama: { reachable: boolean }
}

export interface ExploreOptions {
  app_name: string
  max_rounds?: number
  provider?: string
  device_serial?: string
  max_tokens?: number
  max_cost_usd?: number
  max_llm_calls?: number
}

export interface DeployOptions extends ExploreOptions {
  task: string
  reasoning_mode?: 'reasoning' | 'fast'
}

export interface FanoutOptions {
  task: string
  app_name: string
  device_serials: string[]
  provider?: string
  reasoning_mode?: 'reasoning' | 'fast'
  max_rounds?: number
  max_tokens?: number
  max_cost_usd?: number
  max_llm_calls?: number
}

export interface FanoutResult {
  device_serial: string
  session_id: string | null
  started: boolean
  detail: string | null
}

export interface SessionSummary {
  session_id: string
  app_name: string
  task: string
  mode: string
  provider: string
  reasoning_mode: string
  device_serial: string
  status: string
  round_num: number
  task_complete: boolean
  failure_reason: string | null
  tokens_used: number
  estimated_cost_usd: number
  llm_call_count: number
  escalation_count: number
  created_at: string
  updated_at: string
}

export interface HistoryAction {
  action: string
  element_id?: number | null
  thought?: string
  observation?: string
  [key: string]: unknown
}

export interface HistoryEntry {
  round_num: number
  action: HistoryAction
  element_sig: string | null
  created_at: string
}

export interface StartRecordingRequest {
  task_description: string
  device_serial?: string
}

export interface StartRecordingResponse {
  recording_id: string
}

export interface RecordStepRequest {
  recording_id: string
  action_type: string
  params: Record<string, unknown>
  description?: string
}

export interface RecordStepResponse {
  ok: boolean
  steps_recorded: number
}

export interface StopRecordingRequest {
  recording_id: string
}

export interface StopRecordingResponse {
  ok: boolean
  path: string
}

export interface ReplayRequest {
  device_serial?: string
  on_drift?: 'stop' | 'skip'
}

export interface ReplayFailure {
  step: number
  reason: string
  similarity?: number
}

export interface ReplayResponse {
  completed: boolean
  steps_executed: number
  steps_total: number
  failures: ReplayFailure[]
  stopped_at_step?: number
  reason?: string
  similarity?: number
}

export interface DeleteDemoResponse {
  ok: boolean
}

export interface DemoListItem {
  recording_id: string
  task_description: string
  step_count: number
  created_at: string
}

// ── API calls ─────────────────────────────────────────────────────────────────

export const deviceApi = {
  status: () => api.get<DeviceStatus>('/device/status').then((r) => r.data),
  list: () => api.get<DeviceListEntry[]>('/device/list').then((r) => r.data),
  healthDetailed: () => api.get<HealthDetailed>('/device/health/detailed').then((r) => r.data),
  screenshot: () => api.get<{ screenshot_b64: string }>('/device/screenshot').then((r) => r.data),
  tap: (x: number, y: number) => api.post('/device/tap', { x, y }),
  keyEvent: (code: number) => api.post('/device/keyevent', { code }),
}

export const agentApi = {
  explore: (opts: ExploreOptions) =>
    api.post<SessionResponse>('/agent/explore', opts).then((r) => r.data),
  deploy: (opts: DeployOptions) =>
    api.post<SessionResponse>('/agent/deploy', opts).then((r) => r.data),
  deployFanout: (opts: FanoutOptions) =>
    api.post<{ results: FanoutResult[] }>('/agent/deploy/fanout', opts).then((r) => r.data),
  // 90s — the backend's LLM retry backoff for 429/5xx can take up to ~50s of
  // sleeps alone (5+15+30s) plus request time, so the default 10s client
  // timeout was firing before the backend's own real error (e.g. a 422)
  // ever came back, masking it behind a generic "timeout" message.
  chat: (body: ChatRequest) =>
    api.post<ChatResponse>('/agent/chat', body, { timeout: 90_000 }).then((r) => r.data),
  status: (session_id: string) =>
    api.get<AgentStatus>(`/agent/${session_id}`).then((r) => r.data),
  stop: (session_id: string) =>
    api.delete<AgentStatus>(`/agent/${session_id}`).then((r) => r.data),
  confirm: (session_id: string, approve: boolean) =>
    api.post<AgentStatus>(`/agent/${session_id}/confirm`, { approve }).then((r) => r.data),
  listSessions: (limit?: number, offset?: number) =>
    api.get<SessionSummary[]>('/agent/sessions', { params: { limit, offset } }).then((r) => r.data),
  getSessionHistory: (session_id: string) =>
    api.get<HistoryEntry[]>(`/agent/${session_id}/history`).then((r) => r.data),
}

export const kbApi = {
  list: (app_name: string) => api.get<KBList>(`/kb/${app_name}`).then((r) => r.data),
  search: (app_name: string, q: string) =>
    api.get<KBList>(`/kb/${app_name}/search`, { params: { q } }).then((r) => r.data),
  clear: (app_name: string) => api.delete(`/kb/${app_name}`).then((r) => r.data),
}

export const demonstrationsApi = {
  startRecording: (app_name: string, body: StartRecordingRequest) =>
    api.post<StartRecordingResponse>(`/demonstrations/${app_name}/record/start`, body).then((r) => r.data),
  recordStep: (app_name: string, body: RecordStepRequest) =>
    api.post<RecordStepResponse>(`/demonstrations/${app_name}/record/step`, body).then((r) => r.data),
  stopRecording: (app_name: string, body: StopRecordingRequest) =>
    api.post<StopRecordingResponse>(`/demonstrations/${app_name}/record/stop`, body).then((r) => r.data),
  list: (app_name: string) => api.get<DemoListItem[]>(`/demonstrations/${app_name}`).then((r) => r.data),
  replay: (app_name: string, recording_id: string, body: ReplayRequest) =>
    api
      .post<ReplayResponse>(`/demonstrations/${app_name}/${recording_id}/replay`, body, { timeout: 120_000 })
      .then((r) => r.data),
  delete: (app_name: string, recording_id: string) =>
    api.delete<DeleteDemoResponse>(`/demonstrations/${app_name}/${recording_id}`).then((r) => r.data),
}
