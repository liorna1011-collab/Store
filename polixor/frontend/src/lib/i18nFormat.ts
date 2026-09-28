// עיצוב מספרים, זמנים ותאריכים לפי שפת הממשק.

import i18n, { currentLang } from '../i18n'

function locale(): string {
  return currentLang() === 'he' ? 'he-IL' : 'en-US'
}

/** 3:42 / 1:05:20 – זהה בשתי השפות (תמיד LTR). */
export function formatDuration(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(seconds) || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    : `${m}:${String(sec).padStart(2, '0')}`
}

/** „12 דקות" / „12 minutes" – משך מילולי. */
export function formatMinutes(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return i18n.t('common.units.minutes', { count: minutes })
}

export function formatNumber(n: number, digits = 0): string {
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: digits }).format(n)
}

export function formatPercent(fraction: number): string {
  return new Intl.NumberFormat(locale(), { style: 'percent', maximumFractionDigits: 0 })
    .format(Math.max(0, Math.min(1, fraction)))
}

export function formatBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${formatNumber(n / 1024 ** i, i ? 1 : 0)} ${units[i]}`
}

export function formatRelative(iso: string): string {
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime()
  const diff = (then - Date.now()) / 1000
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: 'auto' })
  const abs = Math.abs(diff)
  if (abs < 60) return rtf.format(Math.round(diff), 'second')
  if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour')
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day')
  return new Intl.DateTimeFormat(locale(), { dateStyle: 'medium' }).format(new Date(then))
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`)
  return new Intl.DateTimeFormat(locale(), { dateStyle: 'medium', timeStyle: 'short' }).format(d)
}

/**
 * עוטף ערך (זמן, מספר, יחס) בבידוד כיווניות (LRI…PDI) לפני שהוא משולב
 * במשפט מתורגם. בלי זה, בעברית „0:05" שליד מספר אחר מתערבב בסדר התצוגה.
 */
export function iso(value: string | number): string {
  return `⁦${value}⁩`
}
