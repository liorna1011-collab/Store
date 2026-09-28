/**
 * פאנל התמונות בעורך הקליפ: שיבוץ תמונות והצעות מהתמלול.
 *
 * כלל: שום תמונה אינה נוצרת ואינה משובצת בלי לחיצה מפורשת. הצעות
 * מהתמלול מוצגות כהצעות בלבד — יצירה ושיבוץ הם שתי פעולות נפרדות
 * של המשתמש.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { formatTimecode } from '../lib/format'
import type {
  Clip, GeneratedImage, ImagePlacement, ImageRole, VisualSuggestion,
} from '../lib/types'
import {
  Chip, IconAlert, IconCheck, IconImage, IconPlus, IconRefresh, IconSparkle,
  IconTrash, IconX, Modal, Spinner,
} from './ui'
import { DURATION_PRESETS, OriginBadge, ROLE_OPTIONS } from './images'

export function ClipImagePanel({ clip, onChanged }: {
  clip: Clip
  onChanged?: () => void
}) {
  const { t } = useTranslation()
  const { notifyError, pushToast, subscribe } = useStore()
  const [placements, setPlacements] = useState<ImagePlacement[]>([])
  const [library, setLibrary] = useState<GeneratedImage[]>([])
  const [loading, setLoading] = useState(true)
  const [picker, setPicker] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [p, imgs] = await Promise.all([api.listPlacements(clip.id), api.listImages(clip.job_id)])
      setPlacements(p)
      setLibrary(imgs.filter((i) => i.status === 'ready'))
    } catch (e) {
      notifyError(e, t('images.clip.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [clip.id, clip.job_id, notifyError, t])

  useEffect(() => { void load() }, [load])
  useEffect(() => subscribe((ev) => { if (ev.type === 'image.ready') void load() }), [subscribe, load])

  const remove = useCallback(async (id: string) => {
    setBusy(true)
    try {
      await api.removePlacement(clip.id, id)
      setPlacements((prev) => prev.filter((p) => p.id !== id))
      onChanged?.()
    } catch (e) {
      notifyError(e, t('images.clip.removeFailed'))
    } finally {
      setBusy(false)
    }
  }, [clip.id, notifyError, onChanged, t])

  const added = useCallback((p: ImagePlacement) => {
    setPlacements((prev) => [...prev, p].sort((a, b) => a.at_time - b.at_time))
    setPicker(false)
    onChanged?.()
    pushToast({ tone: 'success', title: t('images.clip.placed'), body: t('images.clip.placedBody') })
  }, [onChanged, pushToast, t])

  const extra = useMemo(
    () => placements.filter((p) => p.extends_timeline).reduce((sum, p) => sum + p.duration, 0),
    [placements])

  return (
    <div className="card-pad">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="section-title mb-0">{t('images.clip.title')}</h3>
        <Link to={`/images?job=${clip.job_id}`} className="text-xs text-brand-600 hover:underline shrink-0">
          {t('images.clip.createNew')}
        </Link>
      </div>
      <p className="hint mb-4">{t('images.clip.hint')}</p>

      {loading ? (
        <div className="space-y-2">
          <div className="skeleton h-14 rounded-lg" />
          <div className="skeleton h-14 rounded-lg" />
        </div>
      ) : placements.length === 0 ? (
        <div className="rounded-lg border border-dashed border-ink-700 p-5 text-center">
          <IconImage className="w-6 h-6 mx-auto text-ink-500" />
          <p className="text-xs text-ink-400 mt-2">{t('images.clip.none')}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {placements.map((p) => (
            <PlacementRow key={p.id} placement={p} busy={busy} onRemove={() => void remove(p.id)} />
          ))}
        </div>
      )}

      {extra > 0 && <p className="hint mt-3">{t('images.clip.grows', { seconds: extra.toFixed(1) })}</p>}

      <button className="btn-ghost w-full mt-4" onClick={() => setPicker(true)}>
        <IconPlus className="w-3.5 h-3.5" />
        {t('images.clip.place')}
      </button>

      <AddPlacementModal open={picker} onClose={() => setPicker(false)} clip={clip}
                         library={library} onAdded={added} />
    </div>
  )
}

function PlacementRow({ placement, onRemove, busy }: {
  placement: ImagePlacement
  onRemove: () => void
  busy: boolean
}) {
  const { t } = useTranslation()
  const img = placement.image
  return (
    <div className="flex items-center gap-3 rounded-lg bg-ink-900 border border-ink-750 p-2.5 animate-fade-up">
      {img?.has_file ? (
        <img src={api.imageThumbUrl(img.id)} alt="" className="w-12 h-12 rounded-md object-cover bg-ink-800 shrink-0" />
      ) : (
        <div className="w-12 h-12 rounded-md bg-ink-800 shrink-0 flex items-center justify-center">
          <IconAlert className="w-4 h-4 text-ink-500" />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Chip tone="brand">{t(`images.roles.${placement.role}.label`, { defaultValue: placement.role_label })}</Chip>
          {placement.extends_timeline && (
            <span className="chip bg-ink-800 text-ink-400 ltr-nums">+{placement.duration.toFixed(1)}s</span>
          )}
          {!placement.extends_timeline && placement.role !== 'background' && (
            <span className="chip bg-ink-800 text-ink-400 ltr-nums">
              {formatTimecode(placement.at_time)} · {placement.duration.toFixed(1)}s
            </span>
          )}
          {img && !img.is_ai && <OriginBadge image={img} />}
        </div>
        <p className="text-[11px] text-ink-500 truncate mt-1" dir="auto" title={img?.prompt}>
          {img?.prompt || t('images.clip.missing')}
        </p>
      </div>
      <button className="btn-sm rounded-md border border-bad/30 text-bad hover:bg-bad/15 px-2 py-1.5 shrink-0"
              title={t('images.clip.remove')} aria-label={t('images.clip.remove')}
              onClick={onRemove} disabled={busy}>
        <IconTrash className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

function AddPlacementModal({ open, onClose, clip, library, onAdded }: {
  open: boolean
  onClose: () => void
  clip: Clip
  library: GeneratedImage[]
  onAdded: (p: ImagePlacement) => void
}) {
  const { t } = useTranslation()
  const { notifyError } = useStore()
  const [imageId, setImageId] = useState('')
  const [role, setRole] = useState<ImageRole>('insert')
  const [duration, setDuration] = useState(3)
  const [atTime, setAtTime] = useState(0)
  const [saving, setSaving] = useState(false)

  const roleInfo = ROLE_OPTIONS.find((r) => r.value === role)
  const needsTime = role === 'insert' || role === 'broll' || role === 'overlay'

  const submit = useCallback(async () => {
    if (!imageId) return
    setSaving(true)
    try {
      onAdded(await api.addPlacement(clip.id, {
        image_id: imageId, role,
        at_time: needsTime ? atTime : 0,
        duration: roleInfo?.needsDuration ? duration : 0,
        scale: role === 'overlay' ? 0.32 : 1.0,
        position: role === 'overlay' ? 'top_right' : 'center',
      }))
    } catch (e) {
      notifyError(e, t('images.clip.placeFailed'))
    } finally {
      setSaving(false)
    }
  }, [imageId, role, atTime, duration, needsTime, roleInfo, clip.id, onAdded, notifyError, t])

  return (
    <Modal open={open} onClose={onClose} title={t('images.clip.modalTitle')} wide>
      {library.length === 0 ? (
        <div className="text-center py-6">
          <IconImage className="w-7 h-7 mx-auto text-ink-500" />
          <p className="text-sm text-ink-300 mt-3">{t('images.clip.noneReady')}</p>
          <Link to={`/images?job=${clip.job_id}`} className="btn-primary btn-sm mt-4">{t('images.clip.goToImages')}</Link>
        </div>
      ) : (
        <div className="space-y-5">
          <div>
            <div className="label">{t('images.clip.chooseImage')}</div>
            <div className="grid grid-cols-4 sm:grid-cols-6 gap-2 max-h-48 overflow-y-auto p-0.5">
              {library.map((img) => (
                <button key={img.id} type="button" onClick={() => setImageId(img.id)} title={img.prompt}
                        aria-pressed={imageId === img.id}
                        className={`relative aspect-square rounded-lg overflow-hidden border-2 transition-colors
                                    ${imageId === img.id ? 'border-brand-500' : 'border-transparent hover:border-ink-600'}`}>
                  <img src={api.imageThumbUrl(img.id)} alt={img.prompt} className="w-full h-full object-cover" />
                  {imageId === img.id && (
                    <span className="absolute inset-0 bg-brand-600/25 flex items-center justify-center">
                      <IconCheck className="w-5 h-5 text-white" />
                    </span>
                  )}
                  {!img.is_ai && (
                    <span className="absolute bottom-0 inset-x-0 bg-warn text-white text-[9px] font-semibold text-center py-px">
                      {t('images.notAi')}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="label">{t('images.clip.howToPlace')}</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {ROLE_OPTIONS.filter((r) => r.value !== 'thumbnail').map((r) => (
                <button key={r.value} type="button" onClick={() => setRole(r.value)} aria-pressed={role === r.value}
                        className={`rounded-lg border px-3 py-2 text-start transition-colors
                                    ${role === r.value
                                      ? 'border-brand-500 bg-brand-600/10 text-brand-600'
                                      : 'border-ink-700 bg-ink-900 text-ink-400 hover:border-ink-600'}`}>
                  <div className="text-xs font-medium">{r.label}</div>
                </button>
              ))}
            </div>
            {roleInfo && <p className="hint mt-2">{roleInfo.hint}</p>}
          </div>

          {roleInfo?.needsDuration && (
            <div>
              <div className="label">{t('images.clip.duration')}</div>
              <div className="flex flex-wrap gap-2">
                {DURATION_PRESETS.map((d) => (
                  <button key={d} type="button" onClick={() => setDuration(d)} aria-pressed={duration === d}
                          className={`btn btn-sm ltr-nums ${duration === d
                            ? 'bg-brand-600/10 text-brand-600 ring-1 ring-brand-500/40'
                            : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-ink-100'}`}>
                    {d.toFixed(1)}s
                  </button>
                ))}
                <label className="flex items-center gap-2">
                  <span className="text-xs text-ink-400">{t('images.clip.other')}</span>
                  <input type="number" min={0.3} max={15} step={0.5} className="field w-20 py-1 text-xs ltr-nums"
                         value={duration} onChange={(e) => setDuration(Number(e.target.value) || 3)} />
                </label>
              </div>
            </div>
          )}

          {needsTime && (
            <div>
              <div className="label">
                {t('images.clip.entryPoint')}{' '}
                <span className="ltr-nums text-ink-200">{formatTimecode(atTime)}</span>
              </div>
              <input type="range" className="range" min={0} max={Math.max(1, clip.duration)} step={0.5}
                     value={atTime} aria-label={t('images.clip.entryPoint')}
                     onChange={(e) => setAtTime(Number(e.target.value))} />
              <p className="hint mt-1">{t('images.clip.entryHint')}</p>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button className="btn-ghost" onClick={onClose}>{t('common.cancel')}</button>
            <button className="btn-primary" disabled={!imageId || saving} onClick={() => void submit()}>
              {saving && <Spinner />}
              {t('images.clip.placeShort')}
            </button>
          </div>
        </div>
      )}
    </Modal>
  )
}

/**
 * Suggest Visuals – הצעות מהתמלול.
 *
 * שתי פעולות נפרדות ומפורשות: "צור" מייצר את התמונה, ו"שבץ" מכניס
 * אותה לקליפ. אף שלב אינו קורה אוטומטית.
 */
