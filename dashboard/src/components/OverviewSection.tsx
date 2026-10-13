import { card, cardGlow } from '../lib/ui'
import { Reveal } from './Reveal'

/** Purpose overview: what the battery proves, rendered right after the hero. */
export function OverviewSection() {
  return (
    <section id="overview" className="scroll-mt-20">
      <div className="mx-auto max-w-5xl px-5 pt-10 sm:px-8 sm:pt-12">
        <Reveal>
          <div className={`${card} ${cardGlow} p-6 sm:p-8`}>
            <h2 className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
              What it proves
            </h2>
            <ul className="mt-4 max-w-3xl list-disc space-y-2 pl-5 marker:text-dim">
              <li className="text-sm leading-relaxed text-muted">
                Stalls before the first token reroute silently - the client still gets a clean
                answer. (S2)
              </li>
              <li className="text-sm leading-relaxed text-muted">
                A stall after content started cannot be un-sent: the turn fails loudly, with no
                fallback. (S3)
              </li>
              <li className="text-sm leading-relaxed text-muted">
                <code className="rounded border border-line bg-bg-soft px-1.5 py-0.5 font-mono text-[0.8em] text-ink">
                  stream_timeout
                </code>{' '}
                is a silence-gap limit, not a duration cap - slow-but-steady streams survive. (S4)
              </li>
              <li className="text-sm leading-relaxed text-muted">
                5xx and 429 cool the pool and reroute across siblings; 404 is never retried.
                (S5-S7)
              </li>
              <li className="text-sm leading-relaxed text-muted">
                Total pool exhaustion fails in bounded time instead of hanging. (S8)
              </li>
              <li className="text-sm leading-relaxed text-muted">
                Every claim is a hard assertion, evaluated from the client outcome plus the mock's
                hit journal - not log reading.
              </li>
            </ul>
            <p className="mt-5 text-sm">
              <a
                href="#method"
                className="font-medium text-accent underline-offset-2 transition hover:text-ink hover:underline"
              >
                Read the full method
              </a>
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  )
}
