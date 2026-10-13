import type { ReactNode } from 'react'
import { useCallback, useEffect, useState } from 'react'
import { fetchFleet } from '../lib/api'
import { errorMessage, formatUtc, formatUtcSeconds } from '../lib/format'
import type { FleetProbe, FleetSummary } from '../lib/types'
import { buttonGhost, card, cardGlow } from '../lib/ui'
import { RefreshIcon } from './Icons'
import { Section } from './Section'

const badgeBase =
  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[11px]'

const classTones: Record<string, string> = {
  ok: 'border-ok/40 bg-ok/10 text-ok',
  rate_limited: 'border-warn/40 bg-warn/10 text-warn',
  not_found: 'border-warn/40 bg-warn/10 text-warn',
  auth: 'border-warn/40 bg-warn/10 text-warn',
  other_4xx: 'border-warn/40 bg-warn/10 text-warn',
  unavailable: 'border-bad/40 bg-bad/10 text-bad',
  timeout: 'border-bad/40 bg-bad/10 text-bad',
  network: 'border-bad/40 bg-bad/10 text-bad',
}

function ClassBadge({ name, count }: { name: string; count?: number }) {
  const tone = classTones[name] ?? 'border-line bg-white/5 text-muted'
  return (
    <span className={`${badgeBase} ${tone}`}>
      {name}
      {count !== undefined ? <span className="opacity-75">{count}</span> : null}
    </span>
  )
}

function okRateTone(rate: number, total: number): string {
  if (total === 0) return 'text-dim'
  if (rate >= 0.9) return 'text-ok'
  if (rate >= 0.5) return 'text-warn'
  return 'text-bad'
}

function StatCard({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-bg-soft/60 p-5">
      <p className="font-mono text-[11px] tracking-wider text-dim uppercase">{label}</p>
      {children}
    </div>
  )
}

function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <h3 className="font-mono text-[11px] tracking-wider text-dim uppercase">{children}</h3>
  )
}

function RecentProbeRow({ probe }: { probe: FleetProbe }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line/30 px-4 py-1.5 font-mono text-xs first:border-t-0">
      <span className="text-dim">{formatUtcSeconds(probe.ts)}</span>
      <span className="min-w-0 flex-1 truncate text-muted">{probe.model ?? '-'}</span>
      {probe.class ? <ClassBadge name={probe.class} /> : null}
      <span className="text-dim">
        {typeof probe.http_status === 'number' ? `http ${probe.http_status}` : 'http n/a'}
      </span>
      <span className="w-16 text-right text-dim">
        {typeof probe.latency_ms === 'number' ? `${probe.latency_ms}ms` : '-'}
      </span>
    </li>
  )
}

function FleetEmptyState() {
  return (
    <div>
      <p className="text-sm text-muted">
        The production prober runs host-side after deployment: no probe data yet.
      </p>
      <p className="mt-1 text-xs text-dim">
        Once it runs, this tab shows today's probe budget, last-24h failure classes, per-model
        coverage and the most recent raw probes.
      </p>
    </div>
  )
}

function FleetContent({ fleet }: { fleet: FleetSummary }) {
  const { budget, last_24h: window24h, models, recent } = fleet
  const present = Object.entries(window24h.counts).filter(([, n]) => n > 0)
  const okPct = window24h.total > 0 ? Math.round(window24h.ok_rate * 100) : 0

  return (
    <div className="space-y-8">
      <div className="grid gap-4 sm:grid-cols-2">
        <StatCard label="Probes today">
          <p className="mt-2 font-mono text-3xl font-semibold tracking-tight">
            {budget.count}
            <span className="text-dim">/{budget.cap}</span>
          </p>
          <p className="mt-1 font-mono text-xs text-dim">UTC {budget.date}</p>
        </StatCard>
        <StatCard label="Last 24h">
          <p className="mt-2 font-mono text-3xl font-semibold tracking-tight">{window24h.total}</p>
          <p className="mt-1 font-mono text-xs">
            <span className="text-dim">ok {window24h.ok} · </span>
            <span className={okRateTone(window24h.ok_rate, window24h.total)}>{okPct}% ok</span>
          </p>
        </StatCard>
      </div>

      <div>
        <SectionHeading>Probe classes (last 24h)</SectionHeading>
        {present.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {present.map(([name, count]) => (
              <ClassBadge key={name} name={name} count={count} />
            ))}
          </div>
        ) : (
          <p className="mt-2 text-xs text-dim">No probes in the last 24h.</p>
        )}
      </div>

      <div>
        <SectionHeading>Models</SectionHeading>
        {models.length > 0 ? (
          <div className="mt-3 overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[36rem] border-collapse text-sm">
              <thead>
                <tr className="text-left font-mono text-[11px] tracking-wider text-dim uppercase">
                  <th scope="col" className="px-4 py-2.5 font-normal">
                    Model
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-normal">
                    Probes 24h
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-normal">
                    Ok 24h
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-normal">
                    Last class
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-normal">
                    Last probe
                  </th>
                </tr>
              </thead>
              <tbody>
                {models.map((row) => (
                  <tr key={row.model} className="border-t border-line/40">
                    <td className="px-4 py-2.5 font-mono text-xs text-ink">{row.model}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted">{row.probes_24h}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted">{row.ok_24h}</td>
                    <td className="px-4 py-2.5">
                      {row.last_class ? (
                        <ClassBadge name={row.last_class} />
                      ) : (
                        <span className="text-xs text-dim">-</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted">
                      {row.last_ts ? formatUtc(row.last_ts) : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-2 text-xs text-dim">No models recorded yet.</p>
        )}
      </div>

      <div>
        <SectionHeading>Recent probes</SectionHeading>
        {recent.length > 0 ? (
          <ol className="mt-3 max-h-96 overflow-y-auto rounded-xl border border-line bg-bg-soft/40">
            {recent.map((probe, index) => (
              <RecentProbeRow key={`${probe.ts}-${index}`} probe={probe} />
            ))}
          </ol>
        ) : (
          <p className="mt-2 text-xs text-dim">No probes recorded yet.</p>
        )}
      </div>
    </div>
  )
}

/** Fleet section: read-only prober history aggregate (GET /api/fleet). */
export function FleetSection() {
  const [fleet, setFleet] = useState<FleetSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      setFleet(await fetchFleet())
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <Section id="fleet" index="04" title="Fleet">
      <div className={`${card} ${cardGlow} p-6 sm:p-8`}>
        {loading && !fleet ? (
          <div className="animate-pulse space-y-3" role="status" aria-label="Loading fleet data">
            <div className="h-4 w-44 rounded bg-line/70" />
            <div className="h-3 w-full rounded bg-line/50" />
            <div className="h-3 w-5/6 rounded bg-line/50" />
            <div className="h-3 w-4/6 rounded bg-line/50" />
          </div>
        ) : !fleet && error ? (
          <div>
            <p className="text-sm text-bad">{error}</p>
            <button
              type="button"
              onClick={() => void load()}
              className={`${buttonGhost} mt-4 px-3.5 py-1.5`}
            >
              <RefreshIcon className="h-3.5 w-3.5" />
              Retry
            </button>
          </div>
        ) : fleet ? (
          <div>
            {error ? <p className="mb-4 text-xs text-warn">refresh failed: {error}</p> : null}
            {fleet.available ? <FleetContent fleet={fleet} /> : <FleetEmptyState />}
          </div>
        ) : null}
      </div>
    </Section>
  )
}
