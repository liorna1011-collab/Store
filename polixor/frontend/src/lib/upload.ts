// Resumable upload of a source video (backend: services/uploads.py, byte ranges).
//
// The file is never read whole: each request is a File.slice() sent as its own PUT to its byte
// offset. The server records which byte ranges arrived complete (and checksummed), so the request
// size can change at any moment without losing anything already sent – after a refresh, choosing
// the same file again continues from exactly those ranges.
//
// Request size (the part that matters behind proxies): the server tells the browser the transport
// profile of the path it is reached through. Behind the GitHub Codespaces port forwarder
// (*.app.github.dev) big request bodies are refused with HTTP 413 before they reach Polixor, so
// there an upload starts at 4 MiB, may grow only after a size succeeded several times, never past
// 8 MiB. Elsewhere it starts at 8 MiB and may grow to 32 MiB. A size that was ever refused on this
// origin is never tried again (remembered per origin); a size that worked is where the next upload
// starts. An upload never starts with an unproven large request.
//
// A 413 is not retried as it was: dispatch of that size stops, the part is split to half the size
// and sent again, and the upload goes on ("Adjusting upload for this connection…"). Other failures
// are handled by kind: expired sign-in → paused, continues after signing in; 408 / network /
// 502-504 → retried with backoff and fewer parallel requests; 429 → waits Retry-After; any other
// 4xx → stops with the server's explanation. Every failure is recorded for the admin view.

import i18n from '../i18n'
import { PolixorApiError, sessionExpired } from './api'
import { classify, holdReload, recordFailure, requestId, retryAfter, transient } from './diag'

export type UploadPhase = 'starting' | 'uploading' | 'adjusting' | 'retrying' | 'paused' | 'finalizing'
  | 'complete' | 'failed'

export interface UploadState {
  phase: UploadPhase
  uploadId: string
  loaded: number
  total: number
  chunksDone: number
  chunksTotal: number
  retryIn: number          // seconds until the next attempt (retrying)
  requestBytes: number     // the request size in use
  rateBps: number          // bytes per second over the last ~15 s (0 until measured)
  etaSeconds: number | null  // smoothed; null until the rate is stable enough to say
  error: { code: string; message: string; hint: string } | null
  result: { upload_token: string; duration: number; width: number; height: number; file_size: number } | null
}

interface Transport {
  profile: string
  start_bytes: number
  max_bytes: number
  min_bytes: number
  concurrency_start: number
  concurrency_max: number
  request_timeout_s: number
}

interface Session {
  upload_id: string
  size: number
  chunk_size: number
  total_chunks: number
  received: number[]
  ranges?: number[][]
  bytes_received: number
  status: string
  result: UploadState['result']
  error: string
  concurrency_max?: number
  transport?: Transport
}

const MIB = 1024 * 1024
const MAX_ATTEMPTS = 8
const BACKOFF = [1, 2, 4, 8, 15, 30, 30, 30]
const GROW_AFTER = 6              // successes at one size before trying the next one up
const STORE = 'polixor.pendingUploads'
const SPEED_KEY = 'polixor.uploadMbps'

const DEFAULT_TRANSPORT: Transport = {
  profile: 'fallback', start_bytes: 4 * MIB, max_bytes: 8 * MIB, min_bytes: MIB,
  concurrency_start: 2, concurrency_max: 4, request_timeout_s: 300,
}

/**
 * Chrome opens at most 6 connections per host over HTTP/1.1. If the upload took all of them, every
 * click (page data, project status) would wait behind multi-second chunk requests – the app feels
 * stuck while uploading. Over HTTP/1.1 the upload keeps two connections free for the app;
 * HTTP/2 and HTTP/3 multiplex everything over one connection, so no cap is needed there.
 */
export const H1_UPLOAD_PARALLEL_MAX = 4

export function connectionCap(): number {
  try {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    const proto = (nav?.nextHopProtocol || '').toLowerCase()
    return proto === 'h2' || proto === 'h3' ? 16 : H1_UPLOAD_PARALLEL_MAX
  } catch {
    return H1_UPLOAD_PARALLEL_MAX
  }
}

