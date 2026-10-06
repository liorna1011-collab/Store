// לקוח API מוקלד. כל בקשה שולחת את שפת הממשק (X-Polixor-Lang), ולכן
// השגיאות וההודעות מהשרת מגיעות בשפה של המשתמש.

import i18n, { currentLang } from '../i18n'
import type {
  ApiError, Clip, Cue, EditStylesResponse, FontsResponse, GeneratedImage, ImagePlacement,
  ImageProvidersResponse, Job, LiveDetectResult, LiveStatus, PresetsResponse, ProbeResult,
  Project, ProjectDefaults, ResolveResult, SettingsResponse, SubtitlePreview,
  SuggestVisualsResponse, SystemInfo, TimelineData, ClipReview, ProofreadResponse,
  NotificationList, PublishPlatform, SocialAccount, PreflightResult, PublishTargetIn,
  PublishHistory, PublishConfigGroup, TikTokCreatorDetails,
  StudioCaps, StudioThread, StudioThreadSummary, StudioMessage,
  StudioGoal, ContentProfile, QualityMode, StudioResults, StudioReview, StudioMetrics, ProjectStorage, StudioDiagnostics,
  Usage, UsageCheck,
} from './types'

import { classify, noteServerBuild, recordFailure, requestId, retryAfter, transient, type FailureCategory } from './diag'

const BASE = ''

export function langHeaders(): Record<string, string> {
  // X-Polixor-Request: כותרת מותאמת שאתר זר לא יכול לשלוח (הגנת CSRF בשרת)
  return { 'X-Polixor-Lang': currentLang(), 'X-Polixor-Request': '1' }
}

const tt = (key: string) => i18n.t(`common.errors.${key}`)

export class PolixorApiError extends Error {
  code: string
  hint: string
  detail: string
  status: number
  /** כל התשובה (למשל פירוט הבדיקה המוקדמת של פרסום) */
  data: Record<string, any>
  /** shown to the user as "Ref …" and recorded in the admin view */
  requestId = ''
  get category(): FailureCategory { return classify(this.status, this.code) }

  constructor(err: ApiError, status: number) {
    super(err.message || tt('unknown'))
    this.name = 'PolixorApiError'
    this.code = err.code || 'unknown'
    this.hint = err.hint || ''
    this.detail = err.detail || ''
    this.status = status
    this.data = err as unknown as Record<string, any>
  }
}

/** אפשרויות לבקשה בודדת */
export interface RequestOpts {
  /** מקסימום זמן לבקשה (מילישניות). ברירת מחדל: 30 שניות */
  timeoutMs?: number
  /** ניסיונות חוזרים לבקשות קריאה בלבד (GET) כשהרשת/השרת נפלו לרגע */
  retries?: number
}

const DEFAULT_TIMEOUT = 30_000
/** קריאות שמחכות לשירות חיצוני (קישור, מודל, יצירת תמונה) */
const LONG: RequestOpts = { timeoutMs: 120_000 }

/**
 * Every request ends: on time (timeout → a clear error, never an endless spinner), on
 * the caller's abort, or with an answer. Failures are handled by kind (lib/diag.ts):
 * GET requests are retried on a network blip, a timeout, a proxy 502/503/504 and a 429
 * (after Retry-After); writes are never repeated automatically. An expired sign-in (ours,
 * or the Codespaces port's) opens the session dialog. Every failure is recorded for the
 * admin view with a request id the user also sees ("Ref").
 */
async function request<T>(path: string, init?: RequestInit, opts: RequestOpts = {}): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase()
  const retries = opts.retries ?? (method === 'GET' ? 2 : 0)
  for (let attempt = 0; ; attempt++) {
    try {
      return await requestOnce<T>(path, init, opts.timeoutMs ?? DEFAULT_TIMEOUT, attempt)
    } catch (e) {
      const err = e as PolixorApiError
      if (!(err instanceof PolixorApiError) || attempt >= retries || init?.signal?.aborted) throw e
      const cat = classify(err.status, err.code)
      if (!transient(cat)) throw e
      const wait = cat === 'rate_limited' ? retryAfter(String(err.data?.retry_after ?? ''), 5) * 1000 : 600 * 2 ** attempt
      await new Promise((r) => setTimeout(r, wait))
    }
  }
}

