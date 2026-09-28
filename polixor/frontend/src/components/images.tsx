/**
 * רכיבים משותפים לאזור AI Images.
 *
 * כלל בסיסי בכל הרכיבים כאן: תמונה שלא נוצרה על-ידי מודל AI מסומנת
 * ככזו בגלוי (`is_ai === false`), ולעולם לא מוצגת כאילו היא תוצר AI.
 */

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import i18n from '../i18n'
import { api } from '../lib/api'
import type {
  GeneratedImage, ImageAspect, ImageProvidersResponse, ImageRole,
} from '../lib/types'
import { Chip, IconAlert, IconCheck, IconX, Spinner } from './ui'

export const ASPECTS: { value: ImageAspect; label: string; box: string }[] = [
  { value: '9:16', label: '9:16', box: 'w-4 h-7' },
  { value: '16:9', label: '16:9', box: 'w-7 h-4' },
  { value: '1:1', label: '1:1', box: 'w-5 h-5' },
]

const ROLE_KEYS: { value: ImageRole; needsDuration: boolean }[] = [
  { value: 'intro', needsDuration: true }, { value: 'outro', needsDuration: true },
  { value: 'insert', needsDuration: true }, { value: 'broll', needsDuration: true },
  { value: 'overlay', needsDuration: true }, { value: 'background', needsDuration: false },
  { value: 'thumbnail', needsDuration: false },
]

/** תפקידי שיבוץ. התווית וההסבר נקראים מהקטלוג בכל גישה (שפה עדכנית). */
export const ROLE_OPTIONS: { value: ImageRole; label: string; hint: string; needsDuration: boolean }[] =
  ROLE_KEYS.map((r) => ({
    ...r,
    get label() { return i18n.t(`images.roles.${r.value}.label`) },
    get hint() { return i18n.t(`images.roles.${r.value}.hint`) },
  }))

export const DURATION_PRESETS = [2, 3, 5]

export function AspectPicker({ value, onChange, disabled }: {
  value: ImageAspect
  onChange: (v: ImageAspect) => void
  disabled?: boolean
}) {
  return (
    <div className="flex gap-2">
      {ASPECTS.map((a) => (
        <button key={a.value} type="button" disabled={disabled} onClick={() => onChange(a.value)}
                aria-pressed={value === a.value}
                className={`flex flex-col items-center gap-1.5 rounded-lg border px-4 py-2.5
                            transition-colors disabled:opacity-40
                            ${value === a.value
                              ? 'border-brand-500 bg-brand-600/10 text-brand-600'
                              : 'border-ink-700 bg-ink-900 text-ink-400 hover:border-ink-600 hover:text-ink-200'}`}>
          <span className={`${a.box} rounded-sm border-2 ${value === a.value ? 'border-brand-500' : 'border-ink-600'}`} />
          <span className="text-[11px] font-medium ltr-nums">{a.label}</span>
        </button>
      ))}
    </div>
  )
}

