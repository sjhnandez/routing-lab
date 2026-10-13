import { motion } from 'motion/react'
import type { RunProgress, ScenarioSlot, StepState } from '../hooks/useRunController'
import { spinnerAccent } from '../lib/ui'
import { CheckIcon, XIcon } from './Icons'

type IconKind = 'pending' | 'running' | 'ok' | 'bad'

function StateIcon({ kind }: { kind: IconKind }) {
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center">
      <motion.span
        key={kind}
        initial={{ opacity: 0, scale: 0.7 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.18, ease: 'easeOut' }}
        className="flex items-center justify-center"
      >
        {kind === 'running' ? (
          <span className={`h-3.5 w-3.5 ${spinnerAccent}`} />
        ) : kind === 'ok' ? (
          <CheckIcon className="h-4 w-4 text-ok" />
        ) : kind === 'bad' ? (
          <XIcon className="h-4 w-4 text-bad" />
        ) : (
          <span className="h-2 w-2 rounded-full border border-line" />
        )}
      </motion.span>
    </span>
  )
}

const stepText: Record<StepState, string> = {
  pending: 'text-dim',
  running: 'text-accent',
  done: 'text-ok',
  failed: 'text-bad',
}

function stepKind(state: StepState): IconKind {
  return state === 'done' ? 'ok' : state === 'failed' ? 'bad' : state
}

function StepRow({ label, state }: { label: string; state: StepState }) {
  return (
    <div className="flex items-center gap-3 px-2 py-1.5">
      <StateIcon kind={stepKind(state)} />
      <span className="text-sm text-muted">{label}</span>
      <span className={`ml-auto font-mono text-[11px] ${stepText[state]}`}>{state}</span>
    </div>
  )
}

const scenarioText: Record<ScenarioSlot['state'], string> = {
  pending: 'text-dim',
  running: 'text-accent',
  pass: 'text-ok',
  fail: 'text-bad',
}

function scenarioKind(state: ScenarioSlot['state']): IconKind {
  return state === 'pass' ? 'ok' : state === 'fail' ? 'bad' : state
}

function ScenarioRow({ slot }: { slot: ScenarioSlot }) {
  return (
    <div className="flex items-center gap-3 px-2 py-1.5">
      <StateIcon kind={scenarioKind(slot.state)} />
      <span className="font-mono text-xs text-dim">S{slot.id}</span>
      <span className="min-w-0 truncate text-sm text-muted">{slot.name ?? ''}</span>
      <span className={`ml-auto font-mono text-[11px] ${scenarioText[slot.state]}`}>
        {slot.state}
      </span>
    </div>
  )
}

interface LiveStagesProps {
  progress: RunProgress
  live: boolean
}

/** Staged progress for the active run, driven entirely by SSE events. */
export function LiveStages({ progress, live }: LiveStagesProps) {
  const total = progress.scenarios.length
  const resolved = progress.scenarios.filter(
    (slot) => slot.state === 'pass' || slot.state === 'fail',
  ).length
  const finished = progress.done === 'done'
  const failed = progress.done === 'failed'

  let statusLabel = 'waiting for events'
  if (failed) statusLabel = 'failed'
  else if (finished) statusLabel = 'done: results refreshed below'
  else if (live) statusLabel = 'running'

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.35, ease: 'easeOut' }}
      className="mt-6 rounded-xl border border-line bg-bg-soft/70 p-4 sm:p-5"
    >
      <div className="flex items-center gap-2">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            live
              ? 'animate-pulse bg-accent motion-reduce:animate-none'
              : failed
                ? 'bg-bad'
                : finished
                  ? 'bg-ok'
                  : 'bg-dim'
          }`}
        />
        <h3 className="font-display text-sm font-semibold">Live stages</h3>
        {progress.runId ? (
          <span className="hidden font-mono text-[11px] text-dim sm:inline">{progress.runId}</span>
        ) : null}
        <span className="ml-auto font-mono text-[11px] text-dim" aria-live="polite">
          {statusLabel}
        </span>
      </div>

      <div className="mt-3 h-1 overflow-hidden rounded-full bg-line/60">
        <div
          className="h-full rounded-full bg-linear-to-r from-accent to-accent2 transition-[width] duration-500 motion-reduce:transition-none"
          style={{ width: `${finished ? 100 : Math.round((resolved / total) * 100)}%` }}
        />
      </div>

      <div className="mt-3 space-y-0.5">
        <StepRow label="Queued" state={progress.queued} />
        <StepRow label="Booting lab" state={progress.boot} />
        <div className="my-1 border-t border-line/50" />
        {progress.scenarios.map((slot) => (
          <ScenarioRow key={slot.id} slot={slot} />
        ))}
        <div className="my-1 border-t border-line/50" />
        <StepRow label="Teardown" state={progress.teardown} />
        <StepRow label={failed ? 'Failed' : 'Done'} state={progress.done} />
      </div>

      {progress.errorMessage ? (
        <p className="mt-3 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-xs leading-relaxed text-bad">
          {progress.errorMessage}
        </p>
      ) : null}
    </motion.div>
  )
}
