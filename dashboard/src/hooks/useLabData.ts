import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchHistory, fetchLastRun, fetchReport } from '../lib/api'
import { errorMessage } from '../lib/format'
import type { HistoryEntry, Report } from '../lib/types'

/**
 * Cached lab data: the latest report (loaded on page load from /api/last-run),
 * a report opened from history, and the run list.
 */
export function useLabData() {
  const [latest, setLatest] = useState<Report | null>(null)
  const [latestLoading, setLatestLoading] = useState(true)
  const [latestError, setLatestError] = useState<string | null>(null)
  const [viewed, setViewed] = useState<Report | null>(null)
  const [viewLoading, setViewLoading] = useState<string | null>(null)
  const [viewError, setViewError] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const refreshLatest = useCallback(async () => {
    try {
      const report = await fetchLastRun()
      if (mountedRef.current) {
        setLatest(report)
        setLatestError(null)
      }
    } catch (err) {
      if (mountedRef.current) setLatestError(errorMessage(err))
    } finally {
      if (mountedRef.current) setLatestLoading(false)
    }
  }, [])

  const refreshHistory = useCallback(async () => {
    try {
      const entries = await fetchHistory()
      if (mountedRef.current) {
        setHistory(entries)
        setHistoryError(null)
      }
    } catch (err) {
      if (mountedRef.current) setHistoryError(errorMessage(err))
    } finally {
      if (mountedRef.current) setHistoryLoading(false)
    }
  }, [])

  const viewReport = useCallback(async (runId: string) => {
    setViewLoading(runId)
    try {
      const report = await fetchReport(runId)
      if (mountedRef.current) {
        setViewed(report)
        setViewError(null)
      }
    } catch (err) {
      if (mountedRef.current) setViewError(errorMessage(err))
    } finally {
      if (mountedRef.current) setViewLoading(null)
    }
  }, [])

  const showLatest = useCallback(() => {
    setViewed(null)
    setViewError(null)
    void refreshLatest()
  }, [refreshLatest])

  useEffect(() => {
    void refreshLatest()
    void refreshHistory()
  }, [refreshLatest, refreshHistory])

  return {
    latest,
    latestLoading,
    latestError,
    viewed,
    viewLoading,
    viewError,
    history,
    historyLoading,
    historyError,
    refreshLatest,
    refreshHistory,
    viewReport,
    showLatest,
  }
}