export function isCodespacesOrigin(host = location.hostname): boolean {
  return /\.app\.github\.dev$|\.github\.dev$/i.test(host)
}

export function fingerprint(f: File): string {
  return `${f.name}|${f.size}|${f.lastModified}`
}

/** Unfinished uploads of this browser (so the page can offer to continue them). */
export function pendingUploads(): { fingerprint: string; name: string; size: number; uploadId: string }[] {
  try { return JSON.parse(localStorage.getItem(STORE) || '[]') } catch { return [] }
}

export function remember(f: File, uploadId: string | null) {
  try {
    const fp = fingerprint(f)
    const rest = pendingUploads().filter((p) => p.fingerprint !== fp)
    if (uploadId) rest.push({ fingerprint: fp, name: f.name, size: f.size, uploadId })
    localStorage.setItem(STORE, JSON.stringify(rest.slice(-5)))
  } catch { /* private window */ }
}

// ---- what this origin's path is known to accept (per origin: a Codespace URL is not production) ----
const LIMIT_KEY = () => `polixor.uploadLimits.${location.host}`
interface Limits { ok: number; bad: number }
function readLimits(): Limits {
  try { return { ok: 0, bad: 0, ...JSON.parse(localStorage.getItem(LIMIT_KEY()) || '{}') } } catch { return { ok: 0, bad: 0 } }
}
function writeLimits(l: Limits) {
  try { localStorage.setItem(LIMIT_KEY(), JSON.stringify(l)) } catch { /* private window */ }
}

function langHeaders(): Record<string, string> {
  return { 'X-Polixor-Lang': i18n.language?.startsWith('he') ? 'he' : 'en', 'X-Polixor-Request': '1' }
}

