// Resumable, chunked upload of a source video (backend: services/uploads.py).
//
// The file is never read whole: each chunk is a File.slice() sent as its own PUT, a few
// at a time. A chunk that fails is retried with backoff; only when its retries run out
// does the upload stop – with the server's reason – and it can be resumed later: the
// server keeps every chunk it has, and choosing the same file again (even after a page
// refresh) continues from there. Progress is counted in bytes the server confirmed plus
// bytes in flight – never estimated.
//
// Throughput: the checksum of each chunk is computed in a Web Worker (the page never
// stalls on it), the number of parallel chunk requests adapts between 2 and the server's
// limit from the measured speed (more while it helps, fewer after a failure), and the
// chunk size of a new upload is chosen from the speed measured on this browser's previous
// upload (within the server's proxy-safe bounds; fixed for the life of an upload, so a
// resume after a refresh always lines up). A sign-in that expires pauses the upload; it
// continues after signing in again.

import i18n from '../i18n'
import { PolixorApiError, sessionExpired } from './api'

export type UploadPhase = 'starting' | 'uploading' | 'retrying' | 'paused' | 'finalizing' | 'complete' | 'failed'

export interface UploadState {
  phase: UploadPhase
  uploadId: string
  loaded: number
  total: number
  chunksDone: number
  chunksTotal: number
  retryIn: number          // seconds until the next attempt (retrying)
  error: { code: string; message: string; hint: string } | null
  result: { upload_token: string; duration: number; width: number; height: number; file_size: number } | null
}

interface Session {
  concurrency_max?: number
  upload_id: string
  size: number
  chunk_size: number
  total_chunks: number
  received: number[]
  status: string
  result: UploadState['result']
  error: string
}

const MIN_PARALLEL = 2
const START_PARALLEL = 3
const MAX_ATTEMPTS = 8
const SPEED_KEY = 'polixor.uploadMbps'
const MB = 1024 * 1024
const BACKOFF = [1, 2, 4, 8, 15, 30, 30, 30]
const STORE = 'polixor.pendingUploads'

export function fingerprint(f: File): string {
  return `${f.name}|${f.size}|${f.lastModified}`
}

/** Unfinished uploads of this browser (so the page can offer to continue them). */
export function pendingUploads(): { fingerprint: string; name: string; size: number; uploadId: string }[] {
  try { return JSON.parse(localStorage.getItem(STORE) || '[]') } catch { return [] }
}

function remember(f: File, uploadId: string | null) {
  try {
    const fp = fingerprint(f)
    const rest = pendingUploads().filter((p) => p.fingerprint !== fp)
    if (uploadId) rest.push({ fingerprint: fp, name: f.name, size: f.size, uploadId })
    localStorage.setItem(STORE, JSON.stringify(rest.slice(-5)))
  } catch { /* private window */ }
}

function langHeaders(): Record<string, string> {
  return { 'X-Polixor-Lang': i18n.language?.startsWith('he') ? 'he' : 'en' }
}

async function call<T>(method: string, path: string, body?: unknown, timeoutMs = 120_000): Promise<T> {
  let res: Response
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    res = await fetch(path, {
      method, headers: { 'Content-Type': 'application/json', 'X-Polixor-Request': '1', ...langHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body), signal: ctrl.signal,
    })
  } catch {
    throw new PolixorApiError({ code: ctrl.signal.aborted ? 'timeout' : 'network',
      message: i18n.t(ctrl.signal.aborted ? 'creator.upload.timeout' : 'creator.upload.network'), hint: '' }, 0)
  } finally {
    clearTimeout(timer)
  }
  const text = await res.text()
  const data = text ? JSON.parse(text) : undefined
  if (!res.ok) {
    const d = data?.detail && typeof data.detail === 'object' ? data.detail
      : { code: 'http_error', message: i18n.t('common.errors.http', { status: res.status }), hint: '' }
    if (res.status === 401) sessionExpired()
    throw new PolixorApiError(d, res.status)
  }
  return data as T
}

// ---- checksum in a worker (main-thread fallback when workers are unavailable) ----
let hashWorker: Worker | null | undefined
let hashSeq = 0
const hashWaiting = new Map<number, (hex: string) => void>()

