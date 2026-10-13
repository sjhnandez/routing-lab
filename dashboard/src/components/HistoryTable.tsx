import { reportDownloadUrl } from '../lib/api'
import { formatSeconds, formatUtc } from '../lib/format'
import type { HistoryEntry } from '../lib/types'
import { buttonGhost, card, cardGlow, spinnerAccent } from '../lib/ui'
import { DownloadIcon, RefreshIcon } from './Icons'

interface HistoryTableProps {
  entries: HistoryEntry[]
  loading: boolean
  error: string | null
  /** run_id currently shown in the results view, if any. */
  viewingRunId: string | null
  /** run_id currently being fetched by a View click. */
  loadingRunId: string | null
  onView: (runId: string) => void
  onRetry: () => void
}

export function HistoryTable({
  entries,
  loading,
  error,
  viewingRunId,
  loadingRunId,
  onView,
  onRetry,
}: HistoryTableProps) {
  return (
    <div className={`${card} ${cardGlow}`}>
      {loading && entries.length === 0 ? (
        <div className="animate-pulse p-6 sm:p-8" role="status" aria-label="Loading run history">
          <div className="space-y-2">
            {Array.from({ length: 4 }, (_, index) => (
              <div key={index} className="h-8 rounded bg-line/40" />
            ))}
          </div>
        </div>
      ) : entries.length === 0 && error ? (
        <div className="p-6 sm:p-8">
          <p className="text-sm text-bad">{error}</p>
          <button type="button" onClick={onRetry} className={`${buttonGhost} mt-4 px-3.5 py-1.5`}>
            <RefreshIcon className="h-3.5 w-3.5" />
            Retry
          </button>
        </div>
      ) : entries.length === 0 ? (
        <div className="p-6 sm:p-8">
          <p className="text-sm text-muted">No runs recorded yet.</p>
          <p className="mt-1 text-xs text-dim">
            Completed batteries appear here, newest first, with a JSON download per row.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse text-sm">
            <thead>
              <tr className="text-left font-mono text-[11px] tracking-wider text-dim uppercase">
                <th scope="col" className="px-6 py-3 font-normal sm:px-8">
                  Started
                </th>
                <th scope="col" className="px-3 py-3 font-normal">
                  Profile
                </th>
                <th scope="col" className="px-3 py-3 font-normal">
                  Passed
                </th>
                <th scope="col" className="px-3 py-3 font-normal">
                  Duration
                </th>
                <th scope="col" className="px-3 py-3 pr-6 text-right font-normal sm:pr-8">
                  Report
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => {
                const current = entry.run_id === viewingRunId
                const loadingThis = entry.run_id === loadingRunId
                return (
                  <tr
                    key={entry.run_id}
                    className={`border-t border-line/40 transition hover:bg-white/5 ${
                      current ? 'bg-accent/5' : ''
                    }`}
                  >
                    <td className="px-6 py-3 font-mono text-xs text-muted sm:px-8">
                      {formatUtc(entry.started_at)}
                    </td>
                    <td className="px-3 py-3">
                      <span className="rounded-full border border-line px-2.5 py-0.5 font-mono text-[11px] text-dim">
                        {entry.profile}
                      </span>
                    </td>
                    <td className="px-3 py-3 font-mono text-xs">
                      <span
                        className={
                          entry.summary.status === 'pass' ? 'text-ok' : 'text-bad'
                        }
                      >
                        {entry.summary.passed}/{entry.summary.total}
                      </span>
                    </td>
                    <td className="px-3 py-3 font-mono text-xs text-muted">
                      {formatSeconds(entry.duration_s)}
                    </td>
                    <td className="px-3 py-3 pr-6 sm:pr-8">
                      <span className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => onView(entry.run_id)}
                          disabled={loadingThis}
                          aria-current={current ? 'true' : undefined}
                          className={`${buttonGhost} px-3 py-1 text-xs ${
                            current ? 'border-accent/50 text-ink' : ''
                          }`}
                        >
                          {loadingThis ? <span className={`h-3 w-3 ${spinnerAccent}`} /> : null}
                          {current ? 'Viewing' : 'View'}
                        </button>
                        <a
                          href={reportDownloadUrl(entry.run_id)}
                          download={`${entry.run_id}.json`}
                          aria-label={`Download ${entry.run_id} JSON`}
                          className={`${buttonGhost} px-3 py-1 text-xs`}
                        >
                          <DownloadIcon className="h-3.5 w-3.5" />
                          JSON
                        </a>
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {error ? (
            <p className="border-t border-line/40 px-6 py-2 text-xs text-warn sm:px-8">{error}</p>
          ) : null}
        </div>
      )}
    </div>
  )
}
