import { useEffect, useId, useState } from 'react'

/** The mermaid default export, loaded lazily so the entry bundle stays lean. */
type MermaidApi = typeof import('mermaid').default

let mermaidPromise: Promise<MermaidApi> | null = null
let uidSeq = 0

/** Dynamic import + one-time init, shared by every diagram on the page. */
function loadMermaid(): Promise<MermaidApi> {
  mermaidPromise ??= import('mermaid').then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'base',
      themeVariables: {
        darkMode: false,
        background: '#ffffff',
        primaryColor: '#ffffff',
        primaryBorderColor: '#0f0e12',
        primaryTextColor: '#0f0e12',
        secondaryColor: '#eff1f0',
        secondaryBorderColor: '#b2b2b2',
        secondaryTextColor: '#3f3f44',
        tertiaryColor: '#f6f8f7',
        tertiaryBorderColor: '#d9dbda',
        tertiaryTextColor: '#3f3f44',
        lineColor: '#0f0e12',
        textColor: '#3f3f44',
        nodeTextColor: '#0f0e12',
        edgeLabelBackground: '#ffffff',
        clusterBkg: '#f6f8f7',
        clusterBorder: '#d9dbda',
        fontFamily: '"IBM Plex Sans Variable", ui-sans-serif, system-ui, sans-serif',
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
        className="mermaid-diagram mt-4 flex justify-center overflow-x-auto border border-line bg-bg-soft p-4 [&>svg]:h-auto [&>svg]:max-w-full"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    )
  }

  return (
    <pre className="mt-4 overflow-x-auto border border-line bg-bg-soft p-4 font-mono text-xs leading-relaxed text-muted">
      {failed ? `${chart}\n\n(diagram unavailable - showing the mermaid source)` : chart}
    </pre>
  )
}
