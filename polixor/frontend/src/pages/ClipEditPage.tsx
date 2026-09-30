// עריכת קליף: טווח, יחס מסך ופריסה, סגנון עריכה, כתוביות (v2, עם תצוגה
// מקדימה אמיתית), תיקון טקסט הכתוביות, תמונות וייצוא מחדש.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Check, Download, Film, Info, Play, Scissors } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { clamp, formatDuration, formatTimecode, KIND_LABEL } from '../lib/format'
import type { Clip, Cue, EditStyleName, ProofreadResponse, SubtitleStyle } from '../lib/types'
import { BeatStrip, EditStylePicker, EditSummary } from '../components/editing'
import { AudioMasteringSummary, DirectorPlan, QaPanel } from '../components/director'
import { ClipImagePanel, SuggestVisualsPanel } from '../components/clip-images'
import {
  Badge, Button, Callout, Card, CardHeader, EmptyState, Field, IconButton, Input, PageHeader,
  Segmented, Skeleton, Switch, cx,
} from '../components/ds'
import SubtitleEditor from '../features/SubtitleEditor'

type Aspect = '9:16' | '1:1' | '4:5' | '16:9'
type Layout = 'auto' | 'reaction' | 'auto_face' | 'center' | 'split' | 'blur_pad'
const ASPECTS: Aspect[] = ['9:16', '1:1', '4:5', '16:9']
const LAYOUTS: Layout[] = ['auto', 'reaction', 'auto_face', 'center', 'split', 'blur_pad']
type Region = { x: number; y: number; w: number; h: number }

function isV2(s: Record<string, unknown> | null | undefined): s is SubtitleStyle & Record<string, unknown> {
  return Boolean(s && 'words_per_line' in s && 'max_lines' in s && 'weight' in s)
}

/** סגנון ישן (פיקסלים) → v2 (יחסי לגובה הקליפ), מעל ברירת המחדל של השפה. */
function toV2(old: Record<string, any>, base: SubtitleStyle, height: number): SubtitleStyle {
  const out: SubtitleStyle = { ...base, preset: null }
  if (typeof old.size === 'number' && height > 0) out.size = Math.round((old.size / height) * 1000) / 10
  if (old.font) out.font = String(old.font)
  if (old.primary_color) out.color = String(old.primary_color).toUpperCase()
  if (old.outline_color) out.outline_color = String(old.outline_color).toUpperCase()
  if (old.highlight_color) out.highlight_color = String(old.highlight_color).toUpperCase()
  if (old.position) out.position = old.position
  if (old.animation === 'pop') out.animation = 'pop'
  else if (old.animation === 'punch') out.animation = 'bounce'
  else if (old.word_level === false) out.animation = 'none'
  return out
}

