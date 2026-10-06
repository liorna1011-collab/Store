// Production uploads: the browser sends the file DIRECTLY to object storage (S3 / R2 / B2 …) as
// an S3 multipart upload. Polixor only hands out short-lived signed URLs for single parts and
// completes the upload – no video byte passes through the Polixor server, so the speed is what
// the customer's connection and the storage region allow.
//
// Resume: the storage – not this browser – says which parts it already has (GET …/parts), so a
// refresh, a closed laptop, Wi-Fi loss or a new sign-in continue with the missing parts only.
// A part URL that expired (403) is signed again; other failures are retried with backoff and
// fewer parts in flight. No storage credential ever reaches the browser.

import i18n from '../i18n'
import { PolixorApiError } from './api'
import { call, fingerprint, remember, type UploadState } from './upload'

interface DirectSession {
  upload_id: string
  size: number
  part_bytes: number
  parts_total: number
  status: string
  result: UploadState['result']
  parts?: { part_number: number; size: number }[]
}

const BACKOFF = [1, 2, 4, 8, 15, 30]
const MAX_ATTEMPTS = 8

export class DirectUpload {
  state: UploadState
  private session: DirectSession | null = null
  private done = new Set<number>()
  private queue: number[] = []
  private urls = new Map<number, { url: string; at: number }>()
  private inflight = new Map<number, XMLHttpRequest>()
  private loaded = new Map<number, number>()
  private paused = false
  private cancelled = false
  private target = 4
  private maxParallel = 8
  private samples: { t: number; bytes: number }[] = []
  private rateEma = 0
  private startedAt = 0
  private lastEmit = 0

  constructor(private file: File, private onChange: (s: UploadState) => void) {
    this.state = { phase: 'starting', uploadId: '', loaded: 0, total: file.size, chunksDone: 0, chunksTotal: 0,
                   retryIn: 0, requestBytes: 0, rateBps: 0, etaSeconds: null, error: null, result: null }
  }

  private emit(patch: Partial<UploadState>, force = false) {
    this.state = { ...this.state, ...patch }
    const now = performance.now()
    if (!force && now - this.lastEmit < 250) return
    this.lastEmit = now
    this.onChange(this.state)
  }

  private bytesDone(): number {
    const s = this.session!
    let n = 0
    this.done.forEach((p) => { n += this.partSize(p) })
    this.loaded.forEach((v) => { n += v })
    return Math.min(s.size, n)
  }

  private partSize(n: number): number {
    const s = this.session!
    return Math.min(s.part_bytes, s.size - (n - 1) * s.part_bytes)
  }

  async start(): Promise<UploadState['result']> {
    this.paused = false
    this.cancelled = false
    if (!this.startedAt) this.startedAt = Date.now()
    this.emit({ phase: 'starting', error: null }, true)
    try {
      const s0 = await call<DirectSession>('POST', '/api/uploads/direct', {
        filename: this.file.name, size: this.file.size, fingerprint: fingerprint(this.file),
        content_type: this.file.type || 'video/mp4' })
      // what the storage already holds (a resumed upload sends only the rest)
      this.session = await call<DirectSession>('GET', `/api/uploads/direct/${s0.upload_id}/parts`)
    } catch (e) {
      return this.fail(e)
    }
    const s = this.session
    remember(this.file, s.upload_id)
    if (s.status === 'complete' && s.result) return this.finish(s.result)
    this.done = new Set((s.parts || []).filter((p) => p.size === this.partSize(p.part_number)).map((p) => p.part_number))
    this.queue = []
    for (let n = 1; n <= s.parts_total; n++) if (!this.done.has(n)) this.queue.push(n)
    this.emit({ uploadId: s.upload_id, requestBytes: s.part_bytes, chunksTotal: s.parts_total,
                chunksDone: this.done.size, loaded: this.bytesDone(), phase: 'uploading' }, true)
    return this.run()
  }

  private async run(): Promise<UploadState['result']> {
    let failure: unknown = null
    const lanes = new Set<Promise<void>>()
    for (;;) {
      while (!failure && !this.paused && !this.cancelled && lanes.size < this.target && this.queue.length) {
        const n = this.queue.shift()!
        const p: Promise<void> = this.sendPart(n).catch((e) => { failure = failure ?? e }).finally(() => { lanes.delete(p) })
        lanes.add(p)
      }
      if (!lanes.size) break
      await Promise.race(lanes)
    }
    if (this.cancelled) return null
    if (this.paused) { this.emit({ phase: 'paused' }, true); return null }
    if (failure) return this.fail(failure)
    return this.complete()
  }

  private async url(n: number): Promise<string> {
    const hit = this.urls.get(n)
    if (hit && Date.now() - hit.at < 10 * 60_000) return hit.url
    // sign the next parts in one request (≤ 20)
    const want = [n, ...this.queue.slice(0, 19)].filter((x) => !this.urls.has(x) || Date.now() - this.urls.get(x)!.at > 10 * 60_000)
    const r = await call<{ urls: Record<string, string> }>('POST', `/api/uploads/direct/${this.session!.upload_id}/sign`,
                                                           { parts: want })
    const at = Date.now()
    Object.entries(r.urls).forEach(([k, v]) => this.urls.set(Number(k), { url: v, at }))
    return this.urls.get(n)!.url
  }

