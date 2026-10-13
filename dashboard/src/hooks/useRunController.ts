import { useCallback, useEffect, useRef, useState } from 'react'
import {
    ApiError,
    fetchHealth,
    parseStageEvent,
    runEventsUrl,
    RunRejectedError,
    startRun,
} from '../lib/api'
import { errorMessage } from '../lib/format'
import type { LabHealth, StageEvent } from '../lib/types'

/** Lifecycle of the run button. */
export type RunPhase =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'live'; runId: string }
  | { kind: 'cooldown'; reason: 'cooldown' | 'rate_limited'; seconds: number; endsAt: number }
  | { kind: 'blocked'; reason: 'trigger_disabled' | 'run_in_progress' }

export type StepState = 'pending' | 'running' | 'done' | 'failed'
export type ScenarioState = 'pending' | 'running' | 'pass' | 'fail'

export interface ScenarioSlot {
  id: number
  name: string | null
  state: ScenarioState
}

/** Live-stage progress driven purely by SSE events. */
export interface RunProgress {
  visible: boolean
  runId: string | null
  queued: StepState
  boot: StepState
  scenarios: ScenarioSlot[]
  teardown: StepState
  done: StepState
  errorMessage: string | null
}

const SCENARIO_COUNT = 8
/** Used when the server omits retry_after_s on a 429. */
const DEFAULT_COOLDOWN_S = 10
const HEALTH_POLL_MS = 5000
/** Give the browser a few reconnects (the stream replays from the start) before giving up. */
const MAX_STREAM_ERRORS = 3

function initialProgress(visible: boolean): RunProgress {
  return {
    visible,
    runId: null,
    queued: 'pending',
    boot: 'pending',
    scenarios: Array.from({ length: SCENARIO_COUNT }, (_, index) => ({
      id: index + 1,
      name: null,
      state: 'pending' as const,
    })),
    teardown: 'pending',
    done: 'pending',
    errorMessage: null,
  }
}

function applyStageEvent(progress: RunProgress, event: StageEvent): RunProgress {
  switch (event.stage) {
    case 'queued':
      return { ...progress, visible: true, queued: 'running' }
    case 'boot':
      return { ...progress, visible: true, queued: 'done', boot: 'running' }
    case 'scenario': {
      const scenarios = progress.scenarios.map((slot) => {
        if (slot.id !== event.scenario) return slot
        const state: ScenarioState =
          event.status === 'pass' ? 'pass' : event.status === 'fail' ? 'fail' : 'running'
        return { ...slot, name: event.name ?? slot.name, state }
      })
      return { ...progress, visible: true, queued: 'done', boot: 'done', scenarios }
    }
    case 'teardown':
      return { ...progress, visible: true, queued: 'done', boot: 'done', teardown: 'running' }
    case 'done':
      return {
        ...progress,
        visible: true,
        queued: 'done',
        boot: 'done',
        teardown: 'done',
        done: 'done',
      }
    case 'error':
      return {
        ...progress,
        visible: true,
        done: 'failed',
        errorMessage: event.message ?? 'the run reported an error',
      }
  }
}

interface UseRunControllerOptions {
  /** Called on a terminal SSE stage (done/error) or when the stream drops. */
  onRunFinished: () => void
}

