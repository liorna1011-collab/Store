// לקוח API מוקלד. כל בקשה שולחת את שפת הממשק (X-Polixor-Lang), ולכן
// השגיאות וההודעות מהשרת מגיעות בשפה של המשתמש.

import i18n, { currentLang } from '../i18n'
import type {
  ApiError, Clip, Cue, EditStylesResponse, FontsResponse, GeneratedImage, ImagePlacement,
  ImageProvidersResponse, Job, LiveDetectResult, LiveStatus, PresetsResponse, ProbeResult,
  Project, ProjectDefaults, ResolveResult, SettingsResponse, SubtitlePreview,
  SuggestVisualsResponse, SystemInfo, TimelineData, ClipReview, ProofreadResponse,
} from './types'

const BASE = ''

export function langHeaders(): Record<string, string> {
  return { 'X-Polixor-Lang': currentLang() }
}

const tt = (key: string) => i18n.t(`common.errors.${key}`)

export class PolixorApiError extends Error {
  code: string
  hint: string
  detail: string
  status: number

  constructor(err: ApiError, status: number) {
    super(err.message || tt('unknown'))
    this.name = 'PolixorApiError'
    this.code = err.code || 'unknown'
    this.hint = err.hint || ''
    this.detail = err.detail || ''
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...langHeaders(),
        ...(init?.headers || {}),
      },
    })
  } catch (e) {
    // ביטול מכוון (AbortController) אינו תקלת רשת
    if ((e as Error)?.name === 'AbortError') throw e
    throw new PolixorApiError(
      { code: 'network', message: tt('network'), hint: tt('networkHint') },
      0,
    )
  }

  if (!res.ok) {
    let payload: ApiError = {
      code: 'http_error', message: i18n.t('common.errors.http', { status: res.status }), hint: '' }
    try {
      const body = await res.json()
      payload = (body?.detail && typeof body.detail === 'object') ? body.detail
        : (body?.code ? body : payload)
    } catch { /* תשובה שאינה JSON */ }
    if (res.status === 401 && payload.code === 'auth_required') toLogin()
    throw new PolixorApiError(payload, res.status)
  }

  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/** סביבה מוגנת בסיסמה: כשהכניסה פגה, חוזרים לדף הכניסה ומשם לאותו מסך. */
function toLogin(): void {
  const next = window.location.pathname + window.location.search
  window.location.assign(`/login?next=${encodeURIComponent(next)}`)
}

const get = <T>(p: string) => request<T>(p)
const post = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const put = <T>(p: string, body: unknown) =>
  request<T>(p, { method: 'PUT', body: JSON.stringify(body) })
const patch = <T>(p: string, body: unknown) =>
  request<T>(p, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T>(p: string) => request<T>(p, { method: 'DELETE' })

export const api = {
  // --- מקורות ---
  resolve: (url: string) => post<ResolveResult>('/api/sources/resolve', { url }),
  probe: (url: string) => post<ProbeResult>('/api/sources/probe', { url }),

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
          if (xhr.status === 401 && payload.code === 'auth_required') toLogin()
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
    source: { type: 'upload' | 'url'; upload_token?: string; url?: string
      section?: { start: number; end: number } | null; live_capture_seconds?: number | null }
    title?: string; ui_language: string; content_language: string
    preview?: Record<string, unknown> | null
    vocabulary?: string
  }) => post<Project>('/api/projects', body),
  listProjects: (limit = 100) => get<{ items: Project[] }>(`/api/projects?limit=${limit}`),
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
  reexport: (id: string, body: Record<string, unknown>) =>
    post<Clip>(`/api/clips/${id}/reexport`, body),

  // --- AI Images ---
  imageProviders: () => get<ImageProvidersResponse>('/api/images/providers'),
  listImages: (jobId?: string) =>
    get<GeneratedImage[]>(`/api/images${jobId ? `?job_id=${jobId}` : ''}`),
  getImage: (id: string) => get<GeneratedImage>(`/api/images/${id}`),
  createImage: (body: { prompt: string; aspect: string; job_id?: string }) =>
    post<GeneratedImage>('/api/images', body),
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
    post<SuggestVisualsResponse>(`/api/clips/${clipId}/suggest-visuals?limit=${limit}`),

  // --- שידור חי ---
  detectLive: (url: string, probeMedia = true) =>
    post<LiveDetectResult>('/api/live/detect', { url, probe_media: probeMedia }),
  liveStatus: (jobId: string) => get<LiveStatus>(`/api/jobs/${jobId}/live`),
  stopLive: (jobId: string) => post<LiveStatus>(`/api/jobs/${jobId}/live/stop`),

  clipFileUrl: (id: string) => `${BASE}/api/clips/${id}/file`,
  clipThumbUrl: (id: string) => `${BASE}/api/clips/${id}/thumbnail`,
  clipDownloadUrl: (id: string) => `${BASE}/api/clips/${id}/download`,
  clipSrtUrl: (id: string) => `${BASE}/api/clips/${id}/subtitles.srt`,

  downloadZip: async (clipIds: string[], includeSubtitles = true) => {
    const res = await fetch(`${BASE}/api/clips/download-zip`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...langHeaders() },
      body: JSON.stringify({ clip_ids: clipIds, include_subtitles: includeSubtitles }),
    })
    if (!res.ok) {
      let payload: ApiError = { code: 'zip_failed', message: tt('zipFailed'), hint: '' }
      try {
        const b = await res.json()
        payload = b?.detail && typeof b.detail === 'object' ? b.detail : payload
      } catch { /* ignore */ }
      throw new PolixorApiError(payload, res.status)
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'polixor_clips.zip'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 30_000)
  },

  // --- הגדרות ומערכת ---
  getSettings: () => get<SettingsResponse>('/api/settings'),
  updateSettings: (patchBody: Record<string, unknown>) =>
    put<SettingsResponse>('/api/settings', patchBody),
  setSecret: (name: string, value: string) =>
    post<{ saved: boolean; masked: string; configured: boolean }>(
      '/api/settings/secrets', { name, value }),
  deleteSecret: (name: string) => del<{ deleted: boolean }>(`/api/settings/secrets/${name}`),
  testAi: () => post<Record<string, any>>('/api/settings/ai/test'),
  saveCameraRegion: (region: { x: number; y: number; w: number; h: number }) =>
    post<{ saved: boolean }>('/api/settings/camera-region', region),

  editStyles: () => get<EditStylesResponse>('/api/edit/styles'),
  system: () => get<SystemInfo>('/api/system'),
  storage: () => get<Record<string, any>>('/api/system/storage'),
  cleanup: () => post<{ jobs_cleaned: number; freed_human: string }>('/api/system/cleanup'),
  benchmarks: () => get<Record<string, any>>('/api/system/benchmarks'),
  health: () => get<{ ok: boolean; version: string; ffmpeg: boolean; access_protected?: boolean }>('/api/health'),
}
