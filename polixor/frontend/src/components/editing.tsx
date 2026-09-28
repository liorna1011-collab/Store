// רכיבי מנוע העריכה: בחירת סגנון, סיכום מה נעשה, ורצועת הביטים.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { EDIT_STYLE_TONE, formatDuration } from '../lib/format'
import type {
  CaptionAnimation, CaptionAnimationInfo, EditBeat, EditStats,
  EditStyleInfo, EditStyleName,
} from '../lib/types'
import { Chip, IconScissors, Spinner } from './ui'

// --------------------------------------------------------------------------
// טעינת הקטלוג פעם אחת ושיתופו בין המסכים
// --------------------------------------------------------------------------
let cached: { styles: EditStyleInfo[]; anims: CaptionAnimationInfo[] } | null = null
let inflight: Promise<void> | null = null

export function useEditStyles() {
  const [data, setData] = useState(cached)
  const [loading, setLoading] = useState(!cached)

  useEffect(() => {
    if (cached) { setData(cached); setLoading(false); return }
    if (!inflight) {
      inflight = api.editStyles()
        .then((r) => { cached = { styles: r.styles, anims: r.caption_animations } })
        .catch(() => { cached = { styles: [], anims: [] } })
        .finally(() => { inflight = null })
    }
    let alive = true
    void inflight.then(() => {
      if (alive) { setData(cached); setLoading(false) }
    })
    return () => { alive = false }
  }, [])

  return { styles: data?.styles ?? [], animations: data?.anims ?? [], loading }
}

