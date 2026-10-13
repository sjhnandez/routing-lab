/**
 * Types for the frozen lab API contract. `Report` mirrors the JSON schema
 * produced by `runner/report.py` (see `docs/example-report.json`).
 */

/** Stages streamed over GET /api/run/{id}/events (SSE). */
export type StageName = 'queued' | 'boot' | 'scenario' | 'teardown' | 'done' | 'error'

export interface StageEvent {
  /** ISO timestamp set by the server. */
  ts: string
  stage: StageName
  scenario?: number
  name?: string
  status?: 'pass' | 'fail'
  message?: string
  report_id?: string
}

export type RunRejectionReason =
  | 'cooldown'
  | 'run_in_progress'
  | 'rate_limited'
  | 'trigger_disabled'

export interface RunRejectionBody {
  reason: RunRejectionReason
  retry_after_s?: number
}

export interface LabInfo {
  base_url: string
  mock_url: string
  litellm_image: string
  git_rev: string | null
}

export interface ConfigSnapshot {
  source: string
  router_settings: Record<string, unknown>
}

export interface AssertionResult {
  desc: string
  pass: boolean
  detail: string
}

export interface TimelineEntry {
  t: number
  event: string
  detail: string
}

export interface ServedBy {
  mode: string
  hits: number
}

export type ScenarioStatus = 'pass' | 'fail' | 'error'

export interface ScenarioReport {
  id: number
  name: string
  status: ScenarioStatus
  duration_s: number
  expected: string
  observed: string
  assertions: AssertionResult[]
  timeline: TimelineEntry[]
  served_by: ServedBy[]
}

export interface ReportSummary {
  total: number
  passed: number
  failed: number
  status: 'pass' | 'fail'
}

/** Full evidence report (GET /api/last-run, GET /api/reports/{run_id}). */
export interface Report {
  run_id: string
  profile: string
  started_at: string
  finished_at: string
  duration_s: number
  lab: LabInfo
  config_snapshot: ConfigSnapshot
  scenarios: ScenarioReport[]
  summary: ReportSummary
}

/** Row of GET /api/history, newest first. */
export interface HistoryEntry {
  run_id: string
  profile: string
  started_at: string
  duration_s: number
  summary: ReportSummary
}

/** GET /api/health */
export interface LabHealth {
  status: string
  trigger_enabled: boolean
  queue: { pending: number; running: number }
}

/** GET /api/docs */
export interface LabDocs {
  methodology: string
  scenarios: string
}

/** One raw probe line from the prober's JSONL history (fields as recorded). */
export interface FleetProbe {
  ts: string
  model?: string
  ok?: boolean
  class?: string
  http_status?: number | null
  latency_ms?: number
  error_type?: string | null
  budget?: { date?: string; count?: number; cap?: number }
}

/** One row of GET /api/fleet models[]. */
export interface FleetModel {
  model: string
  probes_24h: number
  ok_24h: number
  last_class: string
  last_ts: string
}

/** GET /api/fleet — read-only aggregate of the prober's JSONL history. */
export interface FleetSummary {
  generated_at: string
  available: boolean
  note: string | null
  budget: { date: string; count: number; cap: number }
  last_24h: {
    total: number
    ok: number
    ok_rate: number
    counts: Record<string, number>
  }
  models: FleetModel[]
  recent: FleetProbe[]
}

/**
 * POST /api/chat contract. Success streams OpenAI-style `data:` chunks
 * (delta fields `content` and/or `reasoning_content`), then `data: [DONE]`
 * and one terminal `event: meta` frame with the router's attribution;
 * upstream failures surface as a single in-stream `event: error` frame.
 * Gate rejections arrive as JSON bodies before the stream starts.
 */

/** Rejection reasons of POST /api/chat (JSON bodies, never in the SSE stream). */
export type ChatRejectionReason =
  | 'message_empty'
  | 'message_too_long'
  | 'rate_limited'
  | 'busy'
  | 'chat_disabled'
  | 'router_key_missing'

export interface ChatRejectionBody {
  reason: ChatRejectionReason
  retry_after_s?: number
}

/** Reasons of an in-stream `event: error` frame. */
export type ChatStreamErrorReason = 'pools_busy' | 'router_error' | 'router_timeout'

/** Terminal `event: meta` frame: which deployment served the reply. */
export interface ChatMeta {
  served_by: string | null
  model_id: string | null
  /** Concrete deployment that answered (e.g. `vendor/model:free`), when known. */
  model_name: string | null
  attempted_retries: number | null
  attempted_fallbacks: number | null
  latency_ms: number | null
}
