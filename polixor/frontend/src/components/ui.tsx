// רכיבי ממשק בסיסיים משותפים.

import React, { useEffect, useRef } from 'react'
import { useStore } from '../lib/store'

// --------------------------------------------------------------------------
// אייקונים (SVG מוטבע – ללא ספריות חיצוניות)
// --------------------------------------------------------------------------
type IconProps = { className?: string }
const base = 'w-[18px] h-[18px]'

const svg = (path: React.ReactNode, extra?: React.SVGProps<SVGSVGElement>) =>
  function Icon({ className }: IconProps) {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
           strokeLinecap="round" strokeLinejoin="round"
           className={className || base} aria-hidden="true" {...extra}>
        {path}
      </svg>
    )
  }

export const IconHome = svg(<><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V20h14V9.5" /></>)
export const IconTasks = svg(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M8 9h8M8 13h8M8 17h5" /></>)
export const IconFilm = svg(<><rect x="2.5" y="4" width="19" height="16" rx="2" /><path d="M7 4v16M17 4v16M2.5 12h19" /></>)
export const IconSettings = svg(<><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></>)
export const IconPlay = svg(<path d="M7 4.5 19 12 7 19.5z" fill="currentColor" />)
export const IconDownload = svg(<><path d="M12 3v12" /><path d="m7.5 10.5 4.5 4.5 4.5-4.5" /><path d="M4 20h16" /></>)
export const IconTrash = svg(<><path d="M4 7h16" /><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /><path d="M6 7v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7" /><path d="M10 11v6M14 11v6" /></>)
export const IconEdit = svg(<><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z" /><path d="M14.5 6.5l3 3" /></>)
export const IconX = svg(<><path d="M6 6l12 12M18 6L6 18" /></>)
export const IconCheck = svg(<path d="m5 12.5 4.5 4.5L19 7.5" />)
export const IconAlert = svg(<><path d="M12 8v5" /><circle cx="12" cy="16.5" r=".9" fill="currentColor" stroke="none" /><path d="M10.3 3.9 2.6 17.4A1.9 1.9 0 0 0 4.3 20h15.4a1.9 1.9 0 0 0 1.7-2.6L13.7 3.9a1.9 1.9 0 0 0-3.4 0Z" /></>)
export const IconUpload = svg(<><path d="M12 20V8" /><path d="m7.5 12.5 4.5-4.5 4.5 4.5" /><path d="M4 4h16" /></>)
export const IconRefresh = svg(<><path d="M20 12a8 8 0 1 1-2.6-5.9" /><path d="M20 4v5h-5" /></>)
export const IconLink = svg(<><path d="M10.5 13.5a4 4 0 0 0 5.7 0l2.3-2.3a4 4 0 1 0-5.7-5.7l-1.2 1.2" /><path d="M13.5 10.5a4 4 0 0 0-5.7 0l-2.3 2.3a4 4 0 1 0 5.7 5.7l1.2-1.2" /></>)
export const IconLive = svg(<><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" /><path d="M7.5 7.5a6.4 6.4 0 0 0 0 9M16.5 16.5a6.4 6.4 0 0 0 0-9" /><path d="M4.7 4.7a10.3 10.3 0 0 0 0 14.6M19.3 19.3a10.3 10.3 0 0 0 0-14.6" /></>)
export const IconStop = svg(<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />)
export const IconChart = svg(<><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>)
export const IconPlus = svg(<><path d="M12 5v14M5 12h14" /></>)
export const IconImage = svg(<><rect x="3" y="4.5" width="18" height="15" rx="2" /><circle cx="8.5" cy="9.5" r="1.6" /><path d="m4 17 4.5-4.5 3 3L15 12l5 5" /></>)
export const IconSparkle = svg(<><path d="M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9z" /><path d="M18.5 4v3M20 5.5h-3" /></>)
export const IconScissors = svg(<><circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><path d="M8 7.5 20 18M8 16.5 20 6" /></>)

// --------------------------------------------------------------------------
// התראות
// --------------------------------------------------------------------------
export function ToastHost() {
  const { toasts, dismissToast } = useStore()
  if (!toasts.length) return null

  const tone: Record<string, string> = {
    info: 'border-ink-600 bg-ink-800',
    success: 'border-ok/40 bg-ok/10',
    error: 'border-bad/40 bg-bad/10',
    warn: 'border-warn/40 bg-warn/10',
  }
  const icon: Record<string, React.ReactNode> = {
    info: <IconAlert className="w-4 h-4 text-brand-300" />,
    success: <IconCheck className="w-4 h-4 text-ok" />,
    error: <IconAlert className="w-4 h-4 text-bad" />,
    warn: <IconAlert className="w-4 h-4 text-warn" />,
  }

  return (
    <div className="fixed bottom-5 left-5 z-50 flex flex-col gap-2 w-[min(28rem,calc(100vw-2.5rem))]">
      {toasts.map((t) => (
        <div key={t.id}
             className={`animate-fade-up flex items-start gap-3 rounded-lg border p-3 shadow-xl backdrop-blur ${tone[t.tone]}`}
             role="status">
          <div className="mt-0.5 shrink-0">{icon[t.tone]}</div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-white break-words">{t.title}</div>
            {t.body && <div className="mt-0.5 text-xs text-ink-400 break-words">{t.body}</div>}
          </div>
          <button onClick={() => dismissToast(t.id)}
                  className="text-ink-500 hover:text-white transition-colors shrink-0"
                  aria-label="סגור">
            <IconX className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------
// פס התקדמות
// --------------------------------------------------------------------------
export function ProgressBar({ value, tone = 'brand', height = 'h-2', striped = false }: {
  value: number
  tone?: 'brand' | 'ok' | 'bad' | 'warn'
  height?: string
  striped?: boolean
}) {
  const pct = Math.round(Math.min(1, Math.max(0, value || 0)) * 100)
  const colors: Record<string, string> = {
    brand: 'bg-brand-500', ok: 'bg-ok', bad: 'bg-bad', warn: 'bg-warn',
  }
  return (
    <div className={`w-full ${height} rounded-full bg-ink-750 overflow-hidden`}
         role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className={`${height} ${colors[tone]} rounded-full transition-[width] duration-300 ease-out
                       ${striped ? 'bg-[linear-gradient(110deg,transparent,rgba(255,255,255,.22),transparent)] bg-[length:200%_100%] animate-shimmer' : ''}`}
           style={{ width: `${pct}%` }} />
    </div>
  )
}

// --------------------------------------------------------------------------
// חלון מודאלי
// --------------------------------------------------------------------------
export function Modal({ open, onClose, title, children, wide = false }: {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  wide?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         onMouseDown={(e) => { if (e.target === ref.current) onClose() }} ref={ref}>
      <div className={`card w-full ${wide ? 'max-w-4xl' : 'max-w-lg'} max-h-[88vh] flex flex-col animate-fade-up`}
           role="dialog" aria-modal="true" aria-label={title}>
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-ink-750">
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          <button onClick={onClose} className="text-ink-500 hover:text-white transition-colors"
                  aria-label="סגור">
            <IconX />
          </button>
        </div>
        <div className="p-5 overflow-y-auto">{children}</div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// מצבים ריקים / טעינה
// --------------------------------------------------------------------------
export function EmptyState({ icon, title, body, action }: {
  icon?: React.ReactNode
  title: string
  body?: string
  action?: React.ReactNode
}) {
  return (
    <div className="card flex flex-col items-center justify-center text-center py-16 px-6">
      {icon && <div className="text-ink-600 mb-4">{icon}</div>}
      <h3 className="text-base font-semibold text-white">{title}</h3>
      {body && <p className="mt-2 text-sm text-ink-400 max-w-md leading-relaxed">{body}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function Spinner({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={`${className} animate-spin`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity=".2" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

export function Chip({ children, tone = 'default' }: {
  children: React.ReactNode
  tone?: 'default' | 'ok' | 'warn' | 'bad' | 'brand'
}) {
  const tones: Record<string, string> = {
    default: 'bg-ink-750 text-ink-300',
    ok: 'bg-ok/15 text-ok',
    warn: 'bg-warn/15 text-warn',
    bad: 'bg-bad/15 text-bad',
    brand: 'bg-brand-600/20 text-brand-300',
  }
  return <span className={`chip ${tones[tone]}`}>{children}</span>
}

// --------------------------------------------------------------------------
// אישור פעולה הרסנית
// --------------------------------------------------------------------------
export function ConfirmDialog({ open, title, body, confirmLabel = 'מחק', onConfirm, onCancel, busy }: {
  open: boolean
  title: string
  body: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
}) {
  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <p className="text-sm text-ink-300 leading-relaxed">{body}</p>
      <div className="mt-6 flex gap-2 justify-end">
        <button className="btn-ghost" onClick={onCancel} disabled={busy}>ביטול</button>
        <button className="btn-danger" onClick={onConfirm} disabled={busy}>
          {busy && <Spinner />}
          {confirmLabel}
        </button>
      </div>
    </Modal>
  )
}
