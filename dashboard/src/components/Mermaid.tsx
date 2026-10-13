import { useEffect, useId, useState } from 'react'

/** The mermaid default export, loaded lazily so the entry bundle stays lean. */
type MermaidApi = typeof import('mermaid').default

let mermaidPromise: Promise<MermaidApi> | null = null
let uidSeq = 0

/** Dynamic import + one-time dark init, shared by every diagram on the page. */
function loadMermaid(): Promise<MermaidApi> {
  mermaidPromise ??= import('mermaid').then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'base',
      themeVariables: {
        darkMode: true,
        background: '#0a0c10',
        primaryColor: '#16213a',
        primaryBorderColor: '#4f8cff',
        primaryTextColor: '#e8ecf4',
        secondaryColor: '#0d1017',
        secondaryBorderColor: '#202839',
        secondaryTextColor: '#a3aec4',
        tertiaryColor: '#0d1017',
        tertiaryBorderColor: '#202839',
        tertiaryTextColor: '#a3aec4',
        lineColor: '#4a5a7a',
        textColor: '#a3aec4',
        nodeTextColor: '#e8ecf4',
        edgeLabelBackground: '#0a0c10',
        clusterBkg: '#0d1017',
        clusterBorder: '#202839',
        fontFamily: '"Inter Variable", ui-sans-serif, system-ui, sans-serif',
        fontSize: '14px',
      },
    })
    return mermaid
  })
  return mermaidPromise
}

/**
 * Inline mermaid diagram. Render failures degrade to a styled source fallback
 * instead of crashing the section; `chart` is the raw mermaid text.
 */
export function Mermaid({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const idBase = useId().replace(/[^a-zA-Z0-9]/g, '')

  useEffect(() => {
    let cancelled = false
    setSvg(null)
    setFailed(false)
    loadMermaid()
      .then((mermaid) => mermaid.render(`rl-mermaid-${idBase}-${++uidSeq}`, chart))
      .then((result) => {
        if (!cancelled) setSvg(result.svg)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [chart, idBase])

  if (svg !== null) {
    return (
      <div
        className="mermaid-diagram mt-4 flex justify-center overflow-x-auto rounded-xl border border-line bg-bg-soft p-4 [&>svg]:h-auto [&>svg]:max-w-full"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    )
  }

  return (
    <pre className="mt-4 overflow-x-auto rounded-xl border border-line bg-bg-soft p-4 font-mono text-xs leading-relaxed text-muted">
      {failed ? `${chart}\n\n(diagram unavailable - showing the mermaid source)` : chart}
    </pre>
  )
}
