// Resumable, chunked upload of a source video (backend: services/uploads.py).
//
// The file is never read whole: each chunk is a File.slice() sent as its own PUT, a few
// at a time. A chunk that fails is retried with backoff; only when its retries run out
// does the upload stop – with the server's reason – and it can be resumed later: the
// server keeps every chunk it has, and choosing the same file again (even after a page
// refresh) continues from there. Progress is counted in bytes the server confirmed plus
// bytes in flight – never estimated.

import i18n from '../i18n'
import { PolixorApiError } from './api'

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
  upload_id: string
  size: number
  chunk_size: number
  total_chunks: number
  received: number[]
  status: string
  result: UploadState['result']
  error: string
}

const PARALLEL = 3
const MAX_ATTEMPTS = 8
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

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method, headers: { 'Content-Type': 'application/json', ...langHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new PolixorApiError({ code: 'network', message: i18n.t('creator.upload.network'), hint: '' }, 0)
  }
  const text = await res.text()
  const data = text ? JSON.parse(text) : undefined
  if (!res.ok) {
    const d = data?.detail && typeof data.detail === 'object' ? data.detail
      : { code: 'http_error', message: i18n.t('common.errors.http', { status: res.status }), hint: '' }
    throw new PolixorApiError(d, res.status)
  }
  return data as T
}

async function sha256(blob: Blob): Promise<string> {
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
    this.emit({ phase: 'starting', error: null })
    try {
      this.session = await call<Session>('POST', '/api/uploads', {
        filename: this.file.name, size: this.file.size, fingerprint: fingerprint(this.file) })
    } catch (e) {
      return this.fail(e)
    }
    const s = this.session
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
    const worker = async () => {
      while (!failure && !this.paused && !this.cancelled && next < missing.length) {
        const index = missing[next++]
        try { await this.sendWithRetry(index) } catch (e) { failure = e }
      }
    }
    await Promise.all(Array.from({ length: Math.min(PARALLEL, missing.length) }, worker))
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
      if (sum) xhr.setRequestHeader('X-Chunk-Sha256', sum)
      Object.entries(langHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v))
      xhr.timeout = 10 * 60 * 1000
      xhr.upload.onprogress = (e) => { this.inflightLoaded.set(index, e.loaded); this.progress() }
      const finish = (err: PolixorApiError | null) => { this.inflight.delete(index); err ? reject(err) : resolve() }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) return finish(null)
        let d = { code: 'upload_failed', message: i18n.t('creator.upload.chunkFailed', { status: xhr.status }), hint: '' }
        try { const b = JSON.parse(xhr.responseText); if (b?.detail?.code) d = b.detail } catch { /* proxy page */ }
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

  private done(result: NonNullable<UploadState['result']>): UploadState['result'] {
    remember(this.file, null)
    this.emit({ phase: 'complete', loaded: this.file.size, result, error: null })
    return result
  }

  private fail(e: unknown): null {
    const err = e instanceof PolixorApiError ? { code: e.code, message: e.message, hint: e.hint }
      : { code: 'upload_failed', message: String(e), hint: '' }
    this.emit({ phase: 'failed', error: err })
    return null
  }

  pause() {
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
