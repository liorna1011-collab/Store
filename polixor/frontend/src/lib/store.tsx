// מצב גלובלי: משימות, חיבור WebSocket והודעות למשתמש.

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useSyncExternalStore,
} from 'react'
import i18n from '../i18n'
import { api, PolixorApiError } from './api'
import type { Job, WsEvent } from './types'

type ToastTone = 'info' | 'success' | 'error' | 'warn'

export interface Toast {
  id: number
  tone: ToastTone
  title: string
  body?: string
}

/**
 * A tiny external store: components read a slice with useSyncExternalStore and re-render only
 * when THAT slice changes. (Before: jobs, connection and toasts lived in one React context value,
 * so every progress update – twice a second per running job – re-rendered every component that
 * used the store: the whole app, sidebar and pages included.)
 */
class Slice<T> {
  private subs = new Set<() => void>()
  constructor(private value: T) {}
  get = () => this.value
  set = (next: T | ((prev: T) => T)) => {
    const v = typeof next === 'function' ? (next as (p: T) => T)(this.value) : next
    if (Object.is(v, this.value)) return
    this.value = v
    this.subs.forEach((f) => f())
  }
  subscribe = (f: () => void) => { this.subs.add(f); return () => { this.subs.delete(f) } }
}

const jobsSlice = new Slice<{ jobs: Job[]; loading: boolean; error: string | null }>(
  { jobs: [], loading: true, error: null })
const connectedSlice = new Slice<boolean>(false)
const toastsSlice = new Slice<Toast[]>([])

export function useJobs() { return useSyncExternalStore(jobsSlice.subscribe, jobsSlice.get) }
export function useConnected() { return useSyncExternalStore(connectedSlice.subscribe, connectedSlice.get) }
export function useToasts() { return useSyncExternalStore(toastsSlice.subscribe, toastsSlice.get) }

interface StoreValue {
  refreshJobs: () => Promise<void>
  upsertJob: (job: Job) => void
  removeJob: (id: string) => void
  pushToast: (t: Omit<Toast, 'id'>) => void
  dismissToast: (id: number) => void
  notifyError: (e: unknown, fallback?: string) => void
  /** נרשם לאירועי WebSocket. מחזיר פונקציית ביטול. */
  subscribe: (fn: (e: WsEvent) => void) => () => void
}

const Ctx = createContext<StoreValue | null>(null)