/** A request that never reached Polixor: is it the Codespaces port's own sign-in that expired? */
export async function proxyAuthExpired(): Promise<boolean> {
  try {
    const r = await fetch('/api/health', { redirect: 'manual', cache: 'no-store' })
    return r.type === 'opaqueredirect' || r.status === 401 || r.status === 403
  } catch {
    return false
  }
}

async function requestOnce<T>(path: string, init: RequestInit | undefined, timeoutMs: number,
                              attempt = 0): Promise<T> {
  const ctrl = new AbortController()
  let timedOut = false
  const timer = window.setTimeout(() => { timedOut = true; ctrl.abort() }, timeoutMs)
  const outer = init?.signal
  const onOuterAbort = () => ctrl.abort()
  if (outer) {
    if (outer.aborted) ctrl.abort()
    else outer.addEventListener('abort', onOuterAbort, { once: true })
  }
  const method = (init?.method || 'GET').toUpperCase()
  const rid = requestId()
  const t0 = performance.now()
  const fail = (err: PolixorApiError, rId = rid): PolixorApiError => {
    recordFailure({ action: `${method} ${path.split('?')[0]}`, route: path.split('?')[0], method,
                    status: err.status, elapsed_ms: Math.round(performance.now() - t0),
                    category: classify(err.status, err.code), code: err.code, request_id: rId,
                    message: err.message, detail: { attempt } })
    err.requestId = rId
    return err
  }
  let res: Response
  try {
    try {
      res = await fetch(`${BASE}${path}`, {
        ...init,
        signal: ctrl.signal,
        headers: {
          ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
          ...langHeaders(),
          'X-Request-Id': rid,
          ...(init?.headers || {}),
        },
      })
    } catch (e) {
      if (timedOut) {
        throw fail(new PolixorApiError({ code: 'timeout', message: tt('timeout'), hint: tt('timeoutHint') }, 0))
      }
      // ביטול מכוון (AbortController) אינו תקלת רשת
      if ((e as Error)?.name === 'AbortError') throw e
      // the Codespaces forwarder answers an expired port sign-in with a redirect to GitHub,
      // which fetch reports as a network failure: tell the two apart
      if (await proxyAuthExpired()) {
        sessionExpired()
        throw fail(new PolixorApiError({ code: 'session_expired', message: tt('sessionExpired'), hint: tt('sessionExpiredHint') }, 401))
      }
      throw fail(new PolixorApiError(
        { code: 'network', message: tt('network'), hint: tt('networkHint') },
        0,
      ))
    }
    noteServerBuild(res.headers.get('x-polixor-build'))
    const serverRid = res.headers.get('x-request-id') || rid

    if (!res.ok) {
      let payload: ApiError = {
        code: 'http_error', message: i18n.t('common.errors.http', { status: res.status }), hint: '' }
      let fromPolixor = false
      try {
        const body = await res.json()
        if (body?.detail && typeof body.detail === 'object') { payload = body.detail; fromPolixor = true }
        else if (body?.code) { payload = body; fromPolixor = true }
      } catch { /* תשובה שאינה JSON – a proxy answered, not Polixor */ }
      const cat = classify(res.status, payload.code)
      if (cat === 'auth' && (res.status === 401 || !fromPolixor || payload.code === 'auth_required')) {
        sessionExpired()
        payload = { code: 'session_expired', message: tt('sessionExpired'), hint: tt('sessionExpiredHint') }
      } else if (!fromPolixor) {
        // never a raw proxy page or status line: what happened, in words
        const key = cat === 'payload_too_large' ? 'tooLarge' : cat === 'rate_limited' ? 'rateLimited'
          : cat === 'proxy' ? 'proxy' : cat === 'timeout' ? 'timeout' : res.status >= 500 ? 'server' : ''
        if (key) payload = { code: cat, message: tt(key), hint: tt(`${key}Hint`) }
      }
      const err = new PolixorApiError(payload, res.status)
      if (cat === 'rate_limited') err.data.retry_after = res.headers.get('retry-after')
      throw fail(err, serverRid)
    }

    if (res.status === 204) return undefined as T
    const text = await res.text()
    return (text ? JSON.parse(text) : undefined) as T
  } catch (e) {
    if (timedOut && !(e instanceof PolixorApiError)) {
      throw fail(new PolixorApiError({ code: 'timeout', message: tt('timeout'), hint: tt('timeoutHint') }, 0))
    }
    throw e
  } finally {
    window.clearTimeout(timer)
    outer?.removeEventListener('abort', onOuterAbort)
  }
}

