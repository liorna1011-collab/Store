// רכיבי מערכת העיצוב. כל הרווחים והמיקומים לוגיים (ms/me/ps/pe/start/end),
// כך שאותו רכיב נכון גם מימין לשמאל וגם משמאל לימין.

import React, { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AlertCircle, AlertTriangle, CheckCircle2, Info, Loader2, X,
} from 'lucide-react'

export function cx(...parts: (string | number | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

// --------------------------------------------------------------------------
// כפתורים
// --------------------------------------------------------------------------
type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: React.ReactNode
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading = false, icon, className, children,
    disabled, type = 'button', ...rest }, ref) {
  const base = {
    primary: 'btn-primary', secondary: 'btn-ghost', quiet: 'btn-quiet', danger: 'btn-danger',
  }[variant]
  return (
    <button ref={ref} type={type} disabled={disabled || loading}
            aria-busy={loading || undefined}
            className={cx(base, size === 'sm' && 'btn-sm', size === 'lg' && 'btn-lg', className)}
            {...rest}>
      {loading ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  )
})

export function IconButton({ label, icon, className, tipAlign, ...rest }: {
  label: string
  icon: React.ReactNode
  tipAlign?: 'center' | 'start'
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <Tooltip content={label} align={tipAlign}>
      <button type="button" aria-label={label}
              className={cx('btn-quiet !p-2 rounded-lg', className)} {...rest}>
        {icon}
      </button>
    </Tooltip>
  )
}

// --------------------------------------------------------------------------
// כרטיסים וכותרות
// --------------------------------------------------------------------------
export function Card({ className, children, as: As = 'div', ...rest }: {
  as?: 'div' | 'section' | 'article'
} & React.HTMLAttributes<HTMLElement>) {
  return <As className={cx('card', className)} {...rest}>{children}</As>
}

export function CardHeader({ title, subtitle, actions, icon }: {
  title: React.ReactNode
  subtitle?: React.ReactNode
  actions?: React.ReactNode
  icon?: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-3">
      <div className="flex items-start gap-3 min-w-0">
        {icon && <div className="mt-0.5 text-brand-600 shrink-0">{icon}</div>}
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold leading-snug">{title}</h2>
          {subtitle && <p className="mt-1 text-sm text-ink-500">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions, back }: {
  title: React.ReactNode
  subtitle?: React.ReactNode
  actions?: React.ReactNode
  back?: React.ReactNode
}) {
  return (
    <header className="mb-6">
      {back && <div className="mb-3">{back}</div>}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1.5 text-sm text-ink-500 max-w-2xl">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}

// --------------------------------------------------------------------------
// תגיות
// --------------------------------------------------------------------------
export type Tone = 'neutral' | 'brand' | 'ok' | 'warn' | 'bad'

const TONES: Record<Tone, string> = {
  neutral: 'bg-ink-800 text-ink-400 ring-ink-700',
  brand: 'bg-brand-600/10 text-brand-600 ring-brand-500/25',
  ok: 'bg-ok/10 text-ok ring-ok/25',
  warn: 'bg-warn/10 text-warn ring-warn/25',
  bad: 'bg-bad/10 text-bad ring-bad/25',
}

export function Badge({ tone = 'neutral', children, className, icon }: {
  tone?: Tone
  children: React.ReactNode
  className?: string
  icon?: React.ReactNode
}) {
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs',
                        'font-medium ring-1 ring-inset', TONES[tone], className)}>
      {icon}
      {children}
    </span>
  )
}

// --------------------------------------------------------------------------
// שדות
// --------------------------------------------------------------------------
export function Field({ label, hint, error, children, htmlFor, className, trailing }: {
  label?: React.ReactNode
  hint?: React.ReactNode
  error?: React.ReactNode
  children: React.ReactNode
  htmlFor?: string
  className?: string
  trailing?: React.ReactNode
}) {
  return (
    <div className={className}>
      {(label || trailing) && (
        <div className="flex items-center justify-between gap-2 mb-1.5">
          {label && <label htmlFor={htmlFor} className="text-xs font-medium text-ink-400">{label}</label>}
          {trailing}
        </div>
      )}
      {children}
      {error ? <p className="mt-1.5 text-xs text-bad" role="alert">{error}</p>
        : hint ? <p className="mt-1.5 hint">{hint}</p> : null}
    </div>
  )
}

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cx('field', className)} {...rest} />
  })

export function Select({ className, children, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx('field pe-8', className)} {...rest}>{children}</select>
}

