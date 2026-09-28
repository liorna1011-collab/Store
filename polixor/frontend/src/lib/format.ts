// עזרי תצוגה בעברית.

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

export function formatTimecode(seconds: number): string {
  const s = Math.max(0, seconds || 0)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  const ms = Math.round((s - Math.floor(s)) * 10)
  const core = `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${ms}`
  return h > 0 ? `${h}:${core}` : core
}

export function formatBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export function formatRelative(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime()
  if (Number.isNaN(then)) return '—'
  const diff = (Date.now() - then) / 1000
  if (diff < 45) return 'לפני רגע'
  if (diff < 3600) return `לפני ${Math.round(diff / 60)} דק'`
  if (diff < 86400) return `לפני ${Math.round(diff / 3600)} שע'`
  if (diff < 86400 * 30) return `לפני ${Math.round(diff / 86400)} ימים`
  return new Date(then).toLocaleDateString('he-IL')
}

export function formatEta(seconds: number | null): string {
  if (seconds === null || seconds === undefined || seconds <= 0) return ''
  if (seconds < 90) return `כדקה`
  const m = Math.round(seconds / 60)
  if (m < 60) return `כ-${m} דקות`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return rem ? `כ-${h} שע' ו-${rem} דק'` : `כ-${h} שעות`
}

export const STATUS_LABEL: Record<string, string> = {
  queued: 'בתור',
  running: 'רץ',
  paused: 'מושהה',
  completed: 'הושלם',
  failed: 'נכשל',
  cancelled: 'בוטל',
}

export const STATUS_TONE: Record<string, string> = {
  queued: 'bg-ink-700 text-ink-300',
  running: 'bg-brand-600/20 text-brand-300 ring-1 ring-brand-500/40',
  paused: 'bg-warn/15 text-warn',
  completed: 'bg-ok/15 text-ok',
  failed: 'bg-bad/15 text-bad',
  cancelled: 'bg-ink-700 text-ink-400',
}

export const STAGE_LABEL: Record<string, string> = {
  pending: 'ממתין',
  download: 'הורדה',
  probe: 'בדיקת קובץ',
  audio: 'חילוץ אודיו',
  transcribe: 'תמלול',
  analyze: 'ניתוח',
  select: 'בחירת רגעים',
  render_long: 'קליפים ארוכים',
  render_short: 'שורטים',
  done: 'הושלם',
}

export const STAGE_SEQUENCE = [
  'download', 'probe', 'audio', 'transcribe',
  'analyze', 'select', 'render_long', 'render_short',
] as const

export const KIND_LABEL: Record<string, string> = {
  long: 'קליפ ארוך',
  short: 'שורט',
  highlights: 'מיטב הרגעים',
}

export const CATEGORY_LABEL: Record<string, string> = {
  funny: 'מצחיק',
  win: 'ניצחון',
  fail: 'כישלון',
  surprise: 'הפתעה',
  story: 'סיפור',
  argument: 'ויכוח',
  highlight: 'שיא',
  visual: 'ויזואלי',
  moment: 'רגע בולט',
}

export const PLATFORM_LABEL: Record<string, string> = {
  youtube_vod: 'YouTube — סרטון',
  youtube_live: 'YouTube — שידור חי',
  twitch_vod: 'Twitch — VOD',
  twitch_live: 'Twitch — שידור חי',
  kick_vod: 'Kick — VOD',
  kick_live: 'Kick — שידור חי',
  gdrive: 'Google Drive',
  upload: 'קובץ מהמחשב',
  direct_url: 'קישור ישיר',
  unknown: 'לא מזוהה',
}

export function scoreTone(score: number): string {
  if (score >= 0.7) return 'text-ok'
  if (score >= 0.45) return 'text-warn'
  return 'text-ink-400'
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}


export const EDIT_STYLE_LABEL: Record<string, string> = {
  raw: 'גולמי',
  clean: 'נקי',
  dynamic: 'דינמי',
  hype: 'אנרגטי',
}

export const EDIT_STYLE_TONE: Record<string, string> = {
  raw: 'bg-ink-750 text-ink-400',
  clean: 'bg-ok/15 text-ok',
  dynamic: 'bg-brand-600/20 text-brand-300',
  hype: 'bg-warn/15 text-warn',
}
