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

export interface MockedHit {
  ts: number
  model: string
  mode: string
  stream: boolean
  status: number
  outcome?: string
  duration_s?: number
  chunks_sent?: number
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
  mocked_hits: MockedHit[]
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
