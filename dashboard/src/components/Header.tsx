import type { Report } from '../lib/types'
import { GITHUB_URL } from '../lib/ui'
import { GitHubIcon } from './Icons'
import { ScenarioMatrix } from './ScenarioMatrix'

const sectionLinks = [
  { href: '#overview', label: 'Overview' },
  { href: '#chat', label: 'Try it' },
  { href: '#results', label: 'Results' },
  { href: '#history', label: 'History' },
  { href: '#method', label: 'Method' },
  { href: '#fleet', label: 'Fleet' },
]

const heroLink =
  'inline-flex items-center gap-2 border border-[#3a393d] px-4 py-2 text-sm text-[#f6f8f7] transition-colors hover:border-[#f6f8f7]'

export function Header({ report }: { report: Report | null }) {
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-line bg-bg">
        <div className="mx-auto flex h-14 max-w-[75rem] items-center justify-between gap-3 px-5 sm:px-8">
          <a href="#top" className="font-display text-lg font-light whitespace-nowrap">
            routing-lab
          </a>

          <nav aria-label="Sections" className="hidden items-center gap-6 md:flex">
            {sectionLinks.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="text-[13px] text-muted underline-offset-4 transition-colors hover:text-ink hover:underline"
              >
                {link.label}
              </a>
            ))}
          </nav>

          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-[13px] text-muted underline-offset-4 transition-colors hover:text-ink hover:underline"
          >
            <GitHubIcon className="h-3.5 w-3.5" />
            GitHub
          </a>
        </div>
      </header>

      <section id="top" className="scroll-mt-14 bg-carbon text-[#f6f8f7]">
        <div className="mx-auto grid max-w-[75rem] gap-10 px-5 pt-12 pb-10 sm:px-8 sm:pt-20 sm:pb-14 lg:grid-cols-12 lg:gap-8">
          <div className="lg:col-span-7">
            <p className="font-mono text-xs text-[#b2b2b2]">failover evidence, live</p>
            <h1 className="mt-4 font-display text-6xl font-extralight tracking-tight sm:text-8xl">
              routing-lab
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-relaxed font-light sm:text-xl">
              Every AI request on this server runs through one self-hosted LiteLLM router. When an
              upstream provider stalls, rate-limits, or fails, the router decides what the client
              sees: a silent reroute, a retry, or an error.
            </p>
            <p className="mt-4 max-w-2xl text-sm leading-relaxed text-[#b2b2b2] sm:text-base">
              The failover rules live in configuration, but their consequences are not obvious -
              mid-stream stalls cannot be un-sent, and 503 retryability depends on pool topology.
              This lab pins those behaviors down with eight fault-injection scenarios and runs them
              on demand against a mocked upstream.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <a href={GITHUB_URL} target="_blank" rel="noreferrer" className={heroLink}>
                <GitHubIcon className="h-4 w-4" />
                Source on GitHub
              </a>
              <a href="#method" className={heroLink}>
                Method
              </a>
            </div>
          </div>
          <div className="lg:col-span-5 lg:self-end">
            <ScenarioMatrix report={report} />
          </div>
        </div>
      </section>
    </>
  )
}
