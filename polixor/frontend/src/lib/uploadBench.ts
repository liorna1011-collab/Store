// Upload path benchmark (admin → "Measure upload path"). Runs on the real deployment – the same
// browser, network, Codespaces forwarder / proxy and server the customer uses – and answers:
//
//   raw path     PUT to a sink that reads and drops the bytes (the ceiling of the line + proxy)
//   Polixor path the real resumable range endpoint (hash in the worker, server hash, disk write)
//
// over request sizes × parallel requests, each for a fixed time window. The best stable Polixor
// setting is saved; the transport profile starts from it on the next upload.

import { call, connectionCap, sha256 } from './upload'

const MIB = 1024 * 1024
export interface BenchRow {
  path: 'raw' | 'polixor'; request_bytes: number; concurrency: number
  MBps: number; requests: number; errors: number; p95_request_s: number; status?: number
}
export interface BenchResult { rows: BenchRow[]; best: Record<string, number>; protocol: string; host: string }

function randomBlob(bytes: number): Blob {
  const parts: Uint8Array[] = []
  for (let n = 0; n < bytes; n += 65536) {
    const a = new Uint8Array(Math.min(65536, bytes - n)); crypto.getRandomValues(a); parts.push(a)
  }
  return new Blob(parts)
}

function put(url: string, body: Blob, headers: Record<string, string>, onBytes: (n: number) => void,
             live: Set<XMLHttpRequest>): Promise<number> {
  return new Promise((resolve) => {
    const x = new XMLHttpRequest()
    live.add(x)
    let last = 0
    x.open('PUT', url)
    Object.entries(headers).forEach(([k, v]) => x.setRequestHeader(k, v))
    x.upload.onprogress = (e) => { onBytes(e.loaded - last); last = e.loaded }
    const end = (st: number) => { live.delete(x); resolve(st) }
    x.onload = () => end(x.status)
    x.onerror = () => end(0)
    x.onabort = () => end(-1)
    x.send(body)
  })
}

async function window_(seconds: number, conc: number, send: (i: number) => Promise<number>): Promise<{
  requests: number; errors: number; lat: number[]; status: number }> {
  const deadline = performance.now() + seconds * 1000
  let requests = 0, errors = 0, status = 0
  const lat: number[] = []
  const lanes = Array.from({ length: conc }, async (_v, lane) => {
    while (performance.now() < deadline) {
      const t0 = performance.now()
      const st = await send(lane)
      if (st === -1) break                                   // aborted at the end of the window
      if (st === -2) break                                   // nothing left to send
      requests++
      lat.push((performance.now() - t0) / 1000)
      if (st < 200 || st >= 300) { errors++; status = st; if (st === 413) break }
    }
  })
  await Promise.all(lanes)
  return { requests, errors, lat, status }
}

async function measure(path: 'raw' | 'polixor', size: number, conc: number, seconds: number,
                       blob: Blob): Promise<BenchRow> {
  const live = new Set<XMLHttpRequest>()
  let bytes = 0
  const onBytes = (n: number) => { bytes += Math.max(0, n) }
  let send: (lane: number) => Promise<number>
  let cleanup = async () => {}
  const piece = blob.slice(0, size)
  if (path === 'raw') {
    send = () => put('/api/admin/upload-bench/sink', piece, { 'Content-Type': 'application/octet-stream' }, onBytes, live)
  } else {
    const total = 512 * MIB
    const s = await call<{ upload_id: string }>('POST', '/api/uploads', {
      filename: 'polixor-upload-bench.mp4', size: total, fingerprint: `bench-${Date.now()}-${size}-${conc}` })
    let next = 0
    send = async () => {
      if (next + size > total) return -2
      const off = next; next += size
      // the real client hashes every request in the worker before sending it
      const h = await sha256(piece)
      return put(`/api/uploads/${s.upload_id}/range?offset=${off}`, piece, {
        'Content-Type': 'application/octet-stream', 'X-Upload-Length': String(size),
        ...(h ? { 'X-Chunk-Sha256': h } : {}) }, onBytes, live)
    }
    cleanup = async () => { try { await call('DELETE', `/api/uploads/${s.upload_id}`) } catch { /* gone */ } }
  }
  const t0 = performance.now()
  const timer = window.setTimeout(() => live.forEach((x) => x.abort()), seconds * 1000 + 50)
  const w = await window_(seconds, conc, send)
  window.clearTimeout(timer)
  live.forEach((x) => x.abort())
  const el = Math.max(0.001, (performance.now() - t0) / 1000)
  await cleanup()
  w.lat.sort((a, b) => a - b)
  return { path, request_bytes: size, concurrency: conc, MBps: Math.round(bytes / el / 1e4) / 100,
           requests: w.requests, errors: w.errors,
           p95_request_s: w.lat.length ? Math.round(w.lat[Math.max(0, Math.ceil(w.lat.length * 0.95) - 1)] * 100) / 100 : 0,
           ...(w.status ? { status: w.status } : {}) }
}

/** The grid; onRow gets each row as it is measured (the page shows progress). ~2 minutes. */
export async function measureUploadPath(onRow: (r: BenchRow) => void, opts: { seconds?: number } = {}): Promise<BenchResult> {
  const seconds = opts.seconds ?? 6
  const t = await call<{ max_bytes: number }>('GET', '/api/uploads/transport')
  const sizes = [4 * MIB, 8 * MIB, 16 * MIB].filter((s) => s <= (t.max_bytes || 8 * MIB))
  const concs = [1, 2, 3, 4, 6, 8]
  const blob = randomBlob(Math.max(...sizes))
  const rows: BenchRow[] = []
  for (const size of sizes) {
    for (const c of concs) {
      const r = await measure('raw', size, c, seconds, blob)
      rows.push(r); onRow(r)
      if (r.status === 413) break
    }
  }
  // the Polixor path at the best raw settings the browser can use without starving the app
  const cap = connectionCap()
  const usable = rows.filter((r) => !r.errors && r.concurrency <= cap).sort((a, b) => b.MBps - a.MBps)
  const tried = new Set<string>()
  for (const r of usable.slice(0, 4)) {
    const k = `${r.request_bytes}:${r.concurrency}`
    if (tried.has(k)) continue
    tried.add(k)
    const p = await measure('polixor', r.request_bytes, r.concurrency, seconds + 2, blob)
    rows.push(p); onRow(p)
  }
  const raw = new Map(rows.filter((r) => r.path === 'raw').map((r) => [`${r.request_bytes}:${r.concurrency}`, r.MBps]))
  const best = rows.filter((r) => r.path === 'polixor' && !r.errors).sort((a, b) => b.MBps - a.MBps)[0]
  const bestRaw = rows.filter((r) => r.path === 'raw' && !r.errors).sort((a, b) => b.MBps - a.MBps)[0]
  const proto = ((performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined)?.nextHopProtocol) || ''
  const result: BenchResult = {
    rows, protocol: proto, host: location.host,
    best: best ? { request_bytes: best.request_bytes, concurrency: best.concurrency, polixor_MBps: best.MBps,
                   raw_MBps_same_setting: raw.get(`${best.request_bytes}:${best.concurrency}`) ?? 0,
                   raw_MBps_ceiling: bestRaw?.MBps ?? 0,
                   overhead_pct: Math.round((1 - best.MBps / Math.max(0.01, raw.get(`${best.request_bytes}:${best.concurrency}`) ?? best.MBps)) * 100) }
               : {},
  }
  await call('POST', '/api/admin/upload-bench/result', result)
  return result
}