  private async sendPart(n: number): Promise<void> {
    const s = this.session!
    const a = (n - 1) * s.part_bytes
    const blob = this.file.slice(a, Math.min(s.size, a + s.part_bytes))
    for (let attempt = 0; ; attempt++) {
      try {
        const url = await this.url(n)
        await this.put(n, url, blob)
        this.done.add(n)
        this.measure(blob.size)
        this.emit({ chunksDone: this.done.size, loaded: this.bytesDone() })
        return
      } catch (e) {
        if (this.cancelled || this.paused) return
        const st = (e as PolixorApiError).status ?? 0
        if (st === 403) this.urls.delete(n)                 // the signed URL expired: sign again
        if (attempt + 1 >= MAX_ATTEMPTS) throw e
        this.target = Math.max(1, this.target - 1)
        const wait = BACKOFF[Math.min(attempt, BACKOFF.length - 1)]
        this.emit({ phase: 'retrying', retryIn: wait }, true)
        await new Promise((r) => setTimeout(r, wait * 1000))
        if (this.cancelled || this.paused) return
        this.emit({ phase: 'uploading', retryIn: 0 }, true)
      }
    }
  }

  private put(n: number, url: string, blob: Blob): Promise<void> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      this.inflight.set(n, xhr)
      xhr.open('PUT', url)                        // no cookies, no Polixor headers: the URL is the permission
      xhr.timeout = 15 * 60_000
      xhr.upload.onprogress = (e) => { this.loaded.set(n, e.loaded); this.emit({ loaded: this.bytesDone() }) }
      const end = (err: Error | null) => {
        this.inflight.delete(n)
        this.loaded.delete(n)
        if (err) reject(err); else resolve()
      }
      xhr.onload = () => (xhr.status >= 200 && xhr.status < 300) ? end(null)
        : end(new PolixorApiError({ code: 'storage_error', message: i18n.t('creator.upload.chunkFailed', { status: xhr.status }),
                                    hint: '' }, xhr.status))
      xhr.onerror = () => end(new PolixorApiError({ code: 'network', message: i18n.t('creator.upload.network'), hint: '' }, 0))
      xhr.ontimeout = () => end(new PolixorApiError({ code: 'timeout', message: i18n.t('creator.upload.timeout'), hint: '' }, 0))
      xhr.onabort = () => end(new PolixorApiError({ code: 'aborted', message: '', hint: '' }, 0))
      xhr.send(blob)
    })
  }

  private measure(bytes: number) {
    const now = Date.now()
    this.samples.push({ t: now, bytes })
    this.samples = this.samples.filter((x) => now - x.t < 15000)
    const span = Math.max(1000, now - this.samples[0].t)
    const bps = this.samples.reduce((acc, x) => acc + x.bytes, 0) / (span / 1000)
    const prev = this.rateEma
    this.rateEma = prev ? prev * 0.7 + bps * 0.3 : bps
    // more parts in flight while that raises the speed
    if (this.samples.length >= 3 && bps > prev * 1.1 && this.target < this.maxParallel) this.target++
    const left = Math.max(0, this.file.size - this.bytesDone())
    const stable = this.samples.length >= 3 && now - this.startedAt > 10_000
    this.emit({ rateBps: Math.round(this.rateEma), etaSeconds: stable ? Math.round(left / Math.max(1, this.rateEma)) : null })
  }

  private async complete(): Promise<UploadState['result']> {
    this.emit({ phase: 'finalizing', loaded: this.file.size }, true)
    try {
      const s = await call<DirectSession>('POST', `/api/uploads/direct/${this.session!.upload_id}/complete`, undefined, 300_000)
      if (s.result) return this.finish(s.result)
      return this.fail(new Error('no result'))
    } catch (e) {
      if ((e as PolixorApiError).code === 'upload_incomplete') return this.start()   // ask the storage again
      return this.fail(e)
    }
  }

  private finish(result: NonNullable<UploadState['result']>): UploadState['result'] {
    remember(this.file, null)
    this.emit({ phase: 'complete', loaded: this.file.size, result, error: null }, true)
    return result
  }

  private fail(e: unknown): null {
    const err = e instanceof PolixorApiError ? { code: e.code, message: e.message, hint: e.hint }
      : { code: 'upload_failed', message: String(e), hint: '' }
    this.emit({ phase: 'failed', error: err }, true)
    return null
  }

  pause() {
    this.paused = true
    this.inflight.forEach((x) => x.abort())
    if (this.state.phase !== 'complete' && this.state.phase !== 'failed') this.emit({ phase: 'paused' }, true)
  }

  async resume(): Promise<UploadState['result']> { return this.start() }

  async cancel(): Promise<void> {
    this.cancelled = true
    this.inflight.forEach((x) => x.abort())
    remember(this.file, null)
    if (this.session && this.state.phase !== 'complete') {
      try { await call('DELETE', `/api/uploads/direct/${this.session.upload_id}`) } catch { /* gone */ }
    }
  }
}
