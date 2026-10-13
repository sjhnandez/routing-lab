import { useCallback, useEffect, useRef, useState } from 'react'
import { ChatRejectedError, streamChat } from '../lib/api'
import { errorMessage } from '../lib/format'
import type { ChatMeta, ChatStreamErrorReason } from '../lib/types'

/** One question plus the reply it produced (transcript is client-side only). */
export interface ChatTurn {
  id: number
  question: string
  /** Visible answer accumulated from `content` deltas. */
  answer: string
  /** Dimmed "thinking" text accumulated from `reasoning_content` deltas. */
  reasoning: string
  /** The stream ended with reasoning text but no visible answer (token cap). */
  reasoningCapped: boolean
  /** Terminal `event: meta` attribution, when the stream finished cleanly. */
  meta: ChatMeta | null
  /** Friendly failure/limit note; stands in for the answer when nothing arrived. */
  notice: string | null
}

/** 429 countdown gate (rate limit window or transient busy slots). */
export interface ChatCooldown {
  reason: 'rate_limited' | 'busy'
  endsAt: number
}

/** Used when the server omits retry_after_s on a 429. */
const BUSY_FALLBACK_S = 5
const RATE_FALLBACK_S = 60

/** Friendly copy for a POST /api/chat gate rejection. */
function rejectionNotice(rejection: ChatRejectedError): string {
  switch (rejection.reason) {
    case 'message_empty':
      return 'write a message first.'
    case 'message_too_long':
      return 'that message is over the 400-character cap.'
    case 'rate_limited':
      return "you've reached the free-tier limit — 5 messages / 10 min per visitor."
    case 'busy':
      return 'the demo is busy right now — try again in a moment.'
    case 'chat_disabled':
      return 'chat is paused.'
    case 'router_key_missing':
      return 'chat is paused — the router key is not configured.'
  }
}

/** Friendly copy for an in-stream `event: error` frame. */
function streamErrorNotice(reason: ChatStreamErrorReason): string {
  switch (reason) {
    case 'pools_busy':
      return 'all free pools are cooling down — try again in a moment.'
    case 'router_timeout':
      return 'the router timed out before the reply finished — try again.'
    case 'router_error':
      return 'the router returned an error — try again.'
  }
}

/** Drives POST /api/chat streaming and the transcript of the "Try it" card. */
export function useChat() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [streaming, setStreaming] = useState(false)
  const [cooldown, setCooldown] = useState<ChatCooldown | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const mountedRef = useRef(true)
  const inflightRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)
  const nextIdRef = useRef(1)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  // Tick the 429 countdown, mirroring the RunPanel cooldown pattern.
  useEffect(() => {
    if (cooldown === null) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [cooldown])

  const cooldownRemainingS =
    cooldown === null ? null : Math.max(0, Math.ceil((cooldown.endsAt - now) / 1000))

  useEffect(() => {
    if (cooldown !== null && cooldownRemainingS === 0) setCooldown(null)
  }, [cooldown, cooldownRemainingS])

  const patchTurn = useCallback((id: number, patch: (turn: ChatTurn) => ChatTurn) => {
    setTurns((prev) => prev.map((turn) => (turn.id === id ? patch(turn) : turn)))
  }, [])

  /** Send one message; returns false when a send is already in flight. */
  const send = useCallback(
    (raw: string): boolean => {
      const question = raw.trim()
      if (question === '' || inflightRef.current) return false
      inflightRef.current = true

      const id = nextIdRef.current
      nextIdRef.current += 1
      setTurns((prev) => [
        ...prev,
        {
          id,
          question,
          answer: '',
          reasoning: '',
          reasoningCapped: false,
          meta: null,
          notice: null,
        },
      ])
      setStreaming(true)

      const controller = new AbortController()
      abortRef.current = controller

      void streamChat(
        question,
        (event) => {
          if (!mountedRef.current) return
          switch (event.type) {
            case 'delta':
              patchTurn(id, (turn) => ({
                ...turn,
                answer: turn.answer + (event.content ?? ''),
                reasoning: turn.reasoning + (event.reasoning ?? ''),
              }))
              break
            case 'meta':
              patchTurn(id, (turn) => ({ ...turn, meta: event.meta }))
              break
            case 'error':
              patchTurn(id, (turn) => ({ ...turn, notice: streamErrorNotice(event.reason) }))
              break
            case 'done':
              break
          }
        },
        controller.signal,
      )
        .then(() => {
          if (!mountedRef.current) return
          patchTurn(id, (turn) => {
            if (turn.notice !== null) return turn
            // Reasoning-only stream: the token cap hit while the model was
            // still thinking, so there is no answer text to show.
            if (turn.answer === '' && turn.reasoning !== '') {
              return { ...turn, reasoningCapped: true }
            }
            if (turn.answer === '' && turn.reasoning === '') {
              return { ...turn, notice: 'the model returned no text — try again.' }
            }
            return turn
          })
        })
        .catch((err: unknown) => {
          if (!mountedRef.current) return
          if (err instanceof ChatRejectedError) {
            patchTurn(id, (turn) => ({ ...turn, notice: rejectionNotice(err) }))
            if (err.reason === 'rate_limited' || err.reason === 'busy') {
              const fallback = err.reason === 'busy' ? BUSY_FALLBACK_S : RATE_FALLBACK_S
              const seconds = err.retryAfterS ?? fallback
              setCooldown({ reason: err.reason, endsAt: Date.now() + seconds * 1000 })
            }
            return
          }
          patchTurn(id, (turn) => ({
            ...turn,
            notice: `the demo is unavailable — ${errorMessage(err)}`,
          }))
        })
        .finally(() => {
          abortRef.current = null
          if (!mountedRef.current) return
          inflightRef.current = false
          setStreaming(false)
        })

      return true
    },
    [patchTurn],
  )

  return { turns, streaming, cooldownReason: cooldown?.reason ?? null, cooldownRemainingS, send }
}
