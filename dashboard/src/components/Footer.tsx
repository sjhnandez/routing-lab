import { GITHUB_URL } from '../lib/ui'
import { ArrowUpIcon, GitHubIcon } from './Icons'

export function Footer() {
  return (
    <footer className="mt-14 bg-carbon text-[#f6f8f7] sm:mt-[4.125rem]">
      <div className="mx-auto max-w-[75rem] px-5 py-10 sm:px-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <p className="font-display text-2xl font-extralight">routing-lab</p>
          <div className="flex items-center gap-4">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-[13px] text-[#b2b2b2] transition-colors hover:text-[#f6f8f7]"
            >
              <GitHubIcon className="h-3.5 w-3.5" />
              GitHub
            </a>
            <a
              href="#top"
              className="inline-flex items-center gap-1.5 text-[13px] text-[#b2b2b2] transition-colors hover:text-[#f6f8f7]"
            >
              <ArrowUpIcon className="h-3.5 w-3.5" />
              Back to top
            </a>
          </div>
        </div>
        <p className="mt-6 text-[13px] text-[#b2b2b2]">
          MIT licensed. Every number on this page comes from a pinned-image run of the runner:
          nothing here is mocked for display.
        </p>
      </div>
    </footer>
  )
}