/** Drives POST /api/run, the SSE stage stream and the /api/health readout. */
export function useRunController({ onRunFinished }: UseRunControllerOptions) {
  const [phase, setPhase] = useState<RunPhase>({ kind: 'idle' })
  const [progress, setProgress] = useState<RunProgress>(() => initialProgress(false))
  const [error, setError] = useState<string | null>(null)
  const [health, setHealth] = useState<LabHealth | null>(null)
  const [healthFailed, setHealthFailed] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const sourceRef = useRef<EventSource | null>(null)
  const terminalRef = useRef(false)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      sourceRef.current?.close()
    }
  }, [])

  const loadHealth = useCallback(async () => {
    try {
      const next = await fetchHealth()
      if (!mountedRef.current) return
      setHealth(next)
      setHealthFailed(false)
    } catch {
      if (!mountedRef.current) return
      setHealthFailed(true)
    }
  }, [])

  useEffect(() => {
    void loadHealth()
    const timer = window.setInterval(() => void loadHealth(), HEALTH_POLL_MS)
    return () => window.clearInterval(timer)
  }, [loadHealth])

  // Reflect a disabled server-side trigger in the button state.
  useEffect(() => {
    if (health && phase.kind === 'idle' && !health.trigger_enabled) {
      setPhase({ kind: 'blocked', reason: 'trigger_disabled' })
    }
  }, [health, phase])

  // Re-enable as soon as the server reports we can run again.
  useEffect(() => {
    if (phase.kind !== 'blocked' || !health) return
    if (phase.reason === 'trigger_disabled') {
      if (health.trigger_enabled) setPhase({ kind: 'idle' })
      return
    }
    const queueBusy = health.queue.running > 0 || health.queue.pending > 0
    if (health.trigger_enabled && !queueBusy) setPhase({ kind: 'idle' })
  }, [phase, health])

  // Tick the cooldown countdown.
  useEffect(() => {
    if (phase.kind !== 'cooldown') return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [phase])

  const cooldownRemainingS =
    phase.kind === 'cooldown' ? Math.max(0, Math.ceil((phase.endsAt - now) / 1000)) : null

  useEffect(() => {
    if (phase.kind === 'cooldown' && cooldownRemainingS === 0) {
      setPhase({ kind: 'idle' })
    }
  }, [phase, cooldownRemainingS])

  const start = useCallback(() => {
    setError(null)
    setPhase({ kind: 'starting' })
    void (async () => {
      try {
        const id = await startRun()
        if (!mountedRef.current) return
        terminalRef.current = false
        setProgress({ ...initialProgress(true), runId: id })
        setPhase({ kind: 'live', runId: id })

        const source = new EventSource(runEventsUrl(id))
        sourceRef.current = source
        let streamErrors = 0

        source.onmessage = (message) => {
          if (!mountedRef.current) return
          const event = parseStageEvent(String(message.data))
          if (!event) return
          streamErrors = 0
          setProgress((prev) => applyStageEvent(prev, event))
          if (event.stage === 'done' || event.stage === 'error') {
            terminalRef.current = true
            source.close()
            sourceRef.current = null
            setPhase({ kind: 'idle' })
            onRunFinished()
          }
        }

        source.onerror = () => {
          if (!mountedRef.current || terminalRef.current) return
          streamErrors += 1
          if (source.readyState === EventSource.CLOSED || streamErrors >= MAX_STREAM_ERRORS) {
            terminalRef.current = true
            source.close()
            sourceRef.current = null
            setError('the live event stream dropped: showing the latest report instead')
            setPhase({ kind: 'idle' })
            onRunFinished()
          }
          // While CONNECTING the browser reconnects on its own; the server
          // replays every event from the start, so the progress state converges.
        }
      } catch (err) {
        if (!mountedRef.current) return
        if (err instanceof RunRejectedError) {
          if (err.reason === 'cooldown' || err.reason === 'rate_limited') {
            const seconds = err.retryAfterS ?? DEFAULT_COOLDOWN_S
            setPhase({
              kind: 'cooldown',
              reason: err.reason,
              seconds,
              endsAt: Date.now() + seconds * 1000,
            })
          } else {
            setPhase({ kind: 'blocked', reason: err.reason })
            void loadHealth()
          }
        } else {
          setPhase({ kind: 'idle' })
          setError(
            err instanceof ApiError
              ? err.message
              : `unable to reach the lab API: ${errorMessage(err)}`,
          )
        }
      }
    })()
  }, [loadHealth, onRunFinished])

  const dismissError = useCallback(() => setError(null), [])

  return {
    phase,
    progress,
    error,
    dismissError,
    health,
    healthFailed,
    cooldownRemainingS,
    start,
  }
}
