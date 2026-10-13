import { GITHUB_URL } from '../lib/ui'
import { GitHubIcon } from './Icons'

const sectionLinks = [
  { href: '#results', label: 'Results' },
  { href: '#history', label: 'History' },
  { href: '#method', label: 'Method' },
  { href: '#fleet', label: 'Fleet' },
]

export function Header() {
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-line/50 bg-bg/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-3 px-5 sm:px-8">
          <a
            href="#top"
            className="font-display text-sm font-bold tracking-tight whitespace-nowrap"
          >
            <span className="gradient-text">routing-lab</span>
          </a>

          <nav aria-label="Sections" className="hidden items-center md:flex">
            {sectionLinks.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="rounded-full px-3 py-1.5 text-sm text-muted transition hover:bg-white/5 hover:text-ink"
              >
                {link.label}
              </a>
            ))}
          </nav>

          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs font-medium text-muted transition hover:border-accent/60 hover:text-ink"
          >
            <GitHubIcon className="h-3.5 w-3.5" />
            GitHub
          </a>
        </div>
      </header>

      <section id="top" className="relative overflow-hidden">
        <div aria-hidden="true" className="mesh pointer-events-none absolute inset-0" />
        <div className="relative mx-auto max-w-5xl px-5 pt-14 pb-10 sm:px-8 sm:pt-24 sm:pb-14">
          <p className="font-mono text-sm text-accent">failover evidence, live</p>
          <h1 className="mt-3 font-display text-5xl font-bold tracking-tight sm:text-6xl">
            <span className="gradient-text">routing-lab</span>
          </h1>
          <p className="mt-5 max-w-2xl font-display text-lg text-muted sm:text-xl">
            Eight hard-assertion scenarios that make a pinned LiteLLM router prove its retry,
            cooldown and fallback semantics — click once and watch them pass.
          </p>
          <div className="mt-7 flex flex-wrap items-center gap-3">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-full border border-line px-4 py-2 text-sm text-muted transition hover:border-accent/60 hover:text-ink"
            >
              <GitHubIcon className="h-4 w-4" />
              Source on GitHub
            </a>
            <a
              href="#method"
              className="inline-flex items-center gap-2 rounded-full border border-line px-4 py-2 text-sm text-muted transition hover:border-accent/60 hover:text-ink"
            >
              Method
            </a>
          </div>
        </div>
      </section>
    </>
  )
}