export function SuggestVisualsPanel({ clip, onPlaced }: {
  clip: Clip
  onPlaced?: () => void
}) {
  const { t } = useTranslation()
  const { notifyError, pushToast, subscribe } = useStore()
  const [items, setItems] = useState<VisualSuggestion[] | null>(null)
  const [note, setNote] = useState('')
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(false)
  const [generated, setGenerated] = useState<Record<number, GeneratedImage>>({})
  const [working, setWorking] = useState<number | null>(null)

  const run = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.suggestVisuals(clip.id, 5)
      setItems(res.suggestions)
      setNote(res.note)
      setSource(res.source)
    } catch (e) {
      notifyError(e, t('images.suggest.failed'))
    } finally {
      setLoading(false)
    }
  }, [clip.id, notifyError, t])

  // מעקב אחרי תמונות שנוצרו מתוך ההצעות
  useEffect(() => subscribe((ev) => {
    if (!ev.type.startsWith('image.')) return
    const id = ev.data?.image_id as string | undefined
    if (!id) return
    setGenerated((prev) => {
      const idx = Object.entries(prev).find(([, v]) => v.id === id)?.[0]
      if (idx === undefined) return prev
      api.getImage(id).then((img) => setGenerated((p) => ({ ...p, [Number(idx)]: img }))).catch(() => undefined)
      return prev
    })
  }), [subscribe])

  const generate = useCallback(async (idx: number, s: VisualSuggestion) => {
    setWorking(idx)
    try {
      const img = await api.createImage({ prompt: s.prompt, aspect: s.aspect, job_id: clip.job_id })
      setGenerated((prev) => ({ ...prev, [idx]: img }))
    } catch (e) {
      notifyError(e, t('images.status.failed'))
    } finally {
      setWorking(null)
    }
  }, [clip.job_id, notifyError, t])

  const insert = useCallback(async (idx: number, s: VisualSuggestion) => {
    const img = generated[idx]
    if (!img || img.status !== 'ready') return
    setWorking(idx)
    try {
      await api.addPlacement(clip.id, { image_id: img.id, role: 'insert', at_time: s.end, duration: s.duration })
      onPlaced?.()
      pushToast({ tone: 'success', title: t('images.clip.placed'),
                  body: t('images.suggest.placedAt', { time: formatTimecode(s.end) }) })
    } catch (e) {
      notifyError(e, t('images.clip.placeFailed'))
    } finally {
      setWorking(null)
    }
  }, [generated, clip.id, onPlaced, pushToast, notifyError, t])

  return (
    <div className="card-pad">
      <div className="flex items-center gap-2 mb-1">
        <IconSparkle className="w-4 h-4 text-brand-600" />
        <h3 className="section-title mb-0">{t('images.suggest.title')}</h3>
      </div>
      <p className="hint mb-4">{t('images.suggest.hint')}</p>

      <button className="btn-ghost w-full" onClick={() => void run()} disabled={loading}>
        {loading ? <Spinner className="w-3.5 h-3.5" /> : <IconRefresh className="w-3.5 h-3.5" />}
        {loading ? t('images.suggest.analyzing') : items ? t('images.suggest.again') : t('images.suggest.run')}
      </button>

      {note && (
        <p className="hint mt-3 flex items-start gap-1.5">
          <IconAlert className="w-3.5 h-3.5 text-ink-500 shrink-0 mt-px" />
          <span className="bidi-isolate">{note}</span>
        </p>
      )}

      {source === 'heuristic' && items && items.length > 0 && (
        <p className="hint mt-2">{t('images.suggest.heuristicNote')}</p>
      )}

      {items && items.length > 0 && (
        <div className="mt-4 space-y-3">
          {items.map((s, i) => (
            <SuggestionRow key={`${s.start}-${i}`} suggestion={s} image={generated[i]} busy={working === i}
                           onGenerate={() => void generate(i, s)} onInsert={() => void insert(i, s)} />
          ))}
        </div>
      )}
    </div>
  )
}

