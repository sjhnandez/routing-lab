import type {
    ChatMeta,
    ChatRejectionBody,
    ChatRejectionReason,
    ChatStreamErrorReason,
    FleetSummary,
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

/** GET /api/fleet — read-only aggregate of the prober's JSONL history. */
export function fetchFleet(): Promise<FleetSummary> {
  return getJson<FleetSummary>('/fleet')
}

/** GET /api/health */
export function fetchHealth(): Promise<LabHealth> {
  return getJson<LabHealth>('/health')
}

/** Download URL for the JSON download buttons. */
export function reportDownloadUrl(runId: string): string {
  return `${API_BASE}/reports/${encodeURIComponent(runId)}`
}

// ---------------------------------------------------------------------------
// POST /api/chat — "Try the router" streaming chat (see lib/types.ts for the
// contract). Gate rejections throw before the stream; everything after that
// arrives as `ChatStreamEvent`s from the SSE parse.
// ---------------------------------------------------------------------------

/** POST /api/chat gate rejection (400/429/503 JSON body). */
export class ChatRejectedError extends Error {
  readonly reason: ChatRejectionReason
  readonly retryAfterS: number | undefined

  constructor(body: ChatRejectionBody) {
    super(`chat rejected: ${body.reason}`)
    this.name = 'ChatRejectedError'
    this.reason = body.reason
    this.retryAfterS = body.retry_after_s
  }
}

/** One parsed event of the POST /api/chat SSE stream. */
export type ChatStreamEvent =
  | { type: 'delta'; content: string | null; reasoning: string | null }
  | { type: 'done' }
  | { type: 'meta'; meta: ChatMeta }
  | { type: 'error'; reason: ChatStreamErrorReason; status: number | null }

const CHAT_REJECTION_REASONS: readonly string[] = [
  'message_empty',
  'message_too_long',
  'rate_limited',
  'busy',
  'chat_disabled',
  'router_key_missing',
]

const CHAT_STREAM_ERROR_REASONS: readonly string[] = [
  'pools_busy',
  'router_error',
  'router_timeout',
]

/** One raw SSE frame: optional `event:` name plus the joined `data:` lines. */
interface SseFrame {
  event: string
  data: string
}

/** Parse one raw SSE frame block; returns null when it carries no data. */
function parseSseFrame(raw: string): SseFrame | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''))
    }
  }
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

/** OpenAI chat.completion.chunk → the delta fields the chat UI renders. */
function parseChatDelta(data: string): { content: string | null; reasoning: string | null } | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  const record = asRecord(parsed)
  const choices = record?.choices
  if (!Array.isArray(choices) || choices.length === 0) return null
  const choice = asRecord(choices[0])
  const delta = choice ? asRecord(choice.delta) : null
  if (!delta) return null
  const content = typeof delta.content === 'string' && delta.content !== '' ? delta.content : null
  const reasoning =
    typeof delta.reasoning_content === 'string' && delta.reasoning_content !== ''
      ? delta.reasoning_content
      : null
  if (content === null && reasoning === null) return null
  return { content, reasoning }
}

/** Parse an `event: meta` frame; returns null for malformed payloads. */
function parseChatMeta(data: string): ChatMeta | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  const record = asRecord(parsed)
  if (!record) return null
  const str = (value: unknown) => (typeof value === 'string' ? value : null)
  const num = (value: unknown) =>
    typeof value === 'number' && Number.isFinite(value) ? value : null
  return {
    served_by: str(record.served_by),
    model_id: str(record.model_id),
    model_name: str(record.model_name),
    attempted_retries: num(record.attempted_retries),
    attempted_fallbacks: num(record.attempted_fallbacks),
    latency_ms: num(record.latency_ms),
  }
}

/**
 * POST /api/chat and consume the SSE reply, invoking `onEvent` per parsed
 * frame. Resolves when the stream ends (cleanly or with an in-stream error
 * frame); gate rejections throw ChatRejectedError and transport failures
 * throw ApiError before any event is emitted.
 */
export async function streamChat(
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ message }),
    signal,
  })
  if (!res.ok) {
    let body: Record<string, unknown> | null = null
    try {
      body = asRecord(await res.json())
    } catch {
      body = null
    }
    const reason = body?.reason
    if (
      (res.status === 400 || res.status === 429 || res.status === 503) &&
      typeof reason === 'string' &&
      CHAT_REJECTION_REASONS.includes(reason)
    ) {
      const rejection: ChatRejectionBody = { reason: reason as ChatRejectionReason }
      const retryAfter = body?.retry_after_s
      if (typeof retryAfter === 'number') rejection.retry_after_s = retryAfter
      throw new ChatRejectedError(rejection)
    }
    throw new ApiError(`POST /chat failed with HTTP ${res.status}`, res.status)
  }
  if (!res.body) throw new ApiError('POST /chat returned no body', res.status)

  const emitFrame = (frame: SseFrame) => {
    if (frame.event === 'meta') {
      const meta = parseChatMeta(frame.data)
      if (meta) onEvent({ type: 'meta', meta })
      return
    }
    if (frame.event === 'error') {
      let reason: ChatStreamErrorReason = 'router_error'
      let status: number | null = null
      try {
        const record = asRecord(JSON.parse(frame.data))
        const rawReason = record?.reason
        if (typeof rawReason === 'string' && CHAT_STREAM_ERROR_REASONS.includes(rawReason)) {
          reason = rawReason as ChatStreamErrorReason
        }
        if (typeof record?.status === 'number') status = record.status
      } catch {
        // keep the conservative defaults
      }
      onEvent({ type: 'error', reason, status })
      return
    }
    if (frame.data.trim() === '[DONE]') {
      onEvent({ type: 'done' })
      return
    }
    const delta = parseChatDelta(frame.data)
    if (delta) onEvent({ type: 'delta', content: delta.content, reasoning: delta.reasoning })
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index = buffer.indexOf('\n\n')
    while (index !== -1) {
      const raw = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      const frame = parseSseFrame(raw)
      if (frame) emitFrame(frame)
      index = buffer.indexOf('\n\n')
    }
  }
}
