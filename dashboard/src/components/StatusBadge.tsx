import type { ScenarioStatus } from '../lib/types'
import { CheckIcon, XIcon } from './Icons'

const styles: Record<ScenarioStatus, string> = {
  pass: 'border-ok bg-transparent text-ok',
  fail: 'border-bad bg-bad text-white',
  error: 'border-amber bg-amber text-ink',
}

export function StatusBadge({ status }: { status: ScenarioStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 border px-2.5 py-0.5 font-mono text-[11px] tracking-wider uppercase ${styles[status]}`}
    >
      {status === 'pass' ? <CheckIcon className="h-3 w-3" /> : <XIcon className="h-3 w-3" />}
      {status}
    </span>
  )
}