export function Switch({ checked, onChange, label, description, disabled, id }: {
  checked: boolean
  onChange: (v: boolean) => void
  label: React.ReactNode
  description?: React.ReactNode
  disabled?: boolean
  id?: string
}) {
  const auto = useId()
  const sid = id || auto
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={sid} className="text-sm font-medium text-ink-200 cursor-pointer">{label}</label>
        {description && <p className="hint mt-0.5">{description}</p>}
      </div>
      <button id={sid} type="button" role="switch" aria-checked={checked} disabled={disabled}
              onClick={() => onChange(!checked)}
              className={cx('relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors',
                            'disabled:opacity-50',
                            checked ? 'bg-brand-600' : 'bg-ink-700')}>
        <span className={cx('absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all',
                            checked ? 'start-[22px]' : 'start-0.5')} />
      </button>
    </div>
  )
}

export function Slider({ label, value, min, max, step = 1, onChange, format, disabled, hint }: {
  label: React.ReactNode
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  format?: (v: number) => string
  disabled?: boolean
  hint?: React.ReactNode
}) {
  const id = useId()
  return (
    <Field label={label} htmlFor={id} hint={hint}
           trailing={<span className="text-xs font-medium text-ink-300 ltr-nums">
             {format ? format(value) : value}</span>}>
      <input id={id} type="range" className="range" min={min} max={max} step={step}
             value={value} disabled={disabled}
             onChange={(e) => onChange(Number(e.target.value))} />
    </Field>
  )
}

export interface SegmentedOption<T extends string | number> {
  value: T
  label: React.ReactNode
  hint?: string
  disabled?: boolean
}