// --------------------------------------------------------------------------
// בחירת סגנון עריכה
// --------------------------------------------------------------------------
export function EditStylePicker({ value, onChange, compact = false, disabled }: {
  value: EditStyleName
  onChange: (v: EditStyleName) => void
  compact?: boolean
  disabled?: boolean
}) {
  const { styles, loading } = useEditStyles()

  if (loading) {
    return <div className="flex items-center gap-2 text-xs text-ink-500">
      <Spinner className="w-3.5 h-3.5" />טוען סגנונות…
    </div>
  }
  if (!styles.length) {
    return <p className="hint">לא ניתן לטעון את סגנונות העריכה מהשרת.</p>
  }

  if (compact) {
    return (
      <div className="grid grid-cols-4 gap-1.5">
        {styles.map((s) => (
          <button key={s.name} disabled={disabled}
                  onClick={() => onChange(s.name)}
                  title={s.description}
                  className={`btn btn-sm justify-center ${value === s.name
                    ? 'bg-brand-600/20 text-brand-300 ring-1 ring-brand-500/40'
                    : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-white'}`}>
            {s.label}
          </button>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {styles.map((s) => (
        <label key={s.name}
               className={`flex items-start gap-3 rounded-lg border p-3 transition-colors
                 ${disabled ? 'opacity-50' : 'cursor-pointer'}
                 ${value === s.name
                   ? 'border-brand-500/50 bg-brand-600/10'
                   : 'border-ink-750 bg-ink-900 hover:border-ink-600'}`}>
          <input type="radio" className="mt-1 accent-brand-500" disabled={disabled}
                 checked={value === s.name} onChange={() => onChange(s.name)} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm text-white font-medium">{s.label}</span>
              <span className={`chip ${EDIT_STYLE_TONE[s.name] ?? ''}`}>{s.name}</span>
            </div>
            <p className="hint mt-1">{s.description}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {s.removes_silence && <Chip>מסיר אוויר מת</Chip>}
              {s.angle_changes && <Chip>שינויי זווית</Chip>}
              {s.color_punch > 0.4 && <Chip>צבע מוגבר</Chip>}
              {s.caption_animation !== 'none' && (
                <Chip>כתוביות {s.caption_animation === 'punch' ? 'חזקות' : 'קופצות'}</Chip>
              )}
            </div>
          </div>
        </label>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------
// בחירת אנימציית כתוביות
// --------------------------------------------------------------------------
export function CaptionAnimationPicker({ value, onChange, disabled }: {
  value: CaptionAnimation
  onChange: (v: CaptionAnimation) => void
  disabled?: boolean
}) {
  const { animations } = useEditStyles()
  const list = animations.length ? animations : [
    { name: 'none' as const, label: 'ללא', description: '' },
    { name: 'pop' as const, label: 'קפיצה', description: '' },
    { name: 'punch' as const, label: 'חזקה', description: '' },
  ]
  const current = list.find((a) => a.name === value)

  return (
    <div>
      <div className="grid grid-cols-3 gap-1.5">
        {list.map((a) => (
          <button key={a.name} disabled={disabled} onClick={() => onChange(a.name)}
                  className={`btn btn-sm ${value === a.name
                    ? 'bg-brand-600/20 text-brand-300 ring-1 ring-brand-500/40'
                    : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-white'}`}>
            {a.label}
          </button>
        ))}
      </div>
      {current?.description && <p className="hint mt-1.5">{current.description}</p>}
    </div>
  )
}

// --------------------------------------------------------------------------
// סיכום מה העורך עשה
// --------------------------------------------------------------------------
export function EditSummary({ params }: { params: Record<string, any> }) {
  const stats: EditStats | undefined = (params?.edit_stats ?? [])[0]
  const summary = String(params?.edit_summary ?? '')
  const styleName = String(params?.edit_style ?? '')
  const raw = Number(params?.raw_duration ?? 0)

  if (!summary && !stats) return null

  return (
    <div className="rounded-lg bg-ink-900 border border-ink-750 p-3">
      <div className="flex items-center gap-2 mb-2">
        <IconScissors className="w-3.5 h-3.5 text-ink-500" />
        <span className="text-xs font-medium text-ink-300">מה העורך עשה</span>
        {styleName && (
          <span className={`chip ${EDIT_STYLE_TONE[styleName] ?? ''}`}>
            {params.edit_style_label || styleName}
          </span>
        )}
      </div>

      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
          <Metric label="לפני" value={formatDuration(raw || stats.raw_duration)} />
          <Metric label="אחרי" value={formatDuration(stats.out_duration)}
                  tone={stats.out_duration < stats.raw_duration ? 'ok' : undefined} />
          <Metric label="חיתוכים" value={String(stats.cuts)} />
          <Metric label="שינויי זווית" value={String(stats.zoom_changes)} />
        </div>
      )}

      <p className="text-[11px] text-ink-400 leading-relaxed">{summary}</p>

      {stats && stats.dramatic_pauses_kept > 0 && (
        <p className="text-[11px] text-ok mt-1.5">
          {stats.dramatic_pauses_kept} שתיקות דרמטיות זוהו ונשמרו — הן חלק מהרגע,
          לא אוויר מת.
        </p>
      )}
    </div>
  )
}

function Metric({ label, value, tone }: {
  label: string; value: string; tone?: 'ok'
}) {
  return (
    <div className="rounded-md bg-ink-850 px-2 py-1.5">
      <div className="text-[10px] text-ink-600">{label}</div>
      <div className={`text-xs font-medium ltr-nums ${tone === 'ok' ? 'text-ok' : 'text-white'}`}>
        {value}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// רצועת הביטים – איפה נחתך ואיפה השתנתה הזווית
// --------------------------------------------------------------------------
export function BeatStrip({ beats, rawDuration, onSeek }: {
  beats: EditBeat[]
  rawDuration: number
  onSeek?: (outTime: number) => void
}) {
  const [hover, setHover] = useState<number | null>(null)
  if (!beats?.length || rawDuration <= 0) return null

  // מיקום כל ביט על ציר המקור, וזמן ההתחלה שלו בתוצר
  let acc = 0
  const items = beats.map((b) => {
    const outStart = acc
    acc += (b.end - b.start) / Math.max(0.05, b.speed || 1)
    return { ...b, outStart }
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] text-ink-500">
          רצועת העריכה — האזורים החסרים הם אוויר מת שהוסר
        </span>
        <span className="text-[11px] text-ink-600 ltr-nums">
          {beats.length} ביטים
        </span>
      </div>

      <div className="relative h-9 rounded-md bg-ink-900 border border-ink-750
                      overflow-hidden" dir="ltr">
        {items.map((b, i) => {
          const left = (b.start / rawDuration) * 100
          const width = Math.max(0.6, ((b.end - b.start) / rawDuration) * 100)
          const zoomed = Math.abs(b.zoom - 1) > 1e-3
          const sped = Math.abs((b.speed || 1) - 1) > 1e-3
          return (
            <button
              key={i}
              onClick={() => onSeek?.(b.outStart)}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              title={`${b.start.toFixed(1)}–${b.end.toFixed(1)} שנ' · ` +
                     (zoomed ? `זום ${b.zoom.toFixed(2)}× · ` : '') +
                     (sped ? `מהירות ${b.speed.toFixed(2)}× · ` : '') +
                     b.reason}
              className={`absolute top-0 bottom-0 border-l border-ink-950
                          transition-colors ${hover === i ? 'brightness-125' : ''}
                          ${sped ? 'bg-warn/45'
                            : zoomed ? 'bg-brand-500/55' : 'bg-brand-600/28'}`}
              style={{ left: `${left}%`, width: `${width}%` }}
            >
              {zoomed && width > 4 && (
                <span className="text-[9px] text-white/80 ltr-nums">
                  {b.zoom.toFixed(2)}×
                </span>
              )}
            </button>
          )
        })}
      </div>

      <div className="flex items-center gap-3 mt-2 text-[10px] text-ink-600">
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-brand-600/28" />קטע רגיל
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-brand-500/55" />שינוי זווית
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-warn/45" />מואץ
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-ink-900 border border-ink-700" />
          הוסר
        </span>
      </div>
    </div>
  )
}
