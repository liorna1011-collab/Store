// עזרי תצוגה. התוויות מגיעות מקטלוג התרגום (legacy.*) בשפת הממשק.

import i18n from '../i18n'
import { formatRelative as fmtRelative } from './i18nFormat'

/** מפה שכל גישה אליה מחזירה את התרגום העדכני (כך שהחלפת שפה משפיעה מיד). */
function labels(ns: string): Record<string, string> {
  return new Proxy({} as Record<string, string>, {
    get: (_t, key) => (typeof key === 'string' ? i18n.t(`${ns}.${key}`, { defaultValue: key }) : undefined),
  })
}

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
  return fmtRelative(iso)
}

export function formatEta(seconds: number | null): string {
  if (seconds === null || seconds === undefined || seconds <= 0) return ''
  if (seconds < 90) return i18n.t('legacy.eta.minute')
  const m = Math.round(seconds / 60)
  if (m < 60) return i18n.t('legacy.eta.minutes', { count: m })
  const h = Math.floor(m / 60)
  const rem = m % 60
  return rem ? i18n.t('legacy.eta.hoursMinutes', { h, m: rem }) : i18n.t('legacy.eta.hours', { count: h })
}

export const STATUS_LABEL: Record<string, string> = labels('legacy.status')

export const STATUS_TONE: Record<string, string> = {
  queued: 'bg-ink-700 text-ink-300',
  running: 'bg-brand-600/15 text-brand-600 ring-1 ring-brand-500/40',
  paused: 'bg-warn/15 text-warn',
  completed: 'bg-ok/15 text-ok',
  failed: 'bg-bad/15 text-bad',
  cancelled: 'bg-ink-700 text-ink-400',
}

export const STAGE_LABEL: Record<string, string> = labels('legacy.stage')

export const STAGE_SEQUENCE = [
  'download', 'probe', 'audio', 'transcribe',
  'analyze', 'select', 'render_long', 'render_short',
] as const

export const KIND_LABEL: Record<string, string> = labels('legacy.kind')

export const CATEGORY_LABEL: Record<string, string> = labels('legacy.category')

export const PLATFORM_LABEL: Record<string, string> = labels('legacy.platform')

export function scoreTone(score: number): string {
  if (score >= 0.7) return 'text-ok'
  if (score >= 0.45) return 'text-warn'
  return 'text-ink-400'
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}


export const EDIT_STYLE_LABEL: Record<string, string> = labels('legacy.editStyle')

export const EDIT_STYLE_TONE: Record<string, string> = {
  raw: 'bg-ink-750 text-ink-400',
  clean: 'bg-ok/15 text-ok',
  dynamic: 'bg-brand-600/15 text-brand-600',
  hype: 'bg-warn/15 text-warn',
}