export function Segmented<T extends string | number>({ value, options, onChange, label, size = 'md' }: {
  value: T
  options: SegmentedOption<T>[]
  onChange: (v: T) => void
  label?: string
  size?: 'sm' | 'md'
}) {
  return (
    <div role="radiogroup" aria-label={label}
         className="inline-flex flex-wrap gap-1 rounded-lg bg-ink-800 p-1 ring-1 ring-inset ring-ink-750">
      {options.map((o) => {
        const active = o.value === value
        return (
          <button key={String(o.value)} type="button" role="radio" aria-checked={active}
                  title={o.hint} disabled={o.disabled}
                  onClick={() => onChange(o.value)}
                  className={cx('rounded-md font-medium transition-colors disabled:opacity-40',
                                size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-sm',
                                active ? 'bg-ink-850 text-ink-100 shadow-card ring-1 ring-ink-700'
                                  : 'text-ink-400 hover:text-ink-100')}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

export function ColorInput({ label, value, onChange }: {
  label: React.ReactNode
  value: string
  onChange: (v: string) => void
}) {
  const id = useId()
  return (
    <Field label={label} htmlFor={id}>
      <div className="flex items-center gap-2">
        <input id={id} type="color" value={value} onChange={(e) => onChange(e.target.value.toUpperCase())}
               className="h-9 w-11 shrink-0 cursor-pointer rounded-lg border border-ink-700 bg-ink-850 p-1" />
        <Input value={value} dir="ltr" maxLength={7} className="font-mono uppercase"
               aria-label={typeof label === 'string' ? label : undefined}
               onChange={(e) => {
                 const v = e.target.value.trim()
                 if (/^#[0-9a-fA-F]{6}$/.test(v)) onChange(v.toUpperCase())
               }} />
      </div>
    </Field>
  )
}

// --------------------------------------------------------------------------
// התקדמות וטעינה
// --------------------------------------------------------------------------
export function ProgressBar({ value, indeterminate, tone = 'brand', label }: {
  value?: number
  indeterminate?: boolean
  tone?: 'brand' | 'ok' | 'bad'
  label?: string
}) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100
  const color = { brand: 'bg-brand-600', ok: 'bg-ok', bad: 'bg-bad' }[tone]
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-ink-800" role="progressbar"
         aria-label={label} aria-valuemin={0} aria-valuemax={100}
         aria-valuenow={indeterminate ? undefined : Math.round(pct)}>
      {indeterminate
        ? <div className={cx('h-full w-2/5 rounded-full animate-indeterminate', color)} />
        : <div className={cx('h-full rounded-full transition-[width] duration-500', color)}
               style={{ width: `${pct}%` }} />}
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx('w-5 h-5 animate-spin text-brand-600', className)} aria-hidden />
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('skeleton', className)} aria-hidden />
}

// --------------------------------------------------------------------------
// מצבים ריקים, שגיאות והודעות
// --------------------------------------------------------------------------
export function EmptyState({ icon, title, body, action }: {
  icon?: React.ReactNode
  title: React.ReactNode
  body?: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center text-center px-6 py-12">
      {icon && <div className="mb-4 rounded-2xl bg-brand-600/10 p-4 text-brand-600">{icon}</div>}
      <h3 className="text-base font-semibold">{title}</h3>
      {body && <p className="mt-2 text-sm text-ink-500 max-w-md">{body}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function Callout({ tone = 'neutral', title, children, action }: {
  tone?: 'neutral' | 'ok' | 'warn' | 'bad' | 'brand'
  title?: React.ReactNode
  children?: React.ReactNode
  action?: React.ReactNode
}) {
  const Icon = tone === 'ok' ? CheckCircle2 : tone === 'bad' ? AlertCircle
    : tone === 'warn' ? AlertTriangle : Info
  const ring = {
    neutral: 'bg-ink-800/60 ring-ink-750 text-ink-400',
    brand: 'bg-brand-600/5 ring-brand-500/25 text-brand-600',
    ok: 'bg-ok/5 ring-ok/25 text-ok',
    warn: 'bg-warn/5 ring-warn/30 text-warn',
    bad: 'bg-bad/5 ring-bad/30 text-bad',
  }[tone]
  return (
    <div className={cx('flex items-start gap-3 rounded-xl p-3.5 ring-1 ring-inset', ring)}
         role={tone === 'bad' ? 'alert' : undefined}>
      <Icon className="w-5 h-5 shrink-0 mt-px" aria-hidden />
      <div className="min-w-0 flex-1 text-sm">
        {title && <div className="font-semibold text-ink-100">{title}</div>}
        {children && <div className={cx('text-ink-400 leading-relaxed', title && 'mt-0.5')}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}

export function ErrorState({ title, message, hint, onRetry }: {
  title?: React.ReactNode
  message?: React.ReactNode
  hint?: React.ReactNode
  onRetry?: () => void
}) {
  const { t } = useTranslation()
  return (
    <Callout tone="bad" title={title || t('common.somethingWentWrong')}
             action={onRetry && <Button size="sm" onClick={onRetry}>{t('common.retry')}</Button>}>
      {message}
      {hint && <div className="mt-1 text-ink-500">{hint}</div>}
    </Callout>
  )
}

// --------------------------------------------------------------------------
// Tooltip
// --------------------------------------------------------------------------
export function Tooltip({ content, children, align = 'center' }: {
  content: React.ReactNode
  children: React.ReactElement
  /** start: מיושר לתחילת הכפתור – לכפתורים בקצה של אזור גלילה, שלא ייחתך */
  align?: 'center' | 'start'
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const child = React.cloneElement(children, {
    'aria-describedby': open ? id : undefined,
    onMouseEnter: () => setOpen(true),
    onMouseLeave: () => setOpen(false),
    onFocus: () => setOpen(true),
    onBlur: () => setOpen(false),
  })
  return (
    <span className="relative inline-flex">
      {child}
      {open && (
        <span id={id} role="tooltip"
              className={cx('pointer-events-none absolute bottom-full z-50 mb-2 w-max max-w-[16rem]',
                            'rounded-md bg-ink-100 px-2 py-1 text-xs font-medium text-ink-950 shadow-pop',
                            align === 'start' ? 'start-0' : 'start-1/2 -translate-x-1/2 rtl:translate-x-1/2')}>
          {content}
        </span>
      )}
    </span>
  )
}

export function HelpTip({ text }: { text: string }) {
  return (
    <Tooltip content={text}>
      <button type="button" aria-label={text} className="text-ink-500 hover:text-ink-300">
        <Info className="w-3.5 h-3.5" />
      </button>
    </Tooltip>
  )
}

// --------------------------------------------------------------------------
// Modal
// --------------------------------------------------------------------------
export function Modal({ open, onClose, title, children, footer, size = 'md' }: {
  open: boolean
  onClose: () => void
  title: React.ReactNode
  children: React.ReactNode
  footer?: React.ReactNode
  size?: 'sm' | 'md' | 'lg'
}) {
  const { t } = useTranslation()
  const panel = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'Tab' && panel.current) {
        const f = panel.current.querySelectorAll<HTMLElement>(
          'button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')
        if (!f.length) return
        const first = f[0], last = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    setTimeout(() => panel.current?.querySelector<HTMLElement>('input,button,select')?.focus(), 0)
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [open, onClose])
  if (!open) return null
  const width = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-3xl' }[size]
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[1px]" onClick={onClose} aria-hidden />
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId}
           className={cx('relative w-full card shadow-pop animate-fade-up max-h-[92vh] flex flex-col',
                         'rounded-b-none sm:rounded-xl', width)}>
        <div className="flex items-center justify-between gap-3 border-b border-ink-750 px-5 py-4">
          <h2 id={titleId} className="text-base font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label={t('common.close')}
                  className="btn-quiet !p-1.5"><X className="w-4 h-4" /></button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-ink-750 px-5 py-3">{footer}</div>}
      </div>
    </div>
  )
}

export function ConfirmModal({ open, onClose, onConfirm, title, body, confirmLabel, danger, busy }: {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: React.ReactNode
  body?: React.ReactNode
  confirmLabel: string
  danger?: boolean
  busy?: boolean
}) {
  const { t } = useTranslation()
  return (
    <Modal open={open} onClose={onClose} title={title} size="sm"
           footer={<>
             <Button onClick={onClose}>{t('common.cancel')}</Button>
             <Button variant={danger ? 'danger' : 'primary'} loading={busy} onClick={onConfirm}>
               {confirmLabel}
             </Button>
           </>}>
      {body && <div className="text-sm text-ink-400 leading-relaxed">{body}</div>}
    </Modal>
  )
}

// --------------------------------------------------------------------------
// Stepper
// --------------------------------------------------------------------------
export interface Step {
  key: string
  label: string
  state: 'done' | 'current' | 'upcoming' | 'error'
}

export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex items-center gap-1 overflow-x-auto pb-1">
      {steps.map((s, i) => (
        <li key={s.key} className="flex items-center gap-1 shrink-0">
          <div className={cx('flex items-center gap-2 rounded-full px-2.5 py-1 text-xs font-medium',
                             s.state === 'current' && 'bg-brand-600/10 text-brand-600 ring-1 ring-brand-500/30',
                             s.state === 'done' && 'text-ink-300',
                             s.state === 'upcoming' && 'text-ink-500',
                             s.state === 'error' && 'bg-bad/10 text-bad')}
               aria-current={s.state === 'current' ? 'step' : undefined}>
            <span className={cx('flex h-5 w-5 items-center justify-center rounded-full text-[11px] ltr-nums',
                                s.state === 'done' ? 'bg-ok text-white'
                                  : s.state === 'current' ? 'bg-brand-600 text-on'
                                    : s.state === 'error' ? 'bg-bad text-white' : 'bg-ink-800 text-ink-500')}>
              {s.state === 'done' ? '✓' : i + 1}
            </span>
            {s.label}
          </div>
          {i < steps.length - 1 && <span className="h-px w-4 bg-ink-700" aria-hidden />}
        </li>
      ))}
    </ol>
  )
}

// --------------------------------------------------------------------------
// Toasts
// --------------------------------------------------------------------------
export interface ToastItem {
  id: number
  tone: 'info' | 'success' | 'error' | 'warn'
  title: string
  body?: string
}

export function ToastRegion({ toasts, onDismiss }: {
  toasts: ToastItem[]
  onDismiss: (id: number) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="fixed bottom-4 end-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
         aria-live="polite" aria-relevant="additions">
      {toasts.map((x) => {
        const Icon = x.tone === 'success' ? CheckCircle2 : x.tone === 'error' ? AlertCircle
          : x.tone === 'warn' ? AlertTriangle : Info
        const color = { success: 'text-ok', error: 'text-bad', warn: 'text-warn', info: 'text-brand-600' }[x.tone]
        return (
          <div key={x.id} role={x.tone === 'error' ? 'alert' : 'status'}
               className="card shadow-pop flex items-start gap-3 p-3.5 animate-fade-up">
            <Icon className={cx('w-5 h-5 shrink-0 mt-px', color)} aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-ink-100">{x.title}</div>
              {x.body && <div className="mt-0.5 text-xs text-ink-500 leading-relaxed">{x.body}</div>}
            </div>
            <button type="button" onClick={() => onDismiss(x.id)} aria-label={t('common.close')}
                    className="text-ink-500 hover:text-ink-100"><X className="w-4 h-4" /></button>
          </div>
        )
      })}
    </div>
  )
}

// --------------------------------------------------------------------------
// שורות מידע
// --------------------------------------------------------------------------
export function Stat({ label, value, hint, tone }: {
  label: React.ReactNode
  value: React.ReactNode
  hint?: React.ReactNode
  tone?: Tone
}) {
  const color = tone === 'ok' ? 'text-ok' : tone === 'warn' ? 'text-warn'
    : tone === 'bad' ? 'text-bad' : 'text-ink-100'
  return (
    <div className="rounded-xl bg-ink-800/50 ring-1 ring-inset ring-ink-750 p-3.5">
      <div className="text-xs text-ink-500">{label}</div>
      <div className={cx('mt-1 text-lg font-semibold', color)}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-500 leading-snug">{hint}</div>}
    </div>
  )
}