export async function call<T>(method: string, path: string, body?: unknown, timeoutMs = 120_000): Promise<T> {
  let res: Response
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  const rid = requestId()
  const t0 = performance.now()
  try {
    res = await fetch(path, {
      method, headers: { 'Content-Type': 'application/json', 'X-Request-Id': rid, ...langHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body), signal: ctrl.signal,
    })
  } catch {
    const code = ctrl.signal.aborted ? 'timeout' : 'network'
    recordFailure({ action: `upload:${method}`, route: path, method, status: 0, elapsed_ms: Math.round(performance.now() - t0),
                    category: classify(0, code), request_id: rid })
    throw new PolixorApiError({ code, message: i18n.t(code === 'timeout' ? 'creator.upload.timeout' : 'creator.upload.network'), hint: '' }, 0)
  } finally {
    clearTimeout(timer)
  }
  const text = await res.text()
  let data: any
  try { data = text ? JSON.parse(text) : undefined } catch { data = undefined }
  if (!res.ok) {
    const d = data?.detail && typeof data.detail === 'object' ? data.detail
      : { code: res.status === 413 ? 'proxy_too_large' : 'http_error', message: i18n.t('common.errors.http', { status: res.status }), hint: '' }
    const cat = classify(res.status, d.code)
    recordFailure({ action: `upload:${method}`, route: path, method, status: res.status, elapsed_ms: Math.round(performance.now() - t0),
                    category: cat, code: d.code, request_id: res.headers.get('x-request-id') || rid })
    if (cat === 'auth') sessionExpired()
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

export async function sha256(blob: Blob): Promise<string> {
  const w = worker()
  if (w) {
    const id = ++hashSeq
    return new Promise((resolve) => { hashWaiting.set(id, resolve); w.postMessage({ id, blob }) })
  }
  if (!globalThis.crypto?.subtle) return ''
  const buf = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** The request size to start with: the profile's safe start, or a size this origin already proved. */
export function startSize(t: Transport, l: Limits = readLimits()): number {
  let s = t.start_bytes
  if (l.ok > s) s = Math.min(l.ok, t.max_bytes)
  if (l.bad) while (s >= l.bad && s > t.min_bytes) s = Math.max(t.min_bytes, Math.floor(s / 2))
  return s
}

type Piece = { a: number; b: number }

class TooLarge extends Error {
  constructor(public bytes: number, public requestId = '') { super('413') }
}

export class ResumableUpload {
  private session: Session | null = null
  private transport: Transport = DEFAULT_TRANSPORT
  private inflight = new Map<string, XMLHttpRequest>()
  private inflightLoaded = new Map<string, number>()
  private confirmed: number[][] = []         // merged byte ranges the server confirmed
  private missing: number[][] = []           // what is left to hand out
  private requeue: Piece[] = []              // pieces to send again (split after a 413, or failed)
  private paused = false
  private cancelled = false
  private reqSize = 4 * MIB
  private target = 2
  private maxParallel = 4
  private okStreak = 0
  private samples: { t: number; bytes: number }[] = []
  private lastRate = 0
  private lastAdjust = 0
  private retries = 0
  private resumes = 0
  private rejected: number[] = []
  private peakMbps = 0
  private startedAt = 0
  private pausedAt = 0
  private pausedMs = 0
  private maxUsed = 0
  private releaseHold: (() => void) | null = null
  state: UploadState

  constructor(private file: File, private onChange: (s: UploadState) => void) {
    this.state = { phase: 'starting', uploadId: '', loaded: 0, total: file.size, chunksDone: 0,
                   chunksTotal: 0, retryIn: 0, requestBytes: 0, rateBps: 0, etaSeconds: null, error: null,
                   result: null }
  }

  // where the browser's time goes (reported to the admin view: is it hashing, the network, the server?)
  private timing = { hashSeconds: 0, hashBytes: 0, requestSeconds: 0, requestBytes: 0, requests: 0, maxBytes: 0 }
  private lastEmit = 0
  private emitTimer: number | null = null
  private rateEma = 0
  private lastReport = 0

  private emit(patch: Partial<UploadState>) {
    this.state = { ...this.state, ...patch }
    // progress-only updates are drawn at most 4 times a second (each XHR progress event used to
    // re-render the page); phase changes and errors are delivered at once
    const progressOnly = Object.keys(patch).every((k) => k === 'loaded' || k === 'chunksDone' || k === 'chunksTotal')
    const now = performance.now()
    if (progressOnly && now - this.lastEmit < 250) {
      if (this.emitTimer === null) {
        this.emitTimer = window.setTimeout(() => { this.emitTimer = null; this.lastEmit = performance.now()
                                                   this.onChange(this.state) }, 250)
      }
      return
    }
    this.lastEmit = now
    this.onChange(this.state)
  }

  private confirmedBytes(): number {
    return this.confirmed.reduce((acc, [a, b]) => acc + (b - a), 0)
  }

  private progress() {
    let inflight = 0
    this.inflightLoaded.forEach((v) => { inflight += v })
    const done = this.confirmedBytes()
    this.emit({ loaded: Math.min(this.file.size, done + inflight),
                chunksDone: Math.floor(done / Math.max(1, this.reqSize)),
                chunksTotal: Math.ceil(this.file.size / Math.max(1, this.reqSize)) })
  }

  private addConfirmed(a: number, b: number) {
    const all = [...this.confirmed, [a, b]].sort((x, y) => x[0] - y[0])
    const out: number[][] = []
    for (const [x, y] of all) {
      if (out.length && x <= out[out.length - 1][1]) out[out.length - 1][1] = Math.max(out[out.length - 1][1], y)
      else out.push([x, y])
    }
    this.confirmed = out
  }

  private computeMissing() {
    const out: number[][] = []
    let at = 0
    for (const [a, b] of this.confirmed) {
      if (a > at) out.push([at, a])
      at = Math.max(at, b)
    }
    if (at < this.file.size) out.push([at, this.file.size])
    this.missing = out
  }

  private nextPiece(): Piece | null {
    const r = this.requeue.shift()
    if (r) return r
    const head = this.missing[0]
    if (!head) return null
    const a = head[0]
    const b = Math.min(head[1], a + this.reqSize)
    if (b >= head[1]) this.missing.shift()
    else head[0] = b
    return { a, b }
  }

  /** Starts, or continues the same file's earlier upload (the server says which bytes it has). */
  async start(): Promise<UploadState['result']> {
    this.paused = false
    this.cancelled = false
    if (this.pausedAt) { this.pausedMs += Date.now() - this.pausedAt; this.pausedAt = 0 }
    if (!this.startedAt) this.startedAt = Date.now()
    this.emit({ phase: 'starting', error: null })
    try {
      this.session = await call<Session>('POST', '/api/uploads', {
        filename: this.file.name, size: this.file.size, fingerprint: fingerprint(this.file) })
    } catch (e) {
      return this.fail(e)
    }
    const s = this.session
    this.transport = s.transport ?? (isCodespacesOrigin() ? DEFAULT_TRANSPORT
      : { ...DEFAULT_TRANSPORT, profile: 'fallback-default', start_bytes: 8 * MIB, max_bytes: 16 * MIB })
    this.reqSize = startSize(this.transport)
    this.maxParallel = Math.max(1, Math.min(this.transport.concurrency_max, s.concurrency_max || 6, connectionCap()))
    this.target = Math.min(this.maxParallel, Math.max(1, this.transport.concurrency_start))
    this.confirmed = (s.ranges ?? []).map((r) => [r[0], r[1]])
    if (!s.ranges) {
      for (const i of s.received) this.addConfirmed(i * s.chunk_size, Math.min(s.size, (i + 1) * s.chunk_size))
    }
    if (this.confirmedBytes() > 0 && s.status !== 'complete') this.resumes++
    remember(this.file, s.upload_id)
    this.emit({ uploadId: s.upload_id, requestBytes: this.reqSize })
    this.progress()
    if (s.status === 'complete' && s.result) return this.done(s.result)
    return this.run()
  }

  private async run(): Promise<UploadState['result']> {
    this.computeMissing()
    this.requeue = []
    this.emit({ phase: 'uploading' })
    if (!this.releaseHold) this.releaseHold = holdReload()
    let failure: unknown = null
    const lanes = new Set<Promise<void>>()
    for (;;) {
      while (!failure && !this.paused && !this.cancelled && lanes.size < this.target) {
        const piece = this.nextPiece()
        if (!piece) break
        const p: Promise<void> = this.sendWithRetry(piece)
          .catch((e) => { failure = failure ?? e })
          .finally(() => { lanes.delete(p) })
        lanes.add(p)
        this.maxUsed = Math.max(this.maxUsed, lanes.size)
      }
      if (lanes.size === 0) break
      await Promise.race(lanes)
    }
    if (this.cancelled) return this.release(null)
    if (this.paused) { this.emit({ phase: 'paused' }); return this.release(null) }
    if (failure) return this.fail(failure)
    if (this.session?.status === 'complete' && this.session.result) return this.done(this.session.result)
    if (this.confirmedBytes() < this.file.size) {
      // pieces were handed back (413 split / failures) after the lanes ended: go on
      return this.run()
    }
    return this.finalize()
  }

  private release<T>(v: T): T {
    this.releaseHold?.()
    this.releaseHold = null
    return v
  }

  private async sendWithRetry(piece: Piece): Promise<void> {
    const s = this.session!
    const blob = this.file.slice(piece.a, piece.b)
    const th = performance.now()
    const sum = await sha256(blob)
    this.timing.hashSeconds += (performance.now() - th) / 1000
    this.timing.hashBytes += blob.size
    for (let attempt = 0; ; attempt++) {
      try {
        await this.sendPiece(piece, blob, sum, attempt)
        this.addConfirmed(piece.a, piece.b)
        this.measure(blob.size)
        this.provenSize(piece.b - piece.a)
        this.progress()
        if (this.state.phase === 'retrying' || this.state.phase === 'adjusting') this.emit({ phase: 'uploading', retryIn: 0 })
        return
      } catch (e) {
        if (this.cancelled || this.paused) return
        if (e instanceof TooLarge) {
          this.shrink(piece, e.bytes, e.requestId)
          return                                    // the piece was handed back in smaller parts
        }
        const err = e as PolixorApiError
        const cat = classify(err.status ?? 0, err.code)
        if (cat === 'auth') { this.pause(); this.emit({ phase: 'paused' }); return }
        if (err.code === 'upload_closed') {
          // the server finished or closed this session meanwhile: ask it, then go on from its state
          this.session = await call<Session>('GET', `/api/uploads/${s.upload_id}`)
          if (this.session.status === 'complete') return
          throw e
        }
        const resend = transient(cat) || /chunk_size|checksum/.test(err.code || '')
        if (!resend || attempt + 1 >= MAX_ATTEMPTS) throw e
        this.retries++
        this.okStreak = 0
        this.target = Math.max(1, this.target - 1)             // fewer requests in flight on trouble
        const wait = cat === 'rate_limited' ? retryAfter((err.data as any)?.retry_after ?? null, 10)
          : BACKOFF[Math.min(attempt, BACKOFF.length - 1)]
        this.emit({ phase: 'retrying', retryIn: wait })
        await new Promise((r) => setTimeout(r, wait * 1000))
        if (this.cancelled || this.paused) return
      }
    }
  }

  /** A 413: never send that size again on this origin; split the piece and go on. */
  private shrink(piece: Piece, bytes: number, rid = '') {
    const lim = readLimits()
    lim.bad = lim.bad ? Math.min(lim.bad, bytes) : bytes
    if (lim.ok >= lim.bad) lim.ok = 0
    writeLimits(lim)
    this.rejected.push(bytes)
    const smaller = Math.max(this.transport.min_bytes, Math.min(this.reqSize, Math.floor(bytes / 2)))
    if (bytes <= this.transport.min_bytes) {
      // even the smallest request is refused: not a size problem we can solve here
      throw new PolixorApiError({ code: 'upload_proxy_refuses', message: i18n.t('creator.upload.proxyRefuses'),
                                  hint: i18n.t('creator.upload.proxyRefusesHint') }, 413)
    }
    this.reqSize = smaller
    this.okStreak = 0
    const parts: Piece[] = []
    for (let a = piece.a; a < piece.b; a += smaller) parts.push({ a, b: Math.min(piece.b, a + smaller) })
    this.requeue.unshift(...parts)
    this.emit({ phase: 'adjusting', requestBytes: smaller })
    recordFailure({ action: 'upload:adjust', route: `/api/uploads/${this.session?.upload_id}/range`, method: 'PUT',
                    status: 413, elapsed_ms: 0, category: 'payload_too_large', request_id: rid,
                    message: `adapted: ${bytes} → ${smaller} bytes`,
                    detail: { request_bytes: bytes, next_bytes: smaller, profile: this.transport.profile,
                              upload_id: this.session?.upload_id, offset: piece.a } })
  }

  /** A size worked: remember it for this origin, and after a streak try the next one up (within the profile). */
  private provenSize(bytes: number) {
    const lim = readLimits()
    if (bytes > lim.ok && (!lim.bad || bytes < lim.bad)) { lim.ok = bytes; writeLimits(lim) }
    if (bytes < this.reqSize) return
    this.okStreak++
    if (this.okStreak < GROW_AFTER) return
    const next = this.reqSize * 2
    if (next <= this.transport.max_bytes && (!lim.bad || next < lim.bad)) {
      this.reqSize = next
      this.okStreak = 0
      this.emit({ requestBytes: next })
    }
  }

  private sendPiece(piece: Piece, blob: Blob, sum: string, attempt: number): Promise<void> {
    const key = `${piece.a}`
    const path = `/api/uploads/${this.session!.upload_id}/range?offset=${piece.a}`
    const rid = requestId()
    const t0 = performance.now()
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      this.inflight.set(key, xhr)
      xhr.open('PUT', path)
      xhr.setRequestHeader('Content-Type', 'application/octet-stream')
      xhr.setRequestHeader('X-Upload-Length', String(blob.size))
      xhr.setRequestHeader('X-Request-Id', rid)
      if (sum) xhr.setRequestHeader('X-Chunk-Sha256', sum)
      Object.entries(langHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v))
      xhr.timeout = this.transport.request_timeout_s * 1000
      xhr.upload.onprogress = (e) => { this.inflightLoaded.set(key, e.loaded); this.progress() }
      const finish = (err: Error | null) => {
        if (!err) {
          this.timing.requestSeconds += (performance.now() - t0) / 1000
          this.timing.requestBytes += blob.size
          this.timing.requests++
          this.timing.maxBytes = Math.max(this.timing.maxBytes, blob.size)
        }
        this.inflight.delete(key)
        this.inflightLoaded.delete(key)
        this.progress()
        if (!err) { resolve(); return }
        const st = err instanceof PolixorApiError ? err.status : 413
        const code = err instanceof PolixorApiError ? err.code : 'payload_too_large'
        if (code !== 'aborted') {
          recordFailure({ action: 'upload:range', route: path.split('?')[0], method: 'PUT', status: st,
                          elapsed_ms: Math.round(performance.now() - t0), category: classify(st, code), code,
                          request_id: xhr.getResponseHeader('x-request-id') || rid,
                          detail: { request_bytes: blob.size, attempt, profile: this.transport.profile,
                                    concurrency: this.target, upload_id: this.session?.upload_id, offset: piece.a } })
        }
        reject(err)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) return finish(null)
        if (xhr.status === 413) return finish(new TooLarge(blob.size, xhr.getResponseHeader('x-request-id') || rid))
        let d = { code: 'upload_failed', message: i18n.t('creator.upload.chunkFailed', { status: xhr.status }), hint: '' }
        let fromPolixor = false
        try { const b = JSON.parse(xhr.responseText); if (b?.detail?.code) { d = b.detail; fromPolixor = true } } catch { /* proxy page */ }
        if (xhr.status === 401 || (xhr.status === 403 && !fromPolixor)) sessionExpired()
        const e = new PolixorApiError(d, xhr.status)
        ;(e.data as any).retry_after = xhr.getResponseHeader('retry-after')
        finish(e)
      }
      xhr.onerror = () => finish(new PolixorApiError({ code: 'network', message: i18n.t('creator.upload.network'), hint: '' }, 0))
      xhr.ontimeout = () => finish(new PolixorApiError({ code: 'timeout', message: i18n.t('creator.upload.timeout'), hint: '' }, 0))
      xhr.onabort = () => finish(new PolixorApiError({ code: 'aborted', message: '', hint: '' }, 0))
      xhr.send(blob)
    })
  }

  /** Throughput over the last ~8 s; more requests in flight while that helps, fewer when it stops helping. */
  private measure(bytes: number) {
    const now = Date.now()
    this.samples.push({ t: now, bytes })
    this.samples = this.samples.filter((x) => now - x.t < 15000)
    const span = Math.max(1000, now - this.samples[0].t)
    const rate = this.samples.reduce((a, x) => a + x.bytes, 0) * 8 / span / 1000   // Mbit/s
    // speed and time left for the person: a rolling window, smoothed, and only once it means something
    const bps = rate * 1e6 / 8
    this.rateEma = this.rateEma ? this.rateEma * 0.7 + bps * 0.3 : bps
    const left = Math.max(0, this.file.size - this.confirmedBytes())
    const stable = this.samples.length >= 4 && now - this.startedAt > 10_000
    this.emit({ rateBps: Math.round(this.rateEma), etaSeconds: stable && this.rateEma > 0 ? Math.round(left / this.rateEma) : null })
    if (now - this.lastReport > 30_000) { this.lastReport = now; this.report() }
    if (this.samples.length >= 3) this.peakMbps = Math.max(this.peakMbps, rate)
    if (now - this.lastAdjust < 6000 || this.samples.length < 3) return
    this.lastAdjust = now
    if (!this.lastRate || rate > this.lastRate * 1.1) {
      if (this.target < this.maxParallel) this.target++
    } else if (rate < this.lastRate * 0.8 && this.target > 1) {
      this.target--
    }
    this.lastRate = rate
  }

  private report() {
    const s = this.session
    if (!s) return
    const secs = Math.max(0.001, (Date.now() - this.startedAt - this.pausedMs) / 1000)
    const avg = this.confirmedBytes() * 8 / secs / 1e6
    try { if (this.state.phase === 'complete') localStorage.setItem(SPEED_KEY, String(Math.round(avg))) } catch { /* private window */ }
    void call('POST', `/api/uploads/${s.upload_id}/telemetry`, {
      retries: this.retries, resumes: this.resumes, concurrency: this.maxUsed,
      peak_mbps: Math.round(this.peakMbps * 10) / 10, avg_mbps: Math.round(avg * 10) / 10,
      client_seconds: Math.round(secs * 10) / 10, paused_seconds: Math.round(this.pausedMs / 100) / 10,
      hash_worker: usesHashWorker(),
      hash_seconds: Math.round(this.timing.hashSeconds * 100) / 100, hash_bytes: this.timing.hashBytes,
      request_seconds: Math.round(this.timing.requestSeconds * 100) / 100, request_bytes: this.timing.requestBytes,
      requests: this.timing.requests, request_bytes_max: this.timing.maxBytes,
    }, 15_000).catch(() => undefined)
  }

  private async finalize(): Promise<UploadState['result']> {
    this.emit({ phase: 'finalizing', loaded: this.file.size })
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const s = await call<Session>('POST', `/api/uploads/${this.session!.upload_id}/complete`, undefined, 300_000)
        if (s.result) return this.done(s.result)
      } catch (e) {
        const err = e as PolixorApiError
        if (err.code === 'upload_incomplete') {
          // the server misses bytes (e.g. a lost reply): take its state and send what is missing
          try {
            this.session = await call<Session>('GET', `/api/uploads/${this.session!.upload_id}`)
          } catch (e2) { return this.fail(e2) }
          this.confirmed = (this.session.ranges ?? []).map((r) => [r[0], r[1]])
          return this.run()
        }
        const cat = classify(err.status ?? 0, err.code)
        if (err.code === 'upload_busy' || transient(cat)) {
          await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)))
          continue
        }
        return this.fail(e)
      }
    }
    return this.fail(new PolixorApiError({ code: 'timeout', message: i18n.t('creator.upload.timeout'), hint: '' }, 0))
  }

  private done(result: NonNullable<UploadState['result']>): UploadState['result'] {
    remember(this.file, null)
    this.emit({ phase: 'complete', loaded: this.file.size, result, error: null })
    this.report()
    return this.release(result)
  }

  private fail(e: unknown): null {
    const err = e instanceof PolixorApiError ? { code: e.code, message: e.message, hint: e.hint }
      : { code: 'upload_failed', message: String(e), hint: '' }
    this.emit({ phase: 'failed', error: err })
    return this.release(null)
  }

  pause() {
    if (!this.paused) this.pausedAt = Date.now()
    this.paused = true
    this.inflight.forEach((x) => x.abort())
    if (this.state.phase !== 'complete' && this.state.phase !== 'failed') this.emit({ phase: 'paused' })
  }

  async resume(): Promise<UploadState['result']> {
    return this.start()        // the server says which bytes it already has
  }

  async cancel(): Promise<void> {
    this.cancelled = true
    this.inflight.forEach((x) => x.abort())
    remember(this.file, null)
    this.release(null)
    if (this.session && this.state.phase !== 'complete') {
      try { await call('DELETE', `/api/uploads/${this.session.upload_id}`) } catch { /* already gone */ }
    }
  }
}
