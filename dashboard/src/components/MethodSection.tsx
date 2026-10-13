import type { ReactNode } from 'react'
import { Children, isValidElement, useCallback, useEffect, useState } from 'react'
import type { Components } from 'react-markdown'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { fetchDocs } from '../lib/api'
import { errorMessage } from '../lib/format'
import type { LabDocs } from '../lib/types'
import { buttonGhost, card, cardGlow } from '../lib/ui'
import { ChevronDownIcon, RefreshIcon } from './Icons'
import { Mermaid } from './Mermaid'
import { Section } from './Section'

/** True for the `<code>` element of a fenced ```mermaid block. */
function isMermaidCode(child: ReactNode): boolean {
  return (
    isValidElement<{ className?: string }>(child) &&
    typeof child.props.className === 'string' &&
    child.props.className.includes('language-mermaid')
  )
}

const mdComponents: Components = {
  h1: ({ children }) => (
    <h3 className="mt-9 font-display text-xl font-bold tracking-tight first:mt-0">{children}</h3>
  ),
  h2: ({ children }) => (
    <h4 className="mt-7 font-display text-lg font-semibold tracking-tight">{children}</h4>
  ),
  h3: ({ children }) => (
    <h5 className="mt-6 font-display text-base font-semibold">{children}</h5>
  ),
  h4: ({ children }) => (
    <h6 className="mt-5 font-display text-sm font-semibold">{children}</h6>
  ),
  p: ({ children }) => <p className="mt-3 text-sm leading-relaxed text-muted">{children}</p>,
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-1.5 pl-5 marker:text-dim">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-1.5 pl-5 marker:text-dim">{children}</ol>
  ),
  li: ({ children }) => <li className="text-sm leading-relaxed text-muted">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent underline-offset-2 transition hover:text-ink hover:underline"
    >
      {children}
    </a>
  ),
  code: ({ className, children }) =>
    className?.includes('language-mermaid') ? (
      <Mermaid chart={String(children).trim()} />
    ) : className ? (
      <code className="font-mono">{children}</code>
    ) : (
      <code className="rounded border border-line bg-bg-soft px-1.5 py-0.5 font-mono text-[0.8em] text-ink">
        {children}
      </code>
    ),
  pre: ({ children }) =>
    Children.toArray(children).some(isMermaidCode) ? (
      <>{children}</>
    ) : (
      <pre className="mt-4 overflow-x-auto rounded-xl border border-line bg-bg-soft p-4 font-mono text-xs leading-relaxed text-muted [&>code]:border-0 [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-[1em] [&>code]:rounded-none">
        {children}
      </pre>
    ),
  blockquote: ({ children }) => (
    <blockquote className="mt-4 border-l-2 border-accent/60 pl-4 text-sm leading-relaxed text-dim">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="mt-8 border-line/60" />,
  table: ({ children }) => (
    <div className="mt-4 overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[32rem] border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-line bg-bg-soft/60 px-3 py-2 text-left font-mono text-[11px] tracking-wider text-dim uppercase">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-line/40 px-3 py-2 align-top text-xs leading-relaxed text-muted">
      {children}
    </td>
  ),
}

/** Method section: renders the API's methodology + scenarios markdown. */
export function MethodSection() {
  const [docs, setDocs] = useState<LabDocs | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      setDocs(await fetchDocs())
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
    <Section id="method" index="03" title="Method">
      <div className={`${card} ${cardGlow} p-6 sm:p-8`}>
        {loading && !docs ? (
          <div className="animate-pulse space-y-3" role="status" aria-label="Loading method docs">
            <div className="h-4 w-48 rounded bg-line/70" />
            <div className="h-3 w-full rounded bg-line/50" />
            <div className="h-3 w-5/6 rounded bg-line/50" />
            <div className="h-3 w-4/6 rounded bg-line/50" />
          </div>
        ) : !docs && error ? (
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
        ) : docs ? (
          <div>
            {error ? (
              <p className="mb-4 text-xs text-warn">refresh failed: {error}</p>
            ) : null}
            <div className="max-w-3xl">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {docs.methodology}
              </ReactMarkdown>
            </div>
            <details className="group mt-10 border-t border-line/60">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-4 font-display text-lg font-semibold tracking-tight transition hover:text-accent [&::-webkit-details-marker]:hidden">
                Scenario catalog - every claim and its hard assertions
                <ChevronDownIcon className="h-4 w-4 shrink-0 text-dim transition group-open:rotate-180" />
              </summary>
              <div className="max-w-3xl">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                  {docs.scenarios}
                </ReactMarkdown>
              </div>
            </details>
          </div>
        ) : null}
      </div>
    </Section>
  )
}