function worker(): Worker | null {
  if (hashWorker !== undefined) return hashWorker
  try {
    hashWorker = new Worker(new URL('../workers/hash.worker.ts', import.meta.url), { type: 'module' })
    hashWorker.onmessage = (e: MessageEvent<{ id: number; hex: string }>) => {
      hashWaiting.get(e.data.id)?.(e.data.hex)
      hashWaiting.delete(e.data.id)
    }
    hashWorker.onerror = () => { hashWaiting.forEach((r) => r('')); hashWaiting.clear(); hashWorker = null }
  } catch {
    hashWorker = null
  }
  return hashWorker
}

export function usesHashWorker(): boolean { return worker() !== null }

/** The upload speed this browser measured last time (Mbit/s), 0 if unknown. */
function lastSpeed(): number {
  try { return Number(localStorage.getItem(SPEED_KEY) || 0) } catch { return 0 }
}

/** Chunk size for a new upload: bigger on a fast link (fewer requests), smaller on a slow one (cheap retries). */
export function chooseChunkSize(mbps = lastSpeed()): number {
  if (mbps >= 200) return 32 * MB
  if (mbps >= 40 || !mbps) return 16 * MB
  return 8 * MB
}

async function sha256(blob: Blob): Promise<string> {
  const w = worker()
  if (w) {
    const id = ++hashSeq
    return new Promise((resolve) => { hashWaiting.set(id, resolve); w.postMessage({ id, blob }) })
  }
  if (!globalThis.crypto?.subtle) return ''
  const buf = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** Retryable: network trouble, timeouts, a proxy hiccup, a server error. Not: a rejected request. */
function retryable(status: number): boolean {
  return status === 0 || status === 408 || status === 409 || status === 425 || status === 429 || status >= 500
    && status !== 507
}

export class ResumableUpload {
  private session: Session | null = null
  private inflight = new Map<number, XMLHttpRequest>()
  private inflightLoaded = new Map<number, number>()
  private paused = false
  private cancelled = false
  // adaptive parallelism and the numbers reported for the admin view
  private target = START_PARALLEL
  private maxParallel = 6
  private samples: { t: number; bytes: number }[] = []
  private lastRate = 0
  private lastAdjust = 0
  private retries = 0
  private resumes = 0
  private peakMbps = 0
  private startedAt = 0
  private pausedAt = 0
  private pausedMs = 0
  private maxUsed = 0
  state: UploadState

  constructor(private file: File, private onChange: (s: UploadState) => void) {
    this.state = { phase: 'starting', uploadId: '', loaded: 0, total: file.size, chunksDone: 0,
                   chunksTotal: 0, retryIn: 0, error: null, result: null }
  }

  private emit(patch: Partial<UploadState>) {
    this.state = { ...this.state, ...patch }
    this.onChange(this.state)
  }

  private confirmedBytes(): number {
    const s = this.session
    if (!s) return 0
    return s.received.reduce((acc, i) => acc + Math.min(s.chunk_size, s.size - i * s.chunk_size), 0)
  }

  private progress() {
    let inflight = 0
    this.inflightLoaded.forEach((v) => { inflight += v })
    this.emit({ loaded: Math.min(this.file.size, this.confirmedBytes() + inflight),
                chunksDone: this.session?.received.length ?? 0 })
  }

  /** Starts, or continues the same file's earlier upload (server-side state). */
  async start(): Promise<UploadState['result']> {
    this.paused = false
    this.cancelled = false
    if (this.pausedAt) { this.pausedMs += Date.now() - this.pausedAt; this.pausedAt = 0 }
    if (!this.startedAt) this.startedAt = Date.now()
    this.emit({ phase: 'starting', error: null })
    try {
      this.session = await call<Session>('POST', '/api/uploads', {
        filename: this.file.name, size: this.file.size, fingerprint: fingerprint(this.file),
        chunk_size: chooseChunkSize() })
    } catch (e) {
      return this.fail(e)
    }
    const s = this.session
    this.maxParallel = Math.max(MIN_PARALLEL, Math.min(6, s.concurrency_max || 6))
    this.target = Math.min(this.target, this.maxParallel)
    if (s.received.length > 0 && s.status !== 'complete') this.resumes++
    remember(this.file, s.upload_id)
    this.emit({ uploadId: s.upload_id, chunksTotal: s.total_chunks })
    this.progress()
    if (s.status === 'complete' && s.result) return this.done(s.result)
    return this.run()
  }

  private async run(): Promise<UploadState['result']> {
    const s = this.session!
    const missing = Array.from({ length: s.total_chunks }, (_, i) => i).filter((i) => !s.received.includes(i))
    this.emit({ phase: 'uploading' })
    let next = 0
    let failure: unknown = null
    // one promise per chunk in flight; as each ends, the next is sent while fewer than
    // `target` are in flight – so a change of target applies from the next chunk on
    const lanes = new Set<Promise<void>>()
    for (;;) {
      while (!failure && !this.paused && !this.cancelled && next < missing.length && lanes.size < this.target) {
        const index = missing[next++]
        const p: Promise<void> = this.sendWithRetry(index)
          .catch((e) => { failure = failure ?? e })
          .finally(() => { lanes.delete(p) })
        lanes.add(p)
        this.maxUsed = Math.max(this.maxUsed, lanes.size)
      }
      if (lanes.size === 0) break
      await Promise.race(lanes)
    }
    if (this.cancelled) return null
    if (this.paused) { this.emit({ phase: 'paused' }); return null }
    if (failure) return this.fail(failure)
    return this.finalize()
  }

  private async sendWithRetry(index: number): Promise<void> {
    const s = this.session!
    const start = index * s.chunk_size
    const blob = this.file.slice(start, Math.min(s.size, start + s.chunk_size))
    const sum = await sha256(blob)
    for (let attempt = 0; ; attempt++) {
      try {
        await this.sendChunk(index, blob, sum)
        this.measure(blob.size)
        if (!s.received.includes(index)) s.received.push(index)
        this.inflightLoaded.delete(index)
        this.progress()
        if (this.state.phase === 'retrying') this.emit({ phase: 'uploading', retryIn: 0 })
        return
      } catch (e) {
        this.inflightLoaded.delete(index)
        this.progress()
        const status = e instanceof PolixorApiError ? e.status : 0
        if (this.cancelled || this.paused) return
        if (status === 401) { this.pause(); this.emit({ phase: 'paused' }); return }
        this.retries++
        this.target = Math.max(MIN_PARALLEL, this.target - 1)     // back off on trouble
        if (!retryable(status) && !(e instanceof PolixorApiError && /chunk_size|checksum/.test(e.code))) throw e
        if (attempt + 1 >= MAX_ATTEMPTS) throw e
        const wait = BACKOFF[Math.min(attempt, BACKOFF.length - 1)]
        this.emit({ phase: 'retrying', retryIn: wait })
        await new Promise((r) => setTimeout(r, wait * 1000))
        if (this.cancelled || this.paused) return
      }
    }
  }

  private sendChunk(index: number, blob: Blob, sum: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      this.inflight.set(index, xhr)
      xhr.open('PUT', `/api/uploads/${this.session!.upload_id}/chunks/${index}`)
      xhr.setRequestHeader('Content-Type', 'application/octet-stream')
      xhr.setRequestHeader('X-Polixor-Request', '1')
      if (sum) xhr.setRequestHeader('X-Chunk-Sha256', sum)
      Object.entries(langHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v))
      xhr.timeout = 10 * 60 * 1000
      xhr.upload.onprogress = (e) => { this.inflightLoaded.set(index, e.loaded); this.progress() }
      const finish = (err: PolixorApiError | null) => { this.inflight.delete(index); err ? reject(err) : resolve() }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) return finish(null)
        let d = { code: 'upload_failed', message: i18n.t('creator.upload.chunkFailed', { status: xhr.status }), hint: '' }
        try { const b = JSON.parse(xhr.responseText); if (b?.detail?.code) d = b.detail } catch { /* proxy page */ }
        if (xhr.status === 401) sessionExpired()
        finish(new PolixorApiError(d, xhr.status))
      }
      xhr.onerror = () => finish(new PolixorApiError({ code: 'network', message: i18n.t('creator.upload.network'), hint: '' }, 0))
      xhr.ontimeout = () => finish(new PolixorApiError({ code: 'timeout', message: i18n.t('creator.upload.timeout'), hint: '' }, 0))
      xhr.onabort = () => finish(new PolixorApiError({ code: 'aborted', message: '', hint: '' }, 0))
      xhr.send(blob)
    })
  }

  private async finalize(): Promise<UploadState['result']> {
    this.emit({ phase: 'finalizing', loaded: this.file.size })
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const s = await call<Session>('POST', `/api/uploads/${this.session!.upload_id}/complete`)
        if (s.result) return this.done(s.result)
      } catch (e) {
        const st = e instanceof PolixorApiError ? e.status : 0
        if (e instanceof PolixorApiError && e.code === 'upload_incomplete') {
          // the server misses a part (e.g. a lost reply): fetch its state and send what is missing
          this.session = await call<Session>('GET', `/api/uploads/${this.session!.upload_id}`)
          return this.run()
        }
        if (!retryable(st) || attempt === 4) return this.fail(e)
        await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)))
      }
    }
    return null
  }

  /** Throughput over the last ~8 s; raises parallelism while that helps, lowers it when it stops helping. */
  private measure(bytes: number) {
    const now = Date.now()
    this.samples.push({ t: now, bytes })
    this.samples = this.samples.filter((x) => now - x.t < 8000)
    const span = Math.max(1000, now - this.samples[0].t)
    const rate = this.samples.reduce((a, x) => a + x.bytes, 0) * 8 / span / 1000   // Mbit/s
    if (this.samples.length >= 3) this.peakMbps = Math.max(this.peakMbps, rate)
    if (now - this.lastAdjust < 6000 || this.samples.length < 3) return
    this.lastAdjust = now
    if (!this.lastRate || rate > this.lastRate * 1.1) {
      if (this.target < this.maxParallel) this.target++
    } else if (rate < this.lastRate * 0.8 && this.target > MIN_PARALLEL) {
      this.target--
    }
    this.lastRate = rate
  }

  private report() {
    const s = this.session
    if (!s) return
    const secs = Math.max(0.001, (Date.now() - this.startedAt - this.pausedMs) / 1000)
    const avg = this.file.size * 8 / secs / 1e6
    try { if (this.state.phase === 'complete') localStorage.setItem(SPEED_KEY, String(Math.round(avg))) } catch { /* private window */ }
    void call('POST', `/api/uploads/${s.upload_id}/telemetry`, {
      retries: this.retries, resumes: this.resumes, concurrency: this.maxUsed,
      peak_mbps: Math.round(this.peakMbps * 10) / 10, avg_mbps: Math.round(avg * 10) / 10,
      client_seconds: Math.round(secs * 10) / 10, paused_seconds: Math.round(this.pausedMs / 100) / 10,
      hash_worker: usesHashWorker(),
    }, 15_000).catch(() => undefined)
  }

  private done(result: NonNullable<UploadState['result']>): UploadState['result'] {
    remember(this.file, null)
    this.emit({ phase: 'complete', loaded: this.file.size, result, error: null })
    this.report()
    return result
  }

  private fail(e: unknown): null {
    const err = e instanceof PolixorApiError ? { code: e.code, message: e.message, hint: e.hint }
      : { code: 'upload_failed', message: String(e), hint: '' }
    this.emit({ phase: 'failed', error: err })
    return null
  }

  pause() {
    if (!this.paused) this.pausedAt = Date.now()
    this.paused = true
    this.inflight.forEach((x) => x.abort())
  }

  async resume(): Promise<UploadState['result']> {
    return this.start()        // the server says which chunks it already has
  }

  async cancel(): Promise<void> {
    this.cancelled = true
    this.inflight.forEach((x) => x.abort())
    remember(this.file, null)
    if (this.session && this.state.phase !== 'complete') {
      try { await call('DELETE', `/api/uploads/${this.session.upload_id}`) } catch { /* already gone */ }
    }
  }
}
