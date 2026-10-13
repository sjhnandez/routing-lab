import { GITHUB_URL } from '../lib/ui'
import { ArrowUpIcon, GitHubIcon } from './Icons'

export function Footer() {
  return (
    <footer className="border-t border-line/40">
      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <p className="font-display text-lg font-bold tracking-tight">
            <span className="gradient-text">routing-lab</span>
          </p>
          <div className="flex items-center gap-4">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-xs text-muted transition hover:text-ink"
            >
              <GitHubIcon className="h-3.5 w-3.5" />
              GitHub
            </a>
            <a
              href="#top"
              className="inline-flex items-center gap-1.5 text-xs text-muted transition hover:text-ink"
            >
              <ArrowUpIcon className="h-3.5 w-3.5" />
              Back to top
            </a>
          </div>
        </div>
        <p className="mt-6 text-xs text-dim">
          MIT licensed. Every number on this page comes from a pinned-image run of the runner —
          nothing here is mocked for display.
        </p>
      </div>
    </footer>
  )
}
