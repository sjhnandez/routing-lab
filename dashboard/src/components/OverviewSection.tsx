import type { ReactNode } from 'react'
import { Reveal } from './Reveal'

const claims: { tag: string; body: ReactNode }[] = [
  {
    tag: 'S2',
    body: 'Stalls before the first token reroute silently - the client still gets a clean answer.',
  },
  {
    tag: 'S3',
    body: 'A stall after content started cannot be un-sent: the turn fails loudly, with no fallback.',
  },
  {
    tag: 'S4',
    body: (
      <>
        <code className="font-mono text-[0.9em] text-ink">stream_timeout</code> is a silence-gap
        limit, not a duration cap - slow-but-steady streams survive.
      </>
    ),
  },
  {
    tag: 'S5-S7',
    body: '5xx and 429 cool the pool and reroute across siblings; 404 is never retried.',
  },
  {
    tag: 'S8',
    body: 'Total pool exhaustion fails in bounded time instead of hanging.',
  },
  {
    tag: 'ALL',
    body: "Every claim is a hard assertion, evaluated from the client outcome plus the mock's hit journal - not log reading.",
  },
]

/** Purpose overview: what the battery proves, rendered right after the hero. */
export function OverviewSection() {
  return (
    <section id="overview" className="scroll-mt-16">
      <div className="mx-auto max-w-[75rem] px-5 pt-14 sm:px-8 sm:pt-[4.125rem]">
        <Reveal>
          <div className="flex flex-wrap items-baseline justify-between gap-3 border-t border-ink pt-3">
            <h2 className="font-display text-3xl font-light tracking-tight sm:text-[2.5rem]">
              What it proves
            </h2>
            <a
              href="#method"
              className="text-[13px] text-accent underline-offset-4 transition-colors hover:underline"
            >
              Read the full method
            </a>
          </div>
          <ul className="mt-6 grid gap-px border border-line bg-line sm:mt-8 sm:grid-cols-2 lg:grid-cols-3">
            {claims.map((claim) => (
              <li key={claim.tag} className="bg-surface p-5">
                <p className="font-mono text-xs text-dim">{claim.tag}</p>
                <p className="mt-6 text-[15px] leading-relaxed text-muted">{claim.body}</p>
              </li>
            ))}
          </ul>
        </Reveal>
      </div>
    </section>
  )
}
