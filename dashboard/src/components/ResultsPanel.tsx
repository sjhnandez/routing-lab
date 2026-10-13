import { useEffect, useState } from 'react'
import { reportDownloadUrl } from '../lib/api'
import { formatSeconds, formatUtc, shortImageDigest } from '../lib/format'
import type { Report, ScenarioReport } from '../lib/types'
import { buttonGhost, card, cardGlow, chip } from '../lib/ui'
import { CheckIcon, ChevronDownIcon, DownloadIcon, RefreshIcon, XIcon } from './Icons'
import { Section } from './Section'
import { StatusBadge } from './StatusBadge'

interface ResultsPanelProps {
  report: Report | null
  /** True when the shown report was opened from the history table. */
  historical: boolean
  /** Initial load from /api/last-run. */
  loading: boolean
  error: string | null
  onBackToLatest: () => void
  onRetry: () => void
}

export function ResultsPanel({
  report,
  historical,
  loading,
  error,
  onBackToLatest,
  onRetry,
}: ResultsPanelProps) {
  return (
    <Section id="results" index="01" title="Results">
      <div className={`${card} ${cardGlow}`}>
        {loading && !report ? (
          <ResultsSkeleton />
        ) : !report && error ? (
          <ErrorState message={error} onRetry={onRetry} />
        ) : !report ? (
          <EmptyState />
        ) : (
          <ReportView
            report={report}
            historical={historical}
            error={error}
            onBackToLatest={onBackToLatest}
          />
        )}
      </div>
    </Section>
  )
}

