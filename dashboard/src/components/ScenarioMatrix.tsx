import type { Report } from '../lib/types'

const cellTone: Record<string, string> = {
  pass: 'border-[#f6f8f7] bg-[#f6f8f7] text-carbon',
  fail: 'border-bad bg-bad text-white',
  error: 'border-amber bg-amber text-carbon',
}

/**
 * The hero readout: one lamp per scenario, lit from the latest real report.
 * Hollow lamps mean no result yet; nothing here is decorative or simulated.
 */
export function ScenarioMatrix({ report }: { report: Report | null }) {
  const slots = report?.scenarios.map((s) => ({ id: s.id, status: s.status as string })) ??
    Array.from({ length: 8 }, (_, index) => ({ id: index + 1, status: 'none' }))

  return (
    <div>
      <ol
        aria-label={
          report
            ? `Latest run: ${report.summary.passed} of ${report.summary.total} scenarios passed`
            : 'No run loaded yet'
        }
        className="grid grid-cols-4 gap-px sm:grid-cols-8"
      >
        {slots.map((slot) => (
          <li
            key={slot.id}
            className={`flex aspect-square items-end border p-1.5 font-mono text-[11px] ${
              cellTone[slot.status] ?? 'border-[#3a393d] text-[#b2b2b2]'
            }`}
          >
            S{slot.id}
          </li>
        ))}
      </ol>
      <p className="mt-3 font-mono text-[11px] text-[#b2b2b2]">
        {report
          ? `latest run · ${report.summary.passed}/${report.summary.total} passed`
          : 'latest run · waiting for report'}
      </p>
    </div>
  )
}