export default function ClipEditPage() {
  const { clipId = '' } = useParams()
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { notifyError, pushToast } = useStore()

  const [clip, setClip] = useState<Clip | null>(null)
  const [cues, setCues] = useState<Cue[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [savingCues, setSavingCues] = useState(false)

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(0)
  const [aspect, setAspect] = useState<Aspect>('16:9')
  const [layout, setLayout] = useState<Layout>('auto')
  const [subsOn, setSubsOn] = useState(true)
  const [titleCard, setTitleCard] = useState(false)
  const [style, setStyle] = useState<SubtitleStyle | null>(null)
  const [camera, setCamera] = useState<Region | null>(null)
  const [editStyle, setEditStyle] = useState<EditStyleName>('clean')
  const [initial, setInitial] = useState('')

  const videoRef = useRef<HTMLVideoElement>(null)
  const [playhead, setPlayhead] = useState(0)
  // ▶ ליד כתובית: מנגן מעט לפני ועד מעט אחרי, ועוצר
  const stopAt = useRef<number | null>(null)
  const [proof, setProof] = useState<ProofreadResponse | null>(null)
  const [onlyReview, setOnlyReview] = useState(false)
  const playCue = (cue: Cue) => {
    const v = videoRef.current
    if (!v) return
    v.currentTime = Math.max(0, cue.start - 1.0)
    stopAt.current = cue.end + 0.6
    void v.play()
  }
  const cueNeedsReview = (cue: Cue) => (cue.words || []).some((w) => w.flag === 'low')
  const cueCorrected = (cue: Cue) => (cue.words || []).some((w) => w.flag === 'corrected')
  const heardAs = (cue: Cue) => (cue.words || []).map((w) => w.asr).filter(Boolean).join(' ')
  // חלופה שהמודל החזק שמע, למשפט שסומן – לפי המילים הלא בטוחות שבכתובית
  const alternativeFor = (cue: Cue): string | null => {
    const low = (cue.words || []).filter((w) => w.flag === 'low').map((w) => w.text)
    if (!low.length || !proof) return null
    const item = proof.items.find((it) => it.alternative && low.some((w) => it.low_words.includes(w)))
    return item?.alternative ?? null
  }
  const reviewCount = cues.filter(cueNeedsReview).length

  const subLang: 'he' | 'en' = cues.find((c) => c.language)?.language === 'en' ? 'en'
    : /[֐-׿]/.test(cues.map((c) => c.text).join(' ')) || !cues.length ? 'he' : 'en'

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const c = await api.getClip(clipId)
      const cueRows = await api.getCues(clipId).catch(() => [] as Cue[])
      api.clipProofread(clipId).then(setProof).catch(() => setProof(null))
      setClip(c)
      setCues(cueRows)
      setTitle(c.title)
      setDescription(c.description)
      setStart(c.source_start)
      setEnd(c.source_end)
      const a = (ASPECTS.includes(c.aspect as Aspect) ? c.aspect : '16:9') as Aspect
      setAspect(a)
      const rp = c.render_params as Record<string, any>
      const requested = String(rp?.layout_requested || c.layout || 'auto')
      setLayout((LAYOUTS.includes(requested as Layout) ? requested : 'auto') as Layout)
      setSubsOn(c.subtitles_enabled || cueRows.length > 0)
      if (rp?.edit_style) setEditStyle(rp.edit_style as EditStyleName)
      const cam = rp?.camera_region
      if (cam && typeof cam === 'object') setCamera({ x: cam.x ?? 0, y: cam.y ?? 0, w: cam.w ?? 0.25, h: cam.h ?? 0.25 })
      const lang = cueRows.some((x) => /[֐-׿]/.test(x.text)) ? 'he' : 'en'
      let st: SubtitleStyle
      if (isV2(c.subtitle_style)) st = c.subtitle_style as unknown as SubtitleStyle
      else {
        const defaults = await api.subtitlePresets(lang)
        st = toV2(c.subtitle_style as Record<string, any>, defaults.default, c.height)
      }
      setStyle(st)
      setInitial(JSON.stringify({ s: c.source_start, e: c.source_end, a, l: requested, st, sub: c.subtitles_enabled, es: rp?.edit_style ?? 'clean' }))
    } catch (e) {
      notifyError(e, t('editor.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [clipId, notifyError, t])

  useEffect(() => { void load() }, [load])

  const dirtyMeta = clip ? (title !== clip.title || description !== clip.description) : false
  const dirtyExport = useMemo(() => initial !== JSON.stringify({ s: start, e: end, a: aspect, l: layout, st: style, sub: subsOn, es: editStyle }) || titleCard,
    [initial, start, end, aspect, layout, style, subsOn, editStyle, titleCard])

  const saveMeta = async () => {
    setSaving(true)
    try {
      setClip(await api.patchClip(clipId, { title, description }))
      pushToast({ tone: 'success', title: t('editor.detailsSaved') })
    } catch (e) { notifyError(e, t('editor.detailsFailed')) } finally { setSaving(false) }
  }

  const saveCues = async () => {
    setSavingCues(true)
    try {
      setCues(await api.putCues(clipId, cues.map((c) => ({ id: c.id, start: c.start, end: c.end, text: c.text }))))
      pushToast({ tone: 'success', title: t('editor.cuesSaved'), body: t('editor.cuesSavedBody') })
    } catch (e) { notifyError(e, t('editor.cuesFailed')) } finally { setSavingCues(false) }
  }

  const doExport = async () => {
    setExporting(true)
    try {
      const updated = await api.reexport(clipId, {
        source_start: start, source_end: end, aspect,
        layout: aspect === '16:9' ? undefined : layout,
        subtitles_enabled: subsOn, subtitle_style: style ?? undefined,
        camera_region: layout === 'split' ? camera : undefined,
        title_card: titleCard, edit_style: editStyle,
      })
      pushToast({ tone: 'success', title: t('editor.exported') })
      setClip(updated)
      await load()
      videoRef.current?.load()
    } catch (e) { notifyError(e, t('editor.exportFailed')) } finally { setExporting(false) }
  }

  const saveCameraToSettings = async () => {
    if (!camera) return
    try {
      await api.saveCameraRegion(camera)
      pushToast({ tone: 'success', title: t('editor.camera.saved'), body: t('editor.camera.savedBody') })
    } catch (e) { notifyError(e, t('editor.camera.saveFailed')) }
  }

  if (loading) return <div className="space-y-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-96" /></div>
  if (!clip) {
    return <Card><EmptyState title={t('editor.notFound')}
                             action={<Link to="/clips" className="btn-primary">{t('editor.backToGallery')}</Link>} /></Card>
  }

  const duration = end - start
  const rp = (clip.render_params || {}) as Record<string, any>
  const beats = (rp.beats ?? []) as { start: number; end: number; zoom: number; speed: number; reason: string }[]
  const rawDuration = Number(rp.raw_duration ?? duration)
  const tracked = Number(rp.tracked_ratio ?? 0)
  const reframeNote = String(rp.reframe_note ?? '')
  const vertical = clip.height > clip.width

  return (
    <div>
      <PageHeader
        title={t('editor.title')}
        subtitle={<span className="bidi-isolate">{clip.title}</span>}
        actions={<>
          <a href={api.clipDownloadUrl(clip.id)} className="btn-ghost btn-sm"><Download className="w-3.5 h-3.5" />{t('clips.download')}</a>
          <Button size="sm" onClick={() => navigate(-1)}>{t('editor.back')}</Button>
        </>} />

      <div className="grid gap-5 lg:grid-cols-5">
        {/* ---- נגן, טווח, כתוביות ---- */}
        <div className="lg:col-span-3 space-y-5 min-w-0">
          <Card className="p-4">
            <video ref={videoRef} src={api.clipFileUrl(clip.id)} controls
                   onTimeUpdate={(e) => {
                     const v = e.target as HTMLVideoElement
                     setPlayhead(v.currentTime)
                     if (stopAt.current !== null && v.currentTime >= stopAt.current) {
                       stopAt.current = null
                       v.pause()
                     }
                   }}
                   className={cx('w-full rounded-lg bg-black', vertical && 'max-h-[56vh] mx-auto w-auto')} />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge tone={clip.kind === 'short' ? 'brand' : 'neutral'}>{KIND_LABEL[clip.kind]}</Badge>
              <Badge><span className="ltr-nums">{clip.width}×{clip.height}</span></Badge>
              <Badge><span className="ltr-nums">{clip.aspect}</span></Badge>
              {clip.layout && clip.layout !== 'center' && <Badge>{t(`editor.layouts.${clip.layout}.label`, { defaultValue: clip.layout })}</Badge>}
              <span className="ms-auto text-xs text-ink-500">{t('editor.playhead')} <span className="ltr-nums">{formatTimecode(playhead)}</span></span>
            </div>
            {beats.length > 1 && (
              <div className="mt-4 pt-4 border-t border-ink-750">
                <BeatStrip beats={beats} rawDuration={rawDuration}
                           onSeek={(s) => { if (videoRef.current) videoRef.current.currentTime = s }} />
              </div>
            )}
          </Card>

          <Card>
            <CardHeader icon={<Scissors className="w-4 h-4" />} title={t('editor.range.title')} subtitle={t('editor.range.hint')} />
            <div className="px-5 pb-5">
              <div className="grid grid-cols-2 gap-4">
                <TimeField label={t('editor.range.start')} value={start} onChange={setStart}
                           onFromPlayhead={() => setStart(clamp(clip.source_start + playhead, 0, end - 1))} />
                <TimeField label={t('editor.range.end')} value={end} onChange={setEnd}
                           onFromPlayhead={() => setEnd(Math.max(start + 1, clip.source_start + playhead))} />
              </div>
              <div className="mt-3 flex items-center justify-between text-xs">
                <span className="text-ink-400">{t('editor.range.newLength')} <span className="ltr-nums text-ink-100">{formatDuration(duration)}</span></span>
                {(start !== clip.source_start || end !== clip.source_end) && (
                  <button type="button" className="text-brand-600 hover:underline"
                          onClick={() => { setStart(clip.source_start); setEnd(clip.source_end) }}>{t('editor.range.reset')}</button>
                )}
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader title={t('editor.cues.title')} subtitle={t('editor.cues.hint')}
                        actions={cues.length > 0 && (
                          <Button size="sm" variant="primary" loading={savingCues} onClick={saveCues}
                                  icon={<Check className="w-3.5 h-3.5" />}>{t('editor.cues.save')}</Button>)} />
            <div className="px-5 pb-5">
              {cues.length > 0 && (
                <div className="mb-3 flex flex-wrap items-center gap-3 text-xs" data-testid="cue-review-bar">
                  {reviewCount > 0
                    ? <Badge tone="warn">{t('editor.cues.toReview', { count: reviewCount })}</Badge>
                    : <Badge tone="ok">{t('editor.cues.noneToReview')}</Badge>}
                  {proof?.stats?.corrected ? <span className="text-ink-500">{t('editor.cues.autoCorrected', { count: proof.stats.corrected })}</span> : null}
                  {reviewCount > 0 && (
                    <label className="inline-flex items-center gap-1.5 text-ink-400">
                      <input type="checkbox" checked={onlyReview} onChange={(e) => setOnlyReview(e.target.checked)} />
                      {t('editor.cues.onlyReview')}
                    </label>
                  )}
                </div>
              )}
              {cues.length === 0 ? <p className="text-sm text-ink-500">{t('editor.cues.none')}</p> : (
                <div className="max-h-96 overflow-y-auto space-y-2 pe-1">
                  {cues.map((cue, i) => (onlyReview && !cueNeedsReview(cue)) ? null : (
                    <div key={cue.id ?? i} data-cue-review={cueNeedsReview(cue) ? 'low' : cueCorrected(cue) ? 'corrected' : 'ok'}
                         className={cx('rounded-lg border p-2.5 transition-colors',
                                       playhead >= cue.start && playhead <= cue.end
                                         ? 'border-brand-500/50 bg-brand-600/5' : 'border-ink-750 bg-ink-900')}>
                      <div className="flex items-center gap-2 mb-1.5 min-w-0">
                        <IconButton label={t('editor.cues.play')} className="!p-1 shrink-0" tipAlign="start"
                                    onClick={() => playCue(cue)}
                                    data-cue-play icon={<Play className="w-3.5 h-3.5" />} />
                        <button type="button" className="text-[11px] text-ink-500 hover:text-brand-600 ltr-nums shrink-0"
                                title={t('editor.cues.jump')}
                                onClick={() => { if (videoRef.current) videoRef.current.currentTime = cue.start }}>
                          {formatTimecode(cue.start)} → {formatTimecode(cue.end)}
                        </button>
                        {cue.edited && <Badge tone="warn">{t('editor.cues.edited')}</Badge>}
                        {cue.original_text && cue.text !== cue.original_text && (
                          <span className="text-[11px] text-ink-500 truncate" title={cue.original_text}>
                            {t('editor.cues.original')} <span className="bidi-isolate">{cue.original_text}</span>
                          </span>
                        )}
                      </div>
                      {cueNeedsReview(cue) && (
                        <div className="mb-1.5 text-[11px] text-amber-500">
                          <span className="inline-flex flex-wrap items-center gap-1.5">
                            {t('editor.cues.uncertain')}
                            {(cue.words || []).filter((w) => w.flag === 'low').map((w, k) => (
                              <span key={k} className="rounded bg-amber-500/10 px-1.5 py-0.5">
                                <bdi dir="auto">{w.text}</bdi>{' '}
                                <span className="ltr-nums opacity-80">{Math.round((w.p ?? 0) * 100)}%</span>
                              </span>
                            ))}
                          </span>
                          {alternativeFor(cue) && (
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-ink-400">
                              <span>{t('editor.cues.alternative')} <bdi dir="auto" className="text-ink-200">{alternativeFor(cue)}</bdi></span>
                              <button type="button" className="text-brand-600 hover:underline"
                                      onClick={() => setCues((prev) => prev.map((c, j) => {
                                        if (j !== i) return c
                                        const low = (c.words || []).filter((w) => w.flag === 'low').map((w) => w.text)
                                        const it = proof?.items.find((x) => x.alternative && low.some((w) => x.low_words.includes(w)))
                                        return it && it.alternative && c.text.includes(it.original)
                                          ? { ...c, text: c.text.replace(it.original, it.alternative) }
                                          : { ...c, text: it?.alternative ?? c.text }
                                      }))}>{t('editor.cues.useAlternative')}</button>
                            </div>
                          )}
                        </div>
                      )}
                      {cueCorrected(cue) && heardAs(cue) && (
                        <div className="mb-1.5 text-[11px] text-ink-500">
                          {t('editor.cues.autoFixed')} <bdi dir="auto">{heardAs(cue)}</bdi>{' '}
                          <button type="button" className="text-brand-600 hover:underline"
                                  onClick={() => setCues((prev) => prev.map((c, j) => j === i ? { ...c, text: heardAs(c) } : c))}>
                            {t('editor.cues.restoreHeard')}</button>
                        </div>
                      )}
                      <textarea className="field !py-1.5 text-sm resize-none" dir="auto"
                                aria-label={t('editor.cues.textLabel', { n: i + 1 })}
                                rows={Math.min(3, Math.ceil(cue.text.length / 46) || 1)} value={cue.text}
                                onChange={(e) => setCues((prev) => prev.map((c, j) => j === i ? { ...c, text: e.target.value } : c))} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>

          {subsOn && cues.length > 0 && style && (
            <Card>
              <CardHeader title={t('editor.subtitleStyle')} subtitle={t('editor.subtitleStyleHint')} />
              <div className="px-5 pb-5">
                <SubtitleEditor value={style} onChange={setStyle} language={subLang} aspect={aspect}
                                clipId={clip.id} compact />
              </div>
            </Card>
          )}
        </div>

        {/* ---- פאנל צד ---- */}
        <div className="lg:col-span-2 space-y-5 min-w-0">
          <Card className="p-5 space-y-4 lg:sticky lg:top-20">
            <h3 className="section-title">{t('editor.export.title')}</h3>
            <Field label={t('editor.export.aspect')}>
              <Segmented<Aspect> value={aspect} onChange={setAspect}
                                 options={ASPECTS.map((a) => ({ value: a, label: <span className="ltr-nums">{a}</span> }))} />
            </Field>
            {aspect !== '16:9' && (
              <Field label={t('editor.export.layout')} hint={t(`editor.layouts.${layout}.hint`)}>
                <select className="field" value={layout} onChange={(e) => setLayout(e.target.value as Layout)}>
                  {LAYOUTS.map((l) => <option key={l} value={l}>{t(`editor.layouts.${l}.label`)}</option>)}
                </select>
              </Field>
            )}
            {aspect !== '16:9' && reframeNote && (
              <Callout tone="neutral">
                <span className="bidi-isolate">{reframeNote}</span>
                {tracked > 0 && ` ${t('editor.export.tracked', { percent: Math.round(tracked * 100) })}`}
              </Callout>
            )}
            {aspect !== '16:9' && layout === 'split' && (
              <CameraPicker jobId={clip.job_id} atSeconds={start + Math.min(2, duration / 3)}
                            region={camera} onChange={setCamera} onSaveDefault={() => void saveCameraToSettings()} />
            )}
            <Switch checked={subsOn} onChange={setSubsOn} disabled={cues.length === 0}
                    label={t('editor.export.burnSubtitles')}
                    description={cues.length === 0 ? t('editor.cues.none') : undefined} />
            <Switch checked={titleCard} onChange={setTitleCard} label={t('editor.export.titleCard')}
                    description={t('editor.export.titleCardHint')} />
            <Button variant="primary" size="lg" className="w-full" loading={exporting}
                    disabled={duration < 1} onClick={doExport} icon={<Film className="w-4 h-4" />}>
              {exporting ? t('editor.export.exporting') : t('editor.export.button')}
            </Button>
            <p className="hint text-center">{dirtyExport ? t('editor.export.hint') : t('editor.export.noChanges')}</p>
          </Card>

          <Card className="p-5 space-y-3">
            <h3 className="section-title">{t('editor.details.title')}</h3>
            <Field label={t('editor.details.clipTitle')} htmlFor="c-title">
              <Input id="c-title" value={title} maxLength={200} dir="auto" onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <Field label={t('editor.details.description')} htmlFor="c-desc">
              <textarea id="c-desc" className="field resize-none" rows={3} value={description} maxLength={1000} dir="auto"
                        onChange={(e) => setDescription(e.target.value)} />
            </Field>
            {clip.reason && (
              <Callout tone="neutral" title={t('clips.gallery.whyChosen')}><span className="bidi-isolate">{clip.reason}</span></Callout>
            )}
            <Button className="w-full" onClick={saveMeta} loading={saving} disabled={!dirtyMeta}>{t('editor.details.save')}</Button>
          </Card>

          <Card className="p-5">
            <h3 className="section-title">{t('editor.editStyle.title')}</h3>
            <p className="hint mb-4">{t('editor.editStyle.hint')}</p>
            <EditStylePicker value={editStyle} onChange={setEditStyle} />
            <div className="mt-4"><EditSummary params={rp} /></div>
          </Card>

          <ClipImagePanel clip={clip} onChanged={() => void load()} />
          {cues.length > 0 && <SuggestVisualsPanel clip={clip} onPlaced={() => void load()} />}
          <QaPanel params={rp} />
          <DirectorPlan params={rp} />
          <AudioMasteringSummary params={rp} />
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
function TimeField({ label, value, onChange, onFromPlayhead }: {
  label: string
  value: number
  onChange: (v: number) => void
  onFromPlayhead: () => void
}) {
  const { t } = useTranslation()
  return (
    <Field label={label}>
      <div className="flex gap-1.5">
        <input type="number" step="0.1" min={0} className="field ltr-nums !px-2" aria-label={label}
               value={value.toFixed(1)} onChange={(e) => onChange(Math.max(0, parseFloat(e.target.value) || 0))} />
        <Button size="sm" onClick={onFromPlayhead}>{t('editor.range.fromPlayer')}</Button>
      </div>
      <div className="mt-1 text-[11px] text-ink-500 ltr-nums">{formatDuration(value)}</div>
    </Field>
  )
}

// --------------------------------------------------------------------------
function CameraPicker({ jobId, atSeconds, region, onChange, onSaveDefault }: {
  jobId: string
  atSeconds: number
  region: Region | null
  onChange: (r: Region) => void
  onSaveDefault: () => void
}) {
  const { t } = useTranslation()
  const boxRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<{ x0: number; y0: number } | null>(null)
  const [failed, setFailed] = useState(false)
  const rel = (e: React.MouseEvent) => {
    const r = boxRef.current!.getBoundingClientRect()
    return { x: clamp((e.clientX - r.left) / r.width, 0, 1), y: clamp((e.clientY - r.top) / r.height, 0, 1) }
  }
  const src = useMemo(() => api.jobFrameUrl(jobId, Math.max(0, atSeconds), 640), [jobId, atSeconds])
  return (
    <div>
      <div className="label">{t('editor.camera.title')}</div>
      <p className="hint mb-2">{t('editor.camera.hint')}</p>
      {failed ? (
        <Callout tone="warn">{t('editor.camera.noFrame')}</Callout>
      ) : (
        <div ref={boxRef} dir="ltr"
             className="relative rounded-lg overflow-hidden border border-ink-700 cursor-crosshair select-none bg-ink-900"
             onMouseDown={(e) => { e.preventDefault(); const p = rel(e); setDrag({ x0: p.x, y0: p.y }) }}
             onMouseMove={(e) => {
               if (!drag) return
               const p = rel(e)
               onChange({ x: Math.min(drag.x0, p.x), y: Math.min(drag.y0, p.y), w: Math.abs(p.x - drag.x0), h: Math.abs(p.y - drag.y0) })
             }}
             onMouseUp={() => setDrag(null)} onMouseLeave={() => setDrag(null)}>
          <img src={src} alt={t('editor.camera.frameAlt')} className="w-full block pointer-events-none"
               onError={() => setFailed(true)} draggable={false} />
          {region && region.w > 0.01 && (
            <div className="absolute border-2 border-brand-400 bg-brand-500/15 pointer-events-none"
                 style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.w * 100}%`, height: `${region.h * 100}%` }} />
          )}
        </div>
      )}
      <div className="mt-2 grid grid-cols-4 gap-1.5" dir="ltr">
        {(['x', 'y', 'w', 'h'] as const).map((k) => (
          <label key={k} className="text-[10px] text-ink-500">
            <span className="block mb-0.5">{k}</span>
            <input type="number" step="0.01" min={0} max={1} className="field !py-1 !px-1.5 text-[11px]"
                   value={(region?.[k] ?? 0).toFixed(2)}
                   onChange={(e) => onChange({ x: region?.x ?? 0, y: region?.y ?? 0, w: region?.w ?? 0.25, h: region?.h ?? 0.25,
                     [k]: clamp(parseFloat(e.target.value) || 0, 0, 1) })} />
          </label>
        ))}
      </div>
      <Button size="sm" className="w-full mt-2" onClick={onSaveDefault} disabled={!region || region.w < 0.02}
              icon={<Info className="w-3.5 h-3.5" />}>{t('editor.camera.saveDefault')}</Button>
    </div>
  )
}