function ReportView({
  report,
  historical,
  error,
  onBackToLatest,
}: {
  report: Report
  historical: boolean
  error: string | null
  onBackToLatest: () => void
}) {
  const [openId, setOpenId] = useState<number | null>(null)

  useEffect(() => {
    setOpenId(null)
  }, [report.run_id])

  const allPass = report.summary.passed === report.summary.total

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4 p-6 sm:p-8">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-mono text-sm text-ink">{report.run_id}</h3>
            <span className={chip}>
              <span className="text-dim">{historical ? 'history' : 'latest'}</span>
            </span>
          </div>
          <p className="mt-1.5 font-mono text-[11px] text-dim">
            started {formatUtc(report.started_at)} · finished {formatUtc(report.finished_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {historical ? (
            <button
              type="button"
              onClick={onBackToLatest}
              className={`${buttonGhost} px-3.5 py-1.5`}
            >
              <RefreshIcon className="h-3.5 w-3.5" />
              Back to latest
            </button>
          ) : null}
          <a
            href={reportDownloadUrl(report.run_id)}
            download={`${report.run_id}.json`}
            className={`${buttonGhost} px-3.5 py-1.5`}
          >
            <DownloadIcon className="h-3.5 w-3.5" />
            Download JSON
          </a>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-6 pb-6 sm:px-8">
        <span className={`${chip} ${allPass ? 'border-ok/40' : 'border-bad/40'}`}>
          <span className="text-dim">passed</span>
          <span className={allPass ? 'text-ok' : 'text-bad'}>
            {report.summary.passed}/{report.summary.total}
          </span>
        </span>
        <span className={chip}>
          <span className="text-dim">duration</span>
          <span className="text-ink">{formatSeconds(report.duration_s)}</span>
        </span>
        <span className={chip}>
          <span className="text-dim">profile</span>
          <span className="text-ink">{report.profile}</span>
        </span>
        <span className={chip} title={report.lab.litellm_image}>
          <span className="text-dim">image</span>
          <span className="text-ink">{shortImageDigest(report.lab.litellm_image)}</span>
        </span>
      </div>

      {error ? (
        <p className="border-t border-line/40 px-6 py-2.5 text-xs text-warn sm:px-8">{error}</p>
      ) : null}

      <div className="overflow-x-auto border-t border-line/40">
        <table className="w-full min-w-[34rem] border-collapse text-sm">
          <thead>
            <tr className="bg-bg-soft/60 text-left font-mono text-[11px] tracking-wider text-dim uppercase">
              <th scope="col" className="px-6 py-2.5 font-normal sm:px-8">
                Scenario
              </th>
              <th scope="col" className="px-3 py-2.5 font-normal">
                Status
              </th>
              <th scope="col" className="px-3 py-2.5 font-normal">
                Duration
              </th>
              <th scope="col" className="px-3 py-2.5 pr-6 font-normal sm:pr-8">
                Served by
              </th>
            </tr>
          </thead>
          <tbody>
            {report.scenarios.map((scenario) => (
              <ScenarioRow
                key={scenario.id}
                scenario={scenario}
                open={openId === scenario.id}
                onToggle={() => setOpenId(openId === scenario.id ? null : scenario.id)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function ScenarioRow({
  scenario,
  open,
  onToggle,
}: {
  scenario: ScenarioReport
  open: boolean
  onToggle: () => void
}) {
  return (
    <>
      <tr className="border-t border-line/40 transition hover:bg-white/5">
        <td className="px-6 py-3 sm:px-8">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            aria-controls={`scenario-${scenario.id}-detail`}
            className="flex w-full items-center gap-3 text-left"
          >
            <ChevronDownIcon
              className={`h-3.5 w-3.5 shrink-0 text-dim transition-transform motion-reduce:transition-none ${
                open ? '' : '-rotate-90'
              }`}
            />
            <span className="font-mono text-xs text-dim">S{scenario.id}</span>
            <span className="min-w-0 truncate text-ink">{scenario.name}</span>
          </button>
        </td>
        <td className="px-3 py-3">
          <StatusBadge status={scenario.status} />
        </td>
        <td className="px-3 py-3 font-mono text-xs text-muted">
          {formatSeconds(scenario.duration_s)}
        </td>
        <td className="px-3 py-3 pr-6 sm:pr-8">
          <span className="flex flex-wrap gap-1.5">
            {scenario.served_by.map((served) => (
              <span
                key={served.mode}
                className="rounded-full border border-line px-2 py-0.5 font-mono text-[11px] text-dim"
              >
                {served.mode} ×{served.hits}
              </span>
            ))}
          </span>
        </td>
      </tr>

      {open ? (
        <tr id={`scenario-${scenario.id}-detail`} className="border-t border-line/40 bg-bg-soft/50">
          <td colSpan={4} className="px-6 py-5 sm:px-8">
            <div className="grid gap-5 md:grid-cols-2">
              <div>
                <p className="font-mono text-[11px] tracking-wider text-dim uppercase">Expected</p>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{scenario.expected}</p>
              </div>
              <div>
                <p className="font-mono text-[11px] tracking-wider text-dim uppercase">Observed</p>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{scenario.observed}</p>
              </div>
            </div>

            <div className="mt-5">
              <p className="font-mono text-[11px] tracking-wider text-dim uppercase">Assertions</p>
              <ul className="mt-2 space-y-2">
                {scenario.assertions.map((assertion, index) => (
                  <li key={index} className="flex gap-2.5">
                    {assertion.pass ? (
                      <CheckIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" />
                    ) : (
                      <XIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-bad" />
                    )}
                    <div className="min-w-0">
                      <p className="text-sm text-ink">{assertion.desc}</p>
                      {assertion.detail ? (
                        <p className="mt-0.5 font-mono text-[11px] leading-relaxed text-dim">
                          {assertion.detail}
                        </p>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            <div className="mt-5">
              <p className="font-mono text-[11px] tracking-wider text-dim uppercase">Timeline</p>
              {scenario.timeline.length === 0 ? (
                <p className="mt-2 text-xs text-dim">no timeline events recorded</p>
              ) : (
                <ol className="mt-2 space-y-1">
                  {scenario.timeline.map((entry, index) => (
                    <li key={index} className="flex gap-3 font-mono text-[11px] leading-relaxed">
                      <span className="w-14 shrink-0 text-right text-accent">
                        {entry.t.toFixed(3)}s
                      </span>
                      <span className="w-28 shrink-0 truncate text-ink sm:w-44" title={entry.event}>
                        {entry.event}
                      </span>
                      <span className="min-w-0 flex-1 text-dim">{entry.detail}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  )
}

function ResultsSkeleton() {
  return (
    <div className="animate-pulse p-6 sm:p-8" role="status" aria-label="Loading latest report">
      <div className="h-4 w-56 rounded bg-line/70" />
      <div className="mt-3 h-3 w-40 rounded bg-line/50" />
      <div className="mt-6 flex flex-wrap gap-2">
        <div className="h-6 w-24 rounded-full bg-line/50" />
        <div className="h-6 w-24 rounded-full bg-line/50" />
        <div className="h-6 w-24 rounded-full bg-line/50" />
        <div className="h-6 w-24 rounded-full bg-line/50" />
      </div>
      <div className="mt-6 space-y-2">
        {Array.from({ length: 8 }, (_, index) => (
          <div key={index} className="h-8 rounded bg-line/40" />
        ))}
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="p-6 sm:p-8">
      <p className="text-sm text-muted">No runs recorded yet.</p>
      <p className="mt-1 text-xs text-dim">
        Click “Run experiment” above: the first report lands here when the battery finishes.
      </p>
    </div>
  )
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="p-6 sm:p-8">
      <p className="text-sm text-bad">{message}</p>
      <button type="button" onClick={onRetry} className={`${buttonGhost} mt-4 px-3.5 py-1.5`}>
        <RefreshIcon className="h-3.5 w-3.5" />
        Retry
      </button>
    </div>
  )
}
