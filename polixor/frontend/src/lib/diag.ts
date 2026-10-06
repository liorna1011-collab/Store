// Failure classification and internal diagnostics for user actions.
//
// Every failed request is classified the same way everywhere (API client, uploader):
//   auth               401/403 – the session (ours, or the Codespaces port's) must be renewed
//   timeout            408, or no answer within the request's time limit
//   payload_too_large  413 – a proxy refused the request size; the sender must send smaller
//   rate_limited       429 – wait (Retry-After) and try again
//   proxy              502/503/504 – a proxy or the server is briefly unavailable
//   network            the request never reached a server (offline, connection reset)
//   validation         another 4xx – the server explains what is wrong; repeating cannot help
//   server             5xx – our failure
// and recorded (no secrets: no bodies, no headers, no tokens) to the server's event log, which
// the admin view shows – nobody needs the browser's developer tools to know why a button failed.

export type FailureCategory = 'auth' | 'timeout' | 'payload_too_large' | 'rate_limited' | 'proxy' | 'network'
  | 'validation' | 'server' | 'stale_build' | 'not_found' | 'conflict' | 'unknown'

export function classify(status: number, code = ''): FailureCategory {
  if (code === 'timeout') return 'timeout'
  if (code === 'network') return 'network'
  if (status === 401 || status === 403) return code === 'admin_required' ? 'validation' : 'auth'
  if (status === 408) return 'timeout'
  if (status === 413) return 'payload_too_large'
  if (status === 429) return 'rate_limited'
  if (status === 502 || status === 503 || status === 504) return 'proxy'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status >= 500) return 'server'
  if (status >= 400) return 'validation'
  return status === 0 ? 'network' : 'unknown'
}

/** Worth trying again by itself (the same request may work in a moment). */
export function transient(cat: FailureCategory): boolean {
  return cat === 'timeout' || cat === 'rate_limited' || cat === 'proxy' || cat === 'network'
}

/** Seconds a 429/503 asks us to wait (Retry-After), capped. */
export function retryAfter(header: string | null, fallback: number): number {
  const n = Number(header)
  return Number.isFinite(n) && n > 0 ? Math.min(120, n) : fallback
}

declare const __BUILD_ID__: string
export const CLIENT_BUILD: string = typeof __BUILD_ID__ !== 'undefined' ? __BUILD_ID__ : 'dev'

export function requestId(): string {
  const a = new Uint8Array(6)
  try { crypto.getRandomValues(a) } catch { for (let i = 0; i < a.length; i++) a[i] = Math.floor(Math.random() * 256) }
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('')
}

export interface FailureEvent {
  action: string
  route: string
  method: string
  status: number
  elapsed_ms: number
  category: FailureCategory
  code?: string
  request_id?: string
  project_id?: string
  message?: string
  detail?: Record<string, unknown>
}

const KEY = 'polixor.pendingEvents'
let queue: FailureEvent[] = []          // only when storage is unavailable (private window)
let timer: number | null = null
const recentLocal: (FailureEvent & { at: number })[] = []

function projectFromPath(path: string): string {
  const m = path.match(/\/(?:projects|jobs)\/([0-9a-f]{8,32})/i)
  return m ? m[1] : ''
}

/** Records a failed action; sent in small batches (kept for later when the session is gone). */
export function recordFailure(e: FailureEvent): void {
  const ev = { ...e, project_id: e.project_id || projectFromPath(e.route) || projectFromPath(location.pathname) }
  recentLocal.unshift({ ...ev, at: Date.now() })
  recentLocal.splice(30)
  if (e.route.startsWith('/api/client-events')) return
  // stored at once: leaving or reloading the page right after a failure never loses it
  try { stash([ev]) } catch { queue.push(ev) }
  if (timer === null) timer = window.setTimeout(flush, 1500)
}

// the page is being left: send what is pending without waiting (keepalive survives the unload)
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => { void flush(true) })
}

export function recentFailures() { return recentLocal.slice() }

function stash(evs: FailureEvent[]) {
  try {
    const old = JSON.parse(localStorage.getItem(KEY) || '[]') as FailureEvent[]
    localStorage.setItem(KEY, JSON.stringify([...old, ...evs].slice(-50)))
  } catch { /* private window */ }
}

export async function flush(leaving = false): Promise<void> {
  if (timer !== null) { window.clearTimeout(timer); timer = null }
  let pending: FailureEvent[] = []
  try { pending = JSON.parse(localStorage.getItem(KEY) || '[]'); localStorage.removeItem(KEY) } catch { /* */ }
  const batch = [...pending, ...queue].slice(-50)
  queue = []
  if (!batch.length) return
  try {
    const ctrl = new AbortController()
    const t = window.setTimeout(() => ctrl.abort(), 10_000)
    const res = await fetch('/api/client-events', {
      method: 'POST', signal: leaving ? undefined : ctrl.signal, keepalive: leaving,
      headers: { 'Content-Type': 'application/json', 'X-Polixor-Request': '1' },
      body: JSON.stringify(batch.map((b) => ({ ...b, page: location.pathname, build: CLIENT_BUILD }))),
    })
    window.clearTimeout(t)
    if (!res.ok) stash(batch)              // signed out / offline: sent after the next sign-in
  } catch {
    stash(batch)
  }
}

// ---- build version: a page running older JS than the server serves is stale ----
let staleSignalled = false
export function noteServerBuild(serverBuild: string | null): void {
  if (!serverBuild || CLIENT_BUILD === 'dev' || staleSignalled) return
  if (serverBuild !== CLIENT_BUILD) {
    staleSignalled = true
    window.dispatchEvent(new CustomEvent('polixor:new-version', { detail: { server: serverBuild } }))
  }
}

// ---- work that a reload would interrupt (an upload in flight) ----
let busy = 0
export function holdReload(): () => void {
  busy++
  let done = false
  return () => { if (!done) { done = true; busy = Math.max(0, busy - 1) } }
}
export function reloadIsSafe(): boolean { return busy === 0 }
