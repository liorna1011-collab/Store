// פעמון ההתראות בסרגל העליון. ההתראות מגיעות מהשרת כבר מתורגמות לשפת
// הממשק ומקובצות למצבים: דורש טיפול / חדש / קודם. עדכון חי דרך ה-WebSocket
// (אירוע "notification"). במובייל הלוח נפתח כגיליון ברוחב מלא.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Bell, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { formatRelative } from '../lib/i18nFormat'
import type { NotificationItem, NotificationLevel, NotificationList } from '../lib/types'
import { cx } from './ds'

const ICON: Record<NotificationLevel, typeof Info> = {
  info: Info, success: CheckCircle2, warning: AlertTriangle, error: XCircle,
}
const TONE: Record<NotificationLevel, string> = {
  info: 'text-brand-400', success: 'text-ok', warning: 'text-warn', error: 'text-bad',
}

export function NotificationBell() {
  const { t, i18n } = useTranslation()
  const { subscribe } = useStore()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [summary, setSummary] = useState({ unread: 0, needs_attention: 0 })
  const [data, setData] = useState<NotificationList | null>(null)
  const [failed, setFailed] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try {
      const d = await api.notifications()
      setData(d)
      setSummary({ unread: d.unread, needs_attention: d.needs_attention })
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [])

  // הטקסטים מתורגמים בשרת – טוענים מחדש כשהשפה משתנה
  useEffect(() => { void load() }, [load, i18n.language])
  useEffect(() => subscribe((e) => {
    if (e.type === 'notification' || e.type === 'hello') void load()
  }), [subscribe, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
    }
  }, [open])

  const openItem = async (n: NotificationItem) => {
    setOpen(false)
    if (!n.read) {
      try { await api.markNotificationsRead([n.id]) } catch { /* לא חוסם ניווט */ }
      void load()
    }
    if (n.link) navigate(n.link)
  }

  const markAll = async () => {
    try { await api.markNotificationsRead() } finally { void load() }
  }
  const clearRead = async () => {
    try { await api.deleteNotifications(undefined, true) } finally { void load() }
  }

  const unread = summary.unread
  const label = unread ? t('notifications.openWithCount', { count: unread }) : t('notifications.open')

  return (
    <div ref={wrap} className="relative">
      <button type="button" className="btn-quiet !p-2 relative" aria-label={label} title={label}
              aria-haspopup="dialog" aria-expanded={open} data-testid="notification-bell"
              onClick={() => { setOpen((v) => !v); if (!open) void load() }}>
        <Bell className="w-4 h-4" />
        {unread > 0 && (
          <span data-testid="notification-badge"
                className={cx('absolute -top-0.5 -end-0.5 min-w-[1.1rem] h-[1.1rem] px-1 rounded-full',
                              'text-[10px] font-semibold leading-[1.1rem] text-center text-white ltr-nums',
                              summary.needs_attention ? 'bg-bad' : 'bg-brand-600')}>
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div role="dialog" aria-label={t('notifications.title')} data-testid="notification-panel"
             className={cx('z-50 bg-ink-850 border border-ink-750 shadow-pop flex flex-col',
                           // מובייל: גיליון ברוחב מלא מתחת לסרגל; מסך רחב: חלונית צפה
                           'fixed inset-x-0 top-14 bottom-0 sm:bottom-auto',
                           'sm:absolute sm:inset-x-auto sm:end-0 sm:top-full sm:mt-2 sm:w-[24rem]',
                           'sm:max-h-[70vh] sm:rounded-xl')}>
          <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-ink-750">
            <h2 className="font-semibold text-ink-100">{t('notifications.title')}</h2>
            <div className="flex items-center gap-1">
              {unread > 0 && (
                <button type="button" className="btn-quiet !px-2 !py-1 text-xs" onClick={markAll}>
                  {t('notifications.markAllRead')}
                </button>
              )}
              {data?.groups.some((g) => g.state === 'earlier') && (
                <button type="button" className="btn-quiet !px-2 !py-1 text-xs" onClick={clearRead}>
                  {t('notifications.clearRead')}
                </button>
              )}
              <button type="button" className="btn-quiet !p-1.5 sm:hidden" onClick={() => setOpen(false)}
                      aria-label={t('notifications.close')}><X className="w-4 h-4" /></button>
            </div>
          </div>

          <div className="overflow-y-auto flex-1">
            {failed && <p className="p-4 text-sm text-bad">{t('notifications.loadFailed')}</p>}
            {!failed && data && data.items.length === 0 && (
              <p className="p-6 text-sm text-ink-400 text-center">{t('notifications.empty')}</p>
            )}
            {data?.groups.map((g) => (
              <section key={g.state} data-group={g.state}>
                <h3 className={cx('px-4 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-wide',
                                  g.state === 'needs_attention' ? 'text-bad' : 'text-ink-500')}>
                  {g.title}
                </h3>
                <ul>
                  {g.items.map((n) => {
                    const Icon = ICON[n.level] || Info
                    return (
                      <li key={n.id}>
                        <button type="button" onClick={() => void openItem(n)}
                                data-testid="notification-item"
                                className={cx('w-full text-start flex gap-3 px-4 py-3 hover:bg-ink-800',
                                              'focus:outline-none focus-visible:bg-ink-800',
                                              !n.read && 'bg-ink-800/40')}>
                          <Icon className={cx('w-4 h-4 mt-0.5 shrink-0', TONE[n.level])}
                                aria-label={t(`notifications.levels.${n.level}`)} />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-2">
                              <span className={cx('text-sm truncate', n.read ? 'text-ink-300' : 'text-ink-100 font-medium')}>
                                {n.title}
                              </span>
                              {n.count > 1 && (
                                <span className="text-[10px] rounded bg-ink-750 px-1.5 text-ink-300 ltr-nums">
                                  {t('notifications.times', { count: n.count })}
                                </span>
                              )}
                              {!n.read && <span className="ms-auto w-2 h-2 rounded-full bg-brand-500 shrink-0" aria-hidden />}
                            </span>
                            <span dir="auto" className="block text-xs text-ink-400 mt-0.5 break-words">{n.body}</span>
                            {n.hint && <span dir="auto" className="block text-xs text-ink-500 mt-0.5">{n.hint}</span>}
                            {n.updated_at && (
                              <span className="block text-[11px] text-ink-500 mt-1">{formatRelative(n.updated_at)}</span>
                            )}
                          </span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