/** באנר שמסביר מדוע יצירת תמונות אינה זמינה, במקום כפתור שנראה פעיל. */
export function ProviderBanner({ providers, onOpenSettings }: {
  providers: ImageProvidersResponse | null
  onOpenSettings?: () => void
}) {
  const { t } = useTranslation()
  if (!providers) return null
  const selected = providers.providers.find((p) => p.selected)

  if (providers.ready && selected?.is_ai) return null

  if (selected && !selected.is_ai) {
    return (
      <div className="flex items-start gap-2.5 rounded-lg bg-warn/10 border border-warn/25 p-3.5">
        <IconAlert className="w-4 h-4 text-warn shrink-0 mt-px" />
        <div className="text-xs text-warn leading-relaxed">
          <strong className="font-semibold">{t('images.banner.activeProvider', { provider: selected.label })}</strong>{' '}
          {t('images.banner.localCards')}
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-2.5 rounded-lg bg-bad/10 border border-bad/25 p-3.5">
      <IconAlert className="w-4 h-4 text-bad shrink-0 mt-px" />
      <div className="text-xs text-bad leading-relaxed flex-1">
        <strong className="font-semibold">{t('images.banner.unavailable')}</strong>{' '}
        <span className="bidi-isolate">{providers.reason}</span>
        <div className="mt-1.5 text-ink-400">{t('images.banner.keySafety')}</div>
        {onOpenSettings && (
          <button className="btn-ghost btn-sm mt-2.5" onClick={onOpenSettings}>
            {t('images.banner.openSettings')}
          </button>
        )}
      </div>
    </div>
  )
}

/** תג שמבדיל בין תמונת AI לכרטיס מקומי. מופיע על כל תצוגה של תמונה. */
export function OriginBadge({ image }: { image: GeneratedImage }) {
  const { t } = useTranslation()
  if (image.is_ai) return <Chip tone="brand">{image.model || 'AI'}</Chip>
  return (
    <span className="chip bg-warn/15 text-warn ring-1 ring-warn/30" title={image.note}>
      {t('images.notAi')}
    </span>
  )
}

export function ImageStatusOverlay({ image, onCancel }: {
  image: GeneratedImage
  onCancel?: () => void
}) {
  const { t } = useTranslation()
  if (image.status === 'ready') return null

  if (image.status === 'failed' || image.status === 'cancelled') {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-ink-950/85 p-4 text-center">
        <IconAlert className={`w-6 h-6 ${image.status === 'failed' ? 'text-bad' : 'text-ink-500'}`} />
        <p className={`text-xs leading-relaxed ${image.status === 'failed' ? 'text-bad' : 'text-ink-400'}`}>
          {image.error || (image.status === 'cancelled' ? t('images.status.cancelled') : t('images.status.failed'))}
        </p>
      </div>
    )
  }

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-ink-950/80">
      <GeneratingPulse />
      <p className="text-xs text-ink-300">{t(`images.status.${image.status}`, { defaultValue: t('images.status.working') })}</p>
      {onCancel && (
        <button className="btn-ghost btn-sm" onClick={onCancel}>
          <IconX className="w-3 h-3" />
          {t('common.cancel')}
        </button>
      )}
    </div>
  )
}

/** אנימציית "יוצר תמונה" – עדינה, באותה שפה של שאר המוצר. */
export function GeneratingPulse() {
  return (
    <div className="flex items-end gap-1 h-6" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span key={i} className="w-1.5 rounded-full bg-brand-500/80"
              style={{ animation: `img-bar 1.1s ease-in-out ${i * 0.13}s infinite`, height: '40%' }} />
      ))}
    </div>
  )
}

/** טיימר שמראה כמה זמן היצירה רצה — מדידה אמיתית, לא הערכה. */
export function ElapsedTimer({ since, active }: { since: number; active: boolean }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [active])
  if (!active) return null
  const secs = Math.max(0, (now - since) / 1000)
  return <span className="ltr-nums text-[11px] text-ink-500">{secs.toFixed(0)}s</span>
}

/**
 * מזהה סיום יצירה: בודק את השרת עד שהתמונה מוכנה או נכשלה.
 * ה-WebSocket מביא את האירוע מיד; זו רשת ביטחון אם הוא מנותק.
 */
export function usePollImage(image: GeneratedImage | null, onUpdate: (img: GeneratedImage) => void) {
  const ref = useRef(onUpdate)
  ref.current = onUpdate
  const pending = image && (image.status === 'queued' || image.status === 'generating')
  const id = image?.id

  useEffect(() => {
    if (!pending || !id) return
    let alive = true
    const t = setInterval(() => {
      api.getImage(id).then((img) => { if (alive) ref.current(img) }).catch(() => undefined)
    }, 2000)
    return () => { alive = false; clearInterval(t) }
  }, [pending, id])
}

export function ImageCheck({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-ok">
      <IconCheck className="w-3 h-3" />
      {label}
    </span>
  )
}

export function InlineSpinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-400">
      <Spinner className="w-3 h-3" />
      {label}
    </span>
  )
}