let toastSeq = 1

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const setJobs = useCallback((fn: (prev: Job[]) => Job[]) =>
    jobsSlice.set((st) => { const jobs = fn(st.jobs); return jobs === st.jobs ? st : { ...st, jobs } }), [])
  const setJobsLoading = (loading: boolean) => jobsSlice.set((st) => (st.loading === loading ? st : { ...st, loading }))
  const setJobsError = (error: string | null) => jobsSlice.set((st) => (st.error === error ? st : { ...st, error }))
  const setConnected = connectedSlice.set
  const setToasts = toastsSlice.set
  const connected = useSyncExternalStore(connectedSlice.subscribe, connectedSlice.get)

  const listeners = useRef(new Set<(e: WsEvent) => void>())
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(0)
  const closedRef = useRef(false)
  // progress events are merged and applied at most twice a second: every update re-renders
  // everything that reads the store, and a busy job sends several per second
  const pendingProgress = useRef(new Map<string, Record<string, any>>())
  const flushTimer = useRef<number | null>(null)
  const everConnected = useRef(false)

  const pushToast = useCallback((t: Omit<Toast, 'id'>) => {
    const id = toastSeq++
    setToasts((prev) => [...prev.slice(-4), { ...t, id }])
    const ttl = t.tone === 'error' ? 9000 : 4500
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), ttl)
  }, [])

  const dismissToast = useCallback(
    (id: number) => setToasts((prev) => prev.filter((t) => t.id !== id)), [])

  const notifyError = useCallback((e: unknown, fallback?: string) => {
    fallback = fallback || i18n.t('common.actionFailed')
    if (e instanceof PolixorApiError) {
      // friendly words for the user; the reference finds the exact failure in the admin view
      const ref = e.requestId ? i18n.t('common.errors.ref', { id: e.requestId }) : ''
      pushToast({ tone: 'error', title: e.message, body: [e.hint, ref].filter(Boolean).join(' · ') || undefined })
    } else if (e instanceof Error) {
      pushToast({ tone: 'error', title: fallback, body: e.message })
    } else {
      pushToast({ tone: 'error', title: fallback })
    }
  }, [pushToast])

  const refreshJobs = useCallback(async () => {
    try {
      const list = await api.listJobs(80)
      setJobs(() => list)
      setJobsError(null)
    } catch (e) {
      setJobsError(e instanceof Error ? e.message : i18n.t('common.errors.loadFailed'))
    } finally {
      setJobsLoading(false)
    }
  }, [])

  const upsertJob = useCallback((job: Job) => {
    setJobs((prev) => {
      const i = prev.findIndex((j) => j.id === job.id)
      if (i === -1) return [job, ...prev]
      const next = [...prev]
      next[i] = job
      return next
    })
  }, [])

  const removeJob = useCallback((id: string) => {
    setJobs((prev) => prev.filter((j) => j.id !== id))
  }, [])

  const subscribe = useCallback((fn: (e: WsEvent) => void) => {
    listeners.current.add(fn)
    return () => { listeners.current.delete(fn) }
  }, [])

  // ---- WebSocket עם חיבור מחדש אוטומטי ----
  useEffect(() => {
    closedRef.current = false

    const connect = () => {
      if (closedRef.current) return
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
        retryRef.current = 0
        // events sent while we were away are gone: take the current state once
        if (everConnected.current) void refreshJobs()
        everConnected.current = true
      }
      ws.onclose = () => {
        setConnected(false)
        if (closedRef.current) return
        retryRef.current = Math.min(retryRef.current + 1, 6)
        setTimeout(connect, 600 * retryRef.current)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (ev) => {
        let parsed: WsEvent
        try { parsed = JSON.parse(ev.data) } catch { return }
        if (parsed.type === 'ping' || parsed.type === 'hello') return

        // עדכון מקומי מהיר של ההתקדמות בלי לקרוא מחדש לשרת
        if (parsed.type === 'job.progress' && parsed.job_id) {
          pendingProgress.current.set(parsed.job_id, parsed.data)
          if (flushTimer.current === null) {
            flushTimer.current = window.setTimeout(() => {
              flushTimer.current = null
              const batch = new Map(pendingProgress.current)
              pendingProgress.current.clear()
              setJobs((prev) => prev.map((j) => {
                const d = batch.get(j.id)
                return d ? {
                  ...j,
                  stage: d.stage ?? j.stage,
                  stage_progress: d.stage_progress ?? j.stage_progress,
                  overall_progress: d.overall_progress ?? j.overall_progress,
                  message: d.message ?? j.message,
                } : j
              }))
            }, 500)
          }
        }
        if (parsed.type === 'job.status' && parsed.job_id) {
          setJobs((prev) => prev.map((j) => j.id === parsed.job_id ? {
            ...j,
            status: parsed.data.status ?? j.status,
            message: parsed.data.message ?? j.message,
            error: parsed.data.error?.message ?? j.error,
          } : j))
          // סטטוס סופי – מושכים את הרשומה המלאה
          if (['completed', 'failed', 'cancelled'].includes(parsed.data.status)) {
            api.getJob(parsed.job_id).then(upsertJob).catch(() => undefined)
          }
        }
        listeners.current.forEach((fn) => {
          try { fn(parsed) } catch { /* מאזין בודד לא מפיל את השאר */ }
        })
      }
    }

    connect()
    return () => {
      closedRef.current = true
      if (flushTimer.current !== null) window.clearTimeout(flushTimer.current)
      wsRef.current?.close()
    }
  }, [upsertJob, refreshJobs])

  useEffect(() => { void refreshJobs() }, [refreshJobs])

  // רשת ביטחון: רענון תקופתי גם אם ה-WebSocket נפל
  useEffect(() => {
    const id = setInterval(() => {
      if (!connected) void refreshJobs()
    }, 8000)
    return () => clearInterval(id)
  }, [connected, refreshJobs])

  // stable: only functions – reading the store never re-renders a component by itself
  const value = useMemo<StoreValue>(() => ({
    refreshJobs, upsertJob, removeJob, pushToast, dismissToast, notifyError, subscribe,
  }), [refreshJobs, upsertJob, removeJob, pushToast, dismissToast, notifyError, subscribe])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useStore(): StoreValue {
  const v = useContext(Ctx)
  if (!v) throw new Error('useStore must be used inside StoreProvider')
  return v
}
