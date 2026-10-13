import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  ChatRejectedError,
  parseStageEvent,
  reportDownloadUrl,
  runEventsUrl,
  RunRejectedError,
  startRun,
  streamChat,
} from '../src/lib/api'

/** Stub the global fetch with a fixed response (or one per call). */
function mockFetch(response: Response | (() => Response)) {
  const fn = vi.fn(async () => (typeof response === 'function' ? response() : response))
  vi.stubGlobal('fetch', fn)
  return fn
}

/** A fetched SSE response body built from raw chunk strings. */
function sseResponse(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder()
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(body, { status: 200 })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('parseStageEvent', () => {
  it('normalizes a full stage event', () => {
    const raw = JSON.stringify({
      ts: '2026-10-13T08:29:31Z',
      stage: 'scenario',
      scenario: 3,
      name: 'mid-stream stall',
      status: 'pass',
      message: 'assertions green',
      report_id: 'run-42',
    })
    expect(parseStageEvent(raw)).toEqual({
      ts: '2026-10-13T08:29:31Z',
      stage: 'scenario',
      scenario: 3,
      name: 'mid-stream stall',
      status: 'pass',
      message: 'assertions green',
      report_id: 'run-42',
    })
  })

  it('keeps only known fields and defaults missing ts to empty', () => {
    expect(parseStageEvent(JSON.stringify({ stage: 'boot', extra: 'dropped' }))).toEqual({
      ts: '',
      stage: 'boot',
    })
  })

  it('rejects unknown stages and invalid status values', () => {
    expect(parseStageEvent(JSON.stringify({ stage: 'exploded' }))).toBeNull()
    expect(parseStageEvent(JSON.stringify({ stage: 'done', status: 'maybe' }))).toEqual({
      ts: '',
      stage: 'done',
    })
  })

  it('rejects malformed JSON and non-object payloads', () => {
    expect(parseStageEvent('{not json')).toBeNull()
    expect(parseStageEvent('[]')).toBeNull()
    expect(parseStageEvent('null')).toBeNull()
  })
})

describe('URL helpers', () => {
  it('builds same-origin URLs under /api and percent-encodes ids', () => {
    expect(runEventsUrl('abc')).toBe('/api/run/abc/events')
    expect(reportDownloadUrl('abc')).toBe('/api/reports/abc')
    expect(runEventsUrl('a/b c')).toBe('/api/run/a%2Fb%20c/events')
    expect(reportDownloadUrl('a/b c')).toBe('/api/reports/a%2Fb%20c')
  })
})

describe('error classes', () => {
  it('ApiError carries the HTTP status', () => {
    const err = new ApiError('GET /api/history failed with HTTP 503', 503)
    expect(err).toBeInstanceOf(Error)
    expect(err.name).toBe('ApiError')
    expect(err.status).toBe(503)
  })

  it('RunRejectedError and ChatRejectedError carry reason and retry hint', () => {
    const run = new RunRejectedError({ reason: 'cooldown', retry_after_s: 30 })
    expect(run.name).toBe('RunRejectedError')
    expect(run.reason).toBe('cooldown')
    expect(run.retryAfterS).toBe(30)

    const chat = new ChatRejectedError({ reason: 'busy' })
    expect(chat.name).toBe('ChatRejectedError')
    expect(chat.reason).toBe('busy')
    expect(chat.retryAfterS).toBeUndefined()
  })
})

describe('startRun', () => {
  it('resolves with the accepted run id', async () => {
    mockFetch(new Response(JSON.stringify({ id: 'run-7' }), { status: 202 }))
    await expect(startRun()).resolves.toBe('run-7')
  })

  it('throws RunRejectedError with the retry hint on a 429 gate', async () => {
    mockFetch(new Response(JSON.stringify({ reason: 'cooldown', retry_after_s: 30 }), { status: 429 }))
    const err: unknown = await startRun().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(RunRejectedError)
    expect((err as RunRejectedError).reason).toBe('cooldown')
    expect((err as RunRejectedError).retryAfterS).toBe(30)
  })
})

describe('streamChat', () => {
  it('emits meta, delta and done events from SSE frames split across chunks', async () => {
    mockFetch(() =>
      sseResponse([
        'event: meta\ndata: {"served_by":"pool/a","model_id":"m1","model_name":null,"attempted_retries":0,"attempted_fallbacks":1,"latency_ms":12',
        '0}\n\ndata: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\ndata: [DONE]\n\n',
      ]),
    )

    const events: unknown[] = []
    await streamChat('hello', (event) => events.push(event))

    expect(events).toEqual([
      {
        type: 'meta',
        meta: {
          served_by: 'pool/a',
          model_id: 'm1',
          model_name: null,
          attempted_retries: 0,
          attempted_fallbacks: 1,
          latency_ms: 120,
        },
      },
      { type: 'delta', content: 'hi', reasoning: null },
      { type: 'delta', content: null, reasoning: 'thinking' },
      { type: 'done' },
    ])
  })

  it('surfaces in-stream error frames with the server reason', async () => {
    mockFetch(() =>
      sseResponse(['event: error\ndata: {"reason":"pools_busy","status":503}\n\n']),
    )

    const events: unknown[] = []
    await streamChat('hello', (event) => events.push(event))

    expect(events).toEqual([{ type: 'error', reason: 'pools_busy', status: 503 }])
  })

  it('throws ChatRejectedError on a gate rejection before any event', async () => {
    mockFetch(new Response(JSON.stringify({ reason: 'busy', retry_after_s: 5 }), { status: 429 }))

    const onEvent = vi.fn()
    const err: unknown = await streamChat('hello', onEvent).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ChatRejectedError)
    expect((err as ChatRejectedError).reason).toBe('busy')
    expect((err as ChatRejectedError).retryAfterS).toBe(5)
    expect(onEvent).not.toHaveBeenCalled()
  })
})
