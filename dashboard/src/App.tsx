import { useCallback } from 'react'
import { MotionConfig } from 'motion/react'
import { Footer } from './components/Footer'
import { FleetSection } from './components/FleetSection'
import { Header } from './components/Header'
import { HistoryTable } from './components/HistoryTable'
import { MethodSection } from './components/MethodSection'
import { ResultsPanel } from './components/ResultsPanel'
import { RunPanel } from './components/RunPanel'
import { Section } from './components/Section'
import { useLabData } from './hooks/useLabData'
import { useRunController } from './hooks/useRunController'

function scrollToResults() {
  const target = document.getElementById('results')
  if (!target) return
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  target.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' })
}

export default function App() {
  const data = useLabData()
  const { refreshLatest, refreshHistory, showLatest, viewReport } = data

  const onRunFinished = useCallback(() => {
    // Drop any historical report the user had open so the fresh results are
    // visible, then pull the new report + history row.
    showLatest()
    void refreshHistory()
  }, [showLatest, refreshHistory])

  const run = useRunController({ onRunFinished })

  const handleView = useCallback(
    (runId: string) => {
      void viewReport(runId).then(() => scrollToResults())
    },
    [viewReport],
  )

  return (
    <MotionConfig reducedMotion="user">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:rounded-full focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:text-ink"
      >
        Skip to content
      </a>
      <Header />
      <main id="main">
        <RunPanel
          phase={run.phase}
          cooldownRemainingS={run.cooldownRemainingS}
          health={run.health}
          healthFailed={run.healthFailed}
          error={run.error}
          onDismissError={run.dismissError}
          onRun={run.start}
          progress={run.progress}
        />
        <ResultsPanel
          report={data.viewed ?? data.latest}
          historical={data.viewed !== null}
          loading={data.latestLoading && data.latest === null && data.viewed === null}
          error={data.viewError ?? data.latestError}
          onBackToLatest={data.showLatest}
          onRetry={() => void refreshLatest()}
        />
        <Section id="history" index="02" title="History">
          <HistoryTable
            entries={data.history}
            loading={data.historyLoading}
            error={data.historyError}
            viewingRunId={data.viewed?.run_id ?? null}
            loadingRunId={data.viewLoading}
            onView={handleView}
            onRetry={() => void refreshHistory()}
          />
        </Section>
        <MethodSection />
        <FleetSection />
      </main>
      <Footer />
    </MotionConfig>
  )
}
