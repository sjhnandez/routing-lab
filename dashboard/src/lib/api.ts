import type {
  HistoryEntry,
  LabDocs,
  LabHealth,
  Report,
  RunRejectionBody,
  RunRejectionReason,
  StageEvent,
  StageName,
} from './types'

/** API base: same-origin `/api` by default, overridable with VITE_API_BASE. */
const API_BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/+$/, '')

/** HTTP error carrying the server's status code. */
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** POST /api/run rejection (429/503) carrying the frozen reason enum. */
export class RunRejectedError extends Error {
  readonly reason: RunRejectionReason
  readonly retryAfterS: number | undefined

  constructor(body: RunRejectionBody) {
    super(`run rejected: ${body.reason}`)
    this.name = 'RunRejectedError'
    this.reason = body.reason
    this.retryAfterS = body.retry_after_s
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { accept: 'application/json' },
  })
  if (!res.ok) {
    throw new ApiError(`GET ${path} failed with HTTP ${res.status}`, res.status)
  }
  return (await res.json()) as T
}

/** POST /api/run; resolves with the id of the accepted run. */
export async function startRun(): Promise<string> {
  const res = await fetch(`${API_BASE}/run`, { method: 'POST' })
  let body: Record<string, unknown> | null = null
  try {
    body = asRecord(await res.json())
  } catch {
    body = null
  }
  if (res.status === 202) {
    const id = body?.id
    if (typeof id === 'string' && id.length > 0) return id
    throw new ApiError('run was accepted without an id', res.status)
  }
  if (res.status === 429 || res.status === 503) {
    const reason = body?.reason
    if (
      reason === 'cooldown' ||
      reason === 'run_in_progress' ||
      reason === 'rate_limited' ||
      reason === 'trigger_disabled'
    ) {
      const rejection: RunRejectionBody = { reason }
      const retryAfter = body?.retry_after_s
      if (typeof retryAfter === 'number') rejection.retry_after_s = retryAfter
      throw new RunRejectedError(rejection)
    }
  }
  throw new ApiError(`POST /run failed with HTTP ${res.status}`, res.status)
}

/** URL of the SSE stream for an accepted run. */
export function runEventsUrl(id: string): string {
  return `${API_BASE}/run/${encodeURIComponent(id)}/events`
}

const STAGE_NAMES: readonly StageName[] = [
  'queued',
  'boot',
  'scenario',
  'teardown',
  'done',
  'error',
]

/** Parse one SSE `data:` payload; returns null for malformed frames. */
export function parseStageEvent(data: string): StageEvent | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  const record = asRecord(parsed)
  if (!record) return null
  const stage = record.stage
  if (typeof stage !== 'string' || !STAGE_NAMES.includes(stage as StageName)) {
    return null
  }
  const event: StageEvent = {
    ts: typeof record.ts === 'string' ? record.ts : '',
    stage: stage as StageName,
  }
  if (typeof record.scenario === 'number') event.scenario = record.scenario
  if (typeof record.name === 'string') event.name = record.name
  if (record.status === 'pass' || record.status === 'fail') event.status = record.status
  if (typeof record.message === 'string') event.message = record.message
  if (typeof record.report_id === 'string') event.report_id = record.report_id
  return event
}

/** GET /api/last-run — report of the most recent run, or null. */
export async function fetchLastRun(): Promise<Report | null> {
  const body = await getJson<{ report: Report | null }>('/last-run')
  return body.report
}

/** GET /api/history — run summaries, newest first. */
export function fetchHistory(): Promise<HistoryEntry[]> {
  return getJson<HistoryEntry[]>('/history')
}

/** GET /api/reports/{run_id} — full report JSON. */
export function fetchReport(runId: string): Promise<Report> {
  return getJson<Report>(`/reports/${encodeURIComponent(runId)}`)
}

/** GET /api/docs — methodology + scenario markdown. */
export function fetchDocs(): Promise<LabDocs> {
  return getJson<LabDocs>('/docs')
}

/** GET /api/health */
export function fetchHealth(): Promise<LabHealth> {
  return getJson<LabHealth>('/health')
}

/** Download URL for the JSON download buttons. */
export function reportDownloadUrl(runId: string): string {
  return `${API_BASE}/reports/${encodeURIComponent(runId)}`
}