function SuggestionRow({ suggestion, image, busy, onGenerate, onInsert }: {
  suggestion: VisualSuggestion
  image?: GeneratedImage
  busy: boolean
  onGenerate: () => void
  onInsert: () => void
}) {
  const { t } = useTranslation()
  const pending = image && (image.status === 'queued' || image.status === 'generating')
  const ready = image?.status === 'ready'
  const failed = image?.status === 'failed'

  return (
    <div className="rounded-lg bg-ink-900 border border-ink-750 p-3 animate-fade-up">
      <div className="flex items-center gap-2 mb-2">
        <span className="chip bg-ink-800 text-ink-300 ltr-nums">
          {formatTimecode(suggestion.start)}–{formatTimecode(suggestion.end)}
        </span>
        <Chip><span className="bidi-isolate">{suggestion.reason}</span></Chip>
      </div>
      <p className="text-xs text-ink-300 leading-relaxed" dir="auto">„{suggestion.text}”</p>
      <p className="text-[11px] text-ink-500 mt-1.5 leading-relaxed" dir="ltr">{suggestion.prompt}</p>

      <div className="mt-3 flex items-center gap-2">
        {ready && image && (
          <img src={api.imageThumbUrl(image.id)} alt="" className="w-10 h-10 rounded-md object-cover bg-ink-800 shrink-0" />
        )}
        {!image && (
          <button className="btn-ghost btn-sm" onClick={onGenerate} disabled={busy}>
            {busy ? <Spinner className="w-3 h-3" /> : null}
            {t('images.suggest.create')}
          </button>
        )}
        {pending && (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-400">
            <Spinner className="w-3 h-3" />
            {t('images.status.generating')}
          </span>
        )}
        {ready && (
          <>
            <span className="inline-flex items-center gap-1 text-[11px] text-ok">
              <IconCheck className="w-3 h-3" />
              {t('images.suggest.ready')}
            </span>
            <button className="btn-primary btn-sm ms-auto" onClick={onInsert} disabled={busy}>
              {busy ? <Spinner className="w-3 h-3" /> : null}
              {t('images.suggest.insert')}
            </button>
          </>
        )}
        {failed && (
          <>
            <span className="inline-flex items-center gap-1 text-[11px] text-bad">
              <IconX className="w-3 h-3" />
              {image?.error || t('images.status.failed')}
            </span>
            <button className="btn-ghost btn-sm ms-auto" onClick={onGenerate} disabled={busy}>
              {t('common.retry')}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