/**
 * הכניסה פגה (סביבה מוגנת בסיסמה). לא מעבירים דף מיד – זה היה זורק טופס
 * שהמשתמש באמצע מילויו. האפליקציה מציגה "פג תוקף הכניסה" עם כפתור כניסה
 * שחוזר לאותו מסך; העלאות נעצרות ונשמרות להמשך.
 */
let lastExpired = 0
export function sessionExpired(): void {
  if (Date.now() - lastExpired < 5000) return       // one dialog for a burst of failing requests
  lastExpired = Date.now()
  window.dispatchEvent(new CustomEvent('polixor:session-expired'))
}

export function loginUrl(): string {
  const next = window.location.pathname + window.location.search
  return `/login?next=${encodeURIComponent(next)}`
}

export function reexportState(clip: Clip): string {
  return String(((clip.render_params as Record<string, any> | undefined)?.reexport || {}).state || '')
}

const get = <T>(p: string, opts?: RequestOpts) => request<T>(p, undefined, opts)
const post = <T>(p: string, body?: unknown, opts?: RequestOpts) =>
  request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }, opts)
const put = <T>(p: string, body: unknown) =>
  request<T>(p, { method: 'PUT', body: JSON.stringify(body) })
const patch = <T>(p: string, body: unknown) =>
  request<T>(p, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T>(p: string) => request<T>(p, { method: 'DELETE' })

export const api = {
  // --- מקורות ---
  resolve: (url: string) => post<ResolveResult>('/api/sources/resolve', { url }, LONG),
  probe: (url: string) => post<ProbeResult>('/api/sources/probe', { url }, LONG),

  upload: async (
    file: File,
    onProgress?: (fraction: number) => void,
    signal?: AbortSignal,
  ): Promise<{ upload_token: string; title: string; duration: number; file_size: number }> =>
    new Promise((resolve, reject) => {
      const form = new FormData()
      form.append('file', file)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${BASE}/api/upload`)
      Object.entries(langHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v))
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText))
        } else {
          let payload: ApiError = { code: 'upload_failed', message: tt('uploadFailed'), hint: '' }
          try {
            const b = JSON.parse(xhr.responseText)
            payload = b?.detail && typeof b.detail === 'object' ? b.detail : payload
          } catch { /* ignore */ }
          if (xhr.status === 401 && payload.code === 'auth_required') sessionExpired()
          reject(new PolixorApiError(payload, xhr.status))
        }
      }
      xhr.onerror = () =>
        reject(new PolixorApiError(
          { code: 'network', message: tt('uploadNetwork'), hint: '' }, 0))
      xhr.onabort = () =>
        reject(new PolixorApiError({ code: 'aborted', message: tt('uploadAborted'), hint: '' }, 0))
      if (signal) {
        if (signal.aborted) { xhr.abort(); return }
        signal.addEventListener('abort', () => xhr.abort(), { once: true })
      }
      xhr.send(form)
    }),

  // --- פרויקטים ---
  projectDefaults: () => get<ProjectDefaults>(`/api/projects/defaults?lang=${currentLang()}`),
  createProject: (body: {
    source: { type: 'upload' | 'url'; upload_token?: string; upload_id?: string; url?: string
      section?: { start: number; end: number } | null; live_capture_seconds?: number | null }
    title?: string; ui_language: string; content_language: string
    preview?: Record<string, unknown> | null
    vocabulary?: string
    goal?: StudioGoal | null
    content_profile?: ContentProfile
    quality?: QualityMode
    editorial_overlay?: boolean
    clip_count?: number
    clip_length?: 'short' | 'medium' | 'long'
    idempotency_key?: string
  }) => post<Project>('/api/projects', body, LONG),

  // ---- Polixor Studio ----
  studioResults: (id: string) => get<StudioResults>(`/api/studio/projects/${id}/results`),
  saveReview: (clipId: string, body: Partial<StudioReview>) =>
    put<{ clip_id: string; review: StudioReview }>(`/api/clips/${clipId}/review`, body),
  reviewsExportUrl: (id: string) => `${BASE}/api/studio/projects/${id}/reviews.json`,
  studioDownloadUrl: (id: string, kind: 'shorts' | 'long' | 'package') =>
    `${BASE}/api/studio/projects/${id}/download?kind=${kind}`,
  studioMetrics: () => get<StudioMetrics>('/api/studio/metrics'),
  studioDiagnostics: (id: string) => get<StudioDiagnostics>(`/api/studio/projects/${id}/diagnostics`),
  projectStorage: (id: string) => get<ProjectStorage>(`/api/studio/projects/${id}/storage`),
  cleanupProject: (id: string, dryRun = false) =>
    post<{ deleted: number; freed_bytes: number }>(`/api/studio/projects/${id}/storage/cleanup?dry_run=${dryRun}`),
  listProjects: (limit = 30, offset = 0) =>
    get<{ items: Project[]; total: number; next_offset: number | null }>(`/api/projects?limit=${limit}&offset=${offset}`),
  getProject: (id: string) => get<Project>(`/api/projects/${id}`),
  projectClips: (id: string) => get<Clip[]>(`/api/projects/${id}/clips`),
  projectClipReview: (id: string) => get<ClipReview>(`/api/projects/${id}/clip-review`),
  clipProofread: (id: string) => get<ProofreadResponse>(`/api/clips/${id}/proofread`),
  patchProject: (id: string, body: Record<string, unknown>) =>
    patch<Project>(`/api/projects/${id}`, body),
  analyzeProject: (id: string) => post<Project>(`/api/projects/${id}/analyze`),
  generateProject: (id: string, body: { mode?: string; config?: object }) =>
    post<Project>(`/api/projects/${id}/generate`, body),
  cancelProject: (id: string) => post<Project>(`/api/projects/${id}/cancel`),
  resumeProject: (id: string) => post<Project>(`/api/projects/${id}/resume`),

  // --- plan usage: source video minutes only ---
  usage: () => get<Usage>('/api/usage'),
  usageCheck: (durationSeconds: number) =>
    post<UsageCheck>('/api/usage/check', { duration_seconds: durationSeconds }),

  // --- admin / developer (needs the admin token; never part of the customer interface) ---
  adminSession: () => get<{ admin: boolean }>('/api/admin/session'),
  adminSignIn: (token: string) => post<{ admin: boolean }>('/api/admin/session', { token }),
  adminSignOut: () => del<{ admin: boolean }>('/api/admin/session'),
  adminOverview: () => get<Record<string, any>>('/api/admin/overview'),
  adminLedger: () => get<{ entries: Record<string, any>[] }>('/api/admin/ledger'),
  adminUploads: () => get<{ uploads: Record<string, any>[] }>('/api/admin/uploads'),
  adminHealth: () => get<Record<string, any>>('/api/admin/health'),
  adminEvents: () => get<{ build: string; paid_ai: boolean; client: Record<string, any>[]; server: Record<string, any>[] }>('/api/admin/events'),
  adminProjectDiagnostics: (id: string) => get<Record<string, any>>(`/api/admin/projects/${id}/diagnostics`),
  adminSetPlan: (code: string) => post<Usage>('/api/admin/plan', { code }),
  adminAdjust: (minutes: number, note: string, key: string) =>
    post<{ entry: Record<string, any>; account: Usage }>('/api/admin/adjust', { minutes, note, key }),
  deleteProject: (id: string, deleteFiles = true) =>
    del<{ deleted: boolean; files_removed: number }>(
      `/api/projects/${id}?delete_files=${deleteFiles}`),
  projectThumbUrl: (id: string) => `${BASE}/api/projects/${id}/thumbnail`,

  // --- כתוביות ---
  subtitlePresets: (language: string) =>
    get<PresetsResponse>(`/api/subtitles/presets?language=${language}`),
  subtitleFonts: () => get<FontsResponse>('/api/subtitles/fonts'),
  subtitlePreview: (body: Record<string, unknown>, signal?: AbortSignal) =>
    request<SubtitlePreview>('/api/subtitles/preview',
      { method: 'POST', body: JSON.stringify(body), signal }),

  // --- משימות ---
  createJob: (body: {
    url?: string; upload_token?: string; title?: string
    live_mode?: boolean; settings_override?: Record<string, unknown>
  }) => post<Job>('/api/jobs', body),
  listJobs: (limit = 50) => get<Job[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => get<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
  retryJob: (id: string, fromStart = false) =>
    post<Job>(`/api/jobs/${id}/retry?from_start=${fromStart}`),
  deleteJob: (id: string, deleteFiles = true) =>
    del<{ deleted: boolean; files_removed: number }>(
      `/api/jobs/${id}?delete_files=${deleteFiles}`),
  jobTimeline: (id: string) => get<TimelineData>(`/api/jobs/${id}/timeline`),
  jobFrameUrl: (id: string, t: number, width = 960) =>
    `${BASE}/api/jobs/${id}/frame?t=${t.toFixed(2)}&width=${width}`,
  jobTranscript: (id: string) =>
    get<{ count: number; language: string; segments: { idx: number; start: number; end: number; text: string }[] }>(
      `/api/jobs/${id}/transcript`),

  // --- קליפים ---
  listClips: (jobId?: string) =>
    get<Clip[]>(`/api/clips${jobId ? `?job_id=${jobId}` : ''}`),
  getClip: (id: string) => get<Clip>(`/api/clips/${id}`),
  patchClip: (id: string, body: { title?: string; description?: string }) =>
    patch<Clip>(`/api/clips/${id}`, body),
  deleteClip: (id: string) => del<{ deleted: boolean }>(`/api/clips/${id}`),
  getCues: (id: string) => get<Cue[]>(`/api/clips/${id}/cues`),
  putCues: (id: string, cues: { id?: number; start: number; end: number; text: string }[]) =>
    put<Cue[]>(`/api/clips/${id}/cues`, cues),
  /**
   * The render runs on the server in the background; this follows the clip until it is
   * done, so leaving the page never cancels it and no request stays open for minutes.
   */
  reexport: async (id: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<Clip> => {
    const clip = await request<Clip>(`/api/clips/${id}/reexport?background=true`,
      { method: 'POST', body: JSON.stringify(body), signal })
    return api.followReexport(clip, signal)
  },
  /** Waits for a background re-export (also one started before the page was opened). */
  followReexport: async (clip: Clip, signal?: AbortSignal): Promise<Clip> => {
    const id = clip.id
    const deadline = Date.now() + 45 * 60_000
    while (reexportState(clip) === 'running') {
      if (Date.now() > deadline) {
        throw new PolixorApiError({ code: 'timeout', message: tt('timeout'), hint: tt('reexportStillRunning') }, 0)
      }
      await new Promise((r) => setTimeout(r, 2000))
      if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
      clip = await request<Clip>(`/api/clips/${id}`, { signal })
    }
    const st = (clip.render_params as Record<string, any> | undefined)?.reexport
    if (st?.state === 'failed') {
      throw new PolixorApiError({ code: 'render_failed',
        message: st.error === 'interrupted' ? tt('reexportInterrupted') : (st.error || tt('unknown')),
        hint: tt('reexportKeptPrevious') }, 0)
    }
    return clip
  },

  // --- AI Images ---
  imageProviders: () => get<ImageProvidersResponse>('/api/images/providers'),
  listImages: (jobId?: string) =>
    get<GeneratedImage[]>(`/api/images${jobId ? `?job_id=${jobId}` : ''}`),
  getImage: (id: string) => get<GeneratedImage>(`/api/images/${id}`),
  createImage: (body: { prompt: string; aspect: string; job_id?: string }) =>
    post<GeneratedImage>('/api/images', body, LONG),
  regenerateImage: (id: string) => post<GeneratedImage>(`/api/images/${id}/regenerate`),
  varyImage: (id: string) => post<GeneratedImage>(`/api/images/${id}/variation`),
  editImagePrompt: (id: string, prompt: string) =>
    patch<GeneratedImage>(`/api/images/${id}`, { prompt }),
  cancelImage: (id: string) => post<{ cancelled: boolean }>(`/api/images/${id}/cancel`),
  deleteImage: (id: string) => del<{ deleted: boolean }>(`/api/images/${id}`),
  imageFileUrl: (id: string) => `${BASE}/api/images/${id}/file`,
  imageThumbUrl: (id: string) => `${BASE}/api/images/${id}/thumbnail`,
  imageDownloadUrl: (id: string) => `${BASE}/api/images/${id}/download`,

  // --- שיבוץ תמונות בקליפ ---
  listPlacements: (clipId: string) =>
    get<ImagePlacement[]>(`/api/clips/${clipId}/images`),
  addPlacement: (clipId: string, body: Record<string, unknown>) =>
    post<ImagePlacement>(`/api/clips/${clipId}/images`, body),
  removePlacement: (clipId: string, placementId: string) =>
    del<{ deleted: boolean }>(`/api/clips/${clipId}/images/${placementId}`),
  suggestVisuals: (clipId: string, limit = 5) =>
    post<SuggestVisualsResponse>(`/api/clips/${clipId}/suggest-visuals?limit=${limit}`, undefined, LONG),

  // --- שידור חי ---
  detectLive: (url: string, probeMedia = true) =>
    post<LiveDetectResult>('/api/live/detect', { url, probe_media: probeMedia }, LONG),
  liveStatus: (jobId: string) => get<LiveStatus>(`/api/jobs/${jobId}/live`),
  stopLive: (jobId: string) => post<LiveStatus>(`/api/jobs/${jobId}/live/stop`),

  clipFileUrl: (id: string) => `${BASE}/api/clips/${id}/file`,
  clipThumbUrl: (id: string) => `${BASE}/api/clips/${id}/thumbnail`,
  clipDownloadUrl: (id: string) => `${BASE}/api/clips/${id}/download`,
  clipSrtUrl: (id: string) => `${BASE}/api/clips/${id}/subtitles.srt`,

  /** A native, streamed download: a multi-GB archive never passes through page memory. */
  downloadZip: (clipIds: string[], includeSubtitles = true) => {
    const a = document.createElement('a')
    a.href = `${BASE}/api/clips/download-zip?ids=${clipIds.map(encodeURIComponent).join(',')}&subtitles=${includeSubtitles}`
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  },

  getSettings: () => get<SettingsResponse>('/api/settings'),
  updateSettings: (patchBody: Record<string, unknown>) =>
    put<SettingsResponse>('/api/settings', patchBody),
  setSecret: (name: string, value: string) =>
    post<{ saved: boolean; masked: string; configured: boolean }>(
      '/api/settings/secrets', { name, value }),
  deleteSecret: (name: string) => del<{ deleted: boolean }>(`/api/settings/secrets/${name}`),
  testAi: () => post<Record<string, any>>('/api/settings/ai/test', undefined, LONG),
  saveCameraRegion: (region: { x: number; y: number; w: number; h: number }) =>
    post<{ saved: boolean }>('/api/settings/camera-region', region),

  editStyles: () => get<EditStylesResponse>('/api/edit/styles'),
  system: () => get<SystemInfo>('/api/system'),
  storage: () => get<Record<string, any>>('/api/system/storage'),
  cleanup: () => post<{ jobs_cleaned: number; freed_human: string }>('/api/system/cleanup', undefined, LONG),
  benchmarks: () => get<Record<string, any>>('/api/system/benchmarks'),
  // --- פרסום ---
  publishPlatforms: () =>
    get<{ platforms: PublishPlatform[]; scheduler: { running: boolean; enabled: boolean } }>('/api/publish/platforms'),
  publishAccounts: () => get<{ accounts: SocialAccount[] }>('/api/publish/accounts'),
  accountDetails: (id: string) =>
    get<{ details: Partial<TikTokCreatorDetails> }>(`/api/publish/accounts/${encodeURIComponent(id)}/details`),
  connectAccount: (platform: string, returnTo = '/publishing?tab=accounts') =>
    post<{ auth_url: string }>(`/api/publish/accounts/${encodeURIComponent(platform)}/connect`, { return_to: returnTo }),
  disconnectAccount: (id: string) =>
    post<{ disconnected: boolean }>(`/api/publish/accounts/${encodeURIComponent(id)}/disconnect`),
  publishConfig: () => get<{ groups: PublishConfigGroup[]; public_base_url: string; base_url: string }>('/api/publish/config'),
  savePublishConfig: (group: string, values: Record<string, string>) =>
    post<{ saved: boolean }>(`/api/publish/config/${encodeURIComponent(group)}`, { values }),
  publishPreflight: (body: { clip_id: string; targets: PublishTargetIn[]; mode: 'now' | 'schedule'; schedule_at?: string | null }) =>
    post<PreflightResult>('/api/publish/preflight', body, LONG),
  // --- סטודיו תמונות ---
  studioCaps: () => get<StudioCaps>('/api/image-studio/capabilities'),
  studioThreads: (jobId?: string) =>
    get<{ threads: StudioThreadSummary[] }>(`/api/image-studio/threads${jobId ? `?job_id=${jobId}` : ''}`),
  studioCreateThread: (jobId?: string) =>
    post<StudioThread>('/api/image-studio/threads', { job_id: jobId || '' }),
  studioThread: (id: string) => get<StudioThread>(`/api/image-studio/threads/${id}`),
  studioRename: (id: string, title: string) =>
    patch<StudioThreadSummary>(`/api/image-studio/threads/${id}`, { title }),
  studioDelete: (id: string) => del<{ deleted: boolean }>(`/api/image-studio/threads/${id}`),
  studioSend: (id: string, body: { text: string; attachments: string[]; aspect: string; mode: string; background: string }) =>
    post<{ user: StudioMessage; assistant: StudioMessage; images: Record<string, GeneratedImage> }>(
      `/api/image-studio/threads/${id}/messages`, body, { timeoutMs: 240_000 }),
  studioRetry: (messageId: string) =>
    post<{ message: StudioMessage; images: Record<string, GeneratedImage> }>(
      `/api/image-studio/messages/${messageId}/retry`, undefined, { timeoutMs: 240_000 }),
  studioUpload: (file: File, jobId?: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('job_id', jobId || '')
    return request<GeneratedImage>('/api/image-studio/uploads', { method: 'POST', body: form }, LONG)
  },
  studioThumbnail: (clipId: string, imageId: string) =>
    post<{ clip_id: string; thumbnail_url: string; placement_id: string }>(
      '/api/image-studio/thumbnail', { clip_id: clipId, image_id: imageId }),

  suggestMetadata: (clipId: string, platforms: string[], regenerate = false) =>
    post<{ platforms: Record<string, { title: string; text: string; hashtags: string[] }>;
           source: 'ai' | 'rules'; language: string; cached: boolean; note?: string }>(
      '/api/publish/metadata', { clip_id: clipId, platforms, regenerate }, LONG),
  publish: (body: { clip_id: string; targets: PublishTargetIn[]; mode: 'now' | 'schedule'; schedule_at?: string | null }) =>
    post<{ group_id: string; jobs: string[] }>('/api/publish/jobs', body),
  publishHistory: (clipId = '', limit = 100) =>
    get<PublishHistory>(`/api/publish/jobs?limit=${limit}${clipId ? `&clip_id=${encodeURIComponent(clipId)}` : ''}`),
  cancelPublish: (id: string) => post<{ cancelled: boolean }>(`/api/publish/jobs/${encodeURIComponent(id)}/cancel`),
  retryPublish: (id: string) => post<{ queued: boolean }>(`/api/publish/jobs/${encodeURIComponent(id)}/retry`),

  // --- התראות ---
  notifications: (limit = 60) => get<NotificationList>(`/api/notifications?limit=${limit}`),
  notificationSummary: () => get<{ unread: number; needs_attention: number }>('/api/notifications/summary'),
  markNotificationsRead: (ids?: number[]) =>
    post<{ updated: number }>('/api/notifications/read', { ids: ids ?? null }),
  deleteNotifications: (ids?: number[], readOnly = false) =>
    post<{ deleted: number }>(`/api/notifications/delete?read_only=${readOnly}`, { ids: ids ?? null }),
  health: () => get<{ ok: boolean; version: string; ffmpeg: boolean; access_protected?: boolean }>('/api/health'),
}
