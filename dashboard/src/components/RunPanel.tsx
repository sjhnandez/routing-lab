import { AnimatePresence } from 'motion/react'
import type { RunPhase, RunProgress } from '../hooks/useRunController'
import type { LabHealth } from '../lib/types'
import { buttonSolid, card, cardGlow, spinnerOnGradient } from '../lib/ui'
import { PlayIcon, XIcon } from './Icons'
import { LiveStages } from './LiveStages'
import { Reveal } from './Reveal'

interface RunPanelProps {
  phase: RunPhase
  cooldownRemainingS: number | null
  health: LabHealth | null
  healthFailed: boolean
  error: string | null
  onDismissError: () => void
  onRun: () => void
  progress: RunProgress
}

function phaseNote(phase: RunPhase, cooldownRemainingS: number | null): string {
  switch (phase.kind) {
    case 'idle':
      return 'Ready to run the battery.'
    case 'starting':
      return 'Requesting a run slot…'
    case 'live':
      return 'Run in progress — stages stream in below.'
    case 'cooldown':
      return `${phase.reason === 'rate_limited' ? 'Rate limited' : 'Cooling down'} — retry in ${cooldownRemainingS ?? phase.seconds}s.`
    case 'blocked':
      return phase.reason === 'trigger_disabled'
        ? 'The run trigger is disabled on the server.'
        : 'A run is already in progress — the button re-enables when it finishes.'
  }
}

export function RunPanel({
  phase,
  cooldownRemainingS,
  health,
  healthFailed,
  error,
  onDismissError,
  onRun,
  progress,
}: RunPanelProps) {
  const busy = phase.kind === 'starting' || phase.kind === 'live'
  const disabled = phase.kind !== 'idle'

  const healthDot = healthFailed
    ? 'bg-bad'
    : health
      ? health.trigger_enabled
        ? 'bg-ok'
        : 'bg-warn'
      : 'bg-dim'

  const healthText = healthFailed
    ? 'lab API unreachable'
    : health
      ? `${health.status} · trigger ${health.trigger_enabled ? 'enabled' : 'disabled'} · queue ${health.queue.running} running / ${health.queue.pending} pending`
      : 'checking lab status…'

  return (
    <section id="run" className="scroll-mt-20">
      <div className="mx-auto max-w-5xl px-5 pt-10 sm:px-8 sm:pt-12">
        <Reveal>
          <div className={`${card} ${cardGlow} p-6 sm:p-8`}>
            <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
              <div className="max-w-xl">
                <h2 className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
                  Run the battery
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-muted">
                  One click runs the eight-scenario battery against the pinned LiteLLM image.
                  Stages stream live below; the report lands in Results.
                </p>
                <div className="mt-4 flex items-center gap-2 font-mono text-[11px] text-dim">
                  <span className={`h-1.5 w-1.5 rounded-full ${healthDot}`} />
                  <span>{healthText}</span>
                </div>
              </div>

              <div className="flex shrink-0 flex-col items-start gap-3 lg:items-end">
                <button
                  type="button"
                  onClick={onRun}
                  disabled={disabled}
                  aria-busy={busy}
                  className={`${buttonSolid} px-6 py-3 text-sm`}
                >
                  {busy ? (
                    <span className={`h-4 w-4 ${spinnerOnGradient}`} />
                  ) : (
                    <PlayIcon className="h-4 w-4" />
                  )}
                  {phase.kind === 'idle' && 'Run experiment'}
                  {phase.kind === 'starting' && 'Starting…'}
                  {phase.kind === 'live' && 'Running…'}
                  {phase.kind === 'cooldown' &&
                    `Cooling down — ${cooldownRemainingS ?? phase.seconds}s`}
                  {phase.kind === 'blocked' && 'Run experiment'}
                </button>
                <p className="text-xs text-dim lg:text-right" aria-live="polite">
                  {phaseNote(phase, cooldownRemainingS)}
                </p>
              </div>
            </div>

            {error ? (
              <div className="mt-5 flex items-start gap-3 rounded-xl border border-bad/40 bg-bad/10 px-4 py-3">
                <p className="min-w-0 flex-1 text-sm text-bad">{error}</p>
                <button
                  type="button"
                  onClick={onDismissError}
                  aria-label="Dismiss error"
                  className="text-bad/80 transition hover:text-bad"
                >
                  <XIcon className="h-4 w-4" />
                </button>
              </div>
            ) : null}

            <AnimatePresence initial={false}>
              {progress.visible ? (
                <LiveStages key="live-stages" progress={progress} live={phase.kind === 'live'} />
              ) : null}
            </AnimatePresence>
          </div>
        </Reveal>
      </div>
    </section>
  )
}
