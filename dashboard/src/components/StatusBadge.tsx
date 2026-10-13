import type { ScenarioStatus } from '../lib/types'
import { CheckIcon, XIcon } from './Icons'

const styles: Record<ScenarioStatus, string> = {
  pass: 'border-ok/40 bg-ok/10 text-ok',
  fail: 'border-bad/40 bg-bad/10 text-bad',
  error: 'border-warn/40 bg-warn/10 text-warn',
}

export function StatusBadge({ status }: { status: ScenarioStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[11px] tracking-wider uppercase ${styles[status]}`}
    >
      {status === 'pass' ? <CheckIcon className="h-3 w-3" /> : <XIcon className="h-3 w-3" />}
      {status}
    </span>
  )
}
