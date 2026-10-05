// Loading helpers that keep a busy page responsive.

import { useCallback, useEffect, useRef } from 'react'

/**
 * Coalesces reload requests: a burst of events (every clip of a run finishing, status
 * changes) becomes one request after `delayMs`, never two overlapping ones – a reload asked
 * for while one runs is done once after it, so the newest answer always wins and an older
 * one can never overwrite it.
 */
export function useCoalesced(fn: () => Promise<unknown>, delayMs = 400): () => void {
  const fnRef = useRef(fn)
  fnRef.current = fn
  const timer = useRef<number | null>(null)
  const running = useRef(false)
  const again = useRef(false)
  const alive = useRef(true)
  useEffect(() => () => {
    alive.current = false
    if (timer.current !== null) window.clearTimeout(timer.current)
  }, [])

  const run = useCallback(async () => {
    timer.current = null
    if (running.current) { again.current = true; return }
    running.current = true
    try { await fnRef.current() } catch { /* the loader reports its own errors */ } finally {
      running.current = false
      if (again.current && alive.current) {
        again.current = false
        timer.current = window.setTimeout(() => { void run() }, delayMs)
      }
    }
  }, [delayMs])

  return useCallback(() => {
    if (timer.current !== null || !alive.current) return
    timer.current = window.setTimeout(() => { void run() }, delayMs)
  }, [run, delayMs])
}

/** Polls `fn` every `ms` while `active` and the tab is visible; one at a time, stops on unmount. */
export function useVisiblePoll(fn: () => Promise<unknown>, ms: number, active: boolean): void {
  const fnRef = useRef(fn)
  fnRef.current = fn
  useEffect(() => {
    if (!active) return
    let stopped = false
    let t: number | null = null
    const tick = async () => {
      if (stopped) return
      if (document.visibilityState === 'visible') {
        try { await fnRef.current() } catch { /* shown by the loader */ }
      }
      if (!stopped) t = window.setTimeout(tick, ms)
    }
    t = window.setTimeout(tick, ms)
    const onVisible = () => { if (document.visibilityState === 'visible' && !stopped) void fnRef.current() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { stopped = true; if (t !== null) window.clearTimeout(t); document.removeEventListener('visibilitychange', onVisible) }
  }, [ms, active])
}
