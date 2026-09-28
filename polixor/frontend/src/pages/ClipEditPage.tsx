import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '../App'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { clamp, formatDuration, formatTimecode, KIND_LABEL } from '../lib/format'
import type { CaptionAnimation, Clip, Cue, EditStyleName } from '../lib/types'
import {
  BeatStrip, CaptionAnimationPicker, EditStylePicker, EditSummary,
} from '../components/editing'
import { AudioMasteringSummary, DirectorPlan, QaPanel } from '../components/director'
import { ClipImagePanel, SuggestVisualsPanel } from '../components/clip-images'
import {
  Chip, EmptyState, IconAlert, IconCheck, IconDownload, IconFilm, IconScissors,
  Spinner,
} from '../components/ui'

type Layout = 'center' | 'auto_face' | 'split' | 'blur_pad'
type Aspect = '16:9' | '9:16'

const LAYOUTS: { key: Layout; label: string; hint: string }[] = [
  { key: 'center', label: 'חיתוך מרכזי', hint: 'חותך את מרכז הפריים. תמיד עובד.' },
  { key: 'auto_face', label: 'מעקב אחרי פנים', hint: 'עוקב אחרי הפנים הדומיננטיות. אם לא זוהו פנים — חוזר לחיתוך מרכזי.' },
  { key: 'split', label: 'מסך מפוצל', hint: 'מצלמת הסטרימר למעלה, גיימפליי למטה. דורש הגדרת אזור המצלמה.' },
  { key: 'blur_pad', label: 'מסגרת מלאה על רקע מטושטש', hint: 'שום דבר לא נחתך והתמונה נשארת חדה, כי אין הגדלה. תופס פחות מסך.' },
]

export default function ClipEditPage() {
  const { clipId = '' } = useParams()
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
  const [layout, setLayout] = useState<Layout>('center')
  const [subsOn, setSubsOn] = useState(true)
  const [titleCard, setTitleCard] = useState(false)
  const [style, setStyle] = useState({
    font: 'DejaVu Sans', size: 54, primary_color: '#FFFFFF',
    outline_color: '#000000', highlight_color: '#FFD400',
    position: 'bottom' as 'top' | 'middle' | 'bottom',
    word_level: true,
  })
  const [camera, setCamera] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [editStyle, setEditStyle] = useState<EditStyleName>('clean')
  const [captionAnim, setCaptionAnim] = useState<CaptionAnimation>('pop')

  const videoRef = useRef<HTMLVideoElement>(null)
  const [playhead, setPlayhead] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const c = await api.getClip(clipId)
      setClip(c)
      setTitle(c.title)
      setDescription(c.description)
      setStart(c.source_start)
      setEnd(c.source_end)
      setAspect((c.aspect as Aspect) || '16:9')
      setLayout((c.layout as Layout) || 'center')
      setSubsOn(c.subtitles_enabled)
      const s = c.subtitle_style as any
      if (s && typeof s === 'object') {
        setStyle((prev) => ({
          ...prev,
          font: s.font ?? prev.font,
          size: s.size ?? prev.size,
          primary_color: s.primary_color ?? prev.primary_color,
          outline_color: s.outline_color ?? prev.outline_color,
          highlight_color: s.highlight_color ?? prev.highlight_color,
          position: s.position ?? prev.position,
          word_level: s.word_level ?? prev.word_level,
        }))
      }
      const rp = c.render_params as any
      if (rp?.edit_style) setEditStyle(rp.edit_style as EditStyleName)
      if (s?.animation) setCaptionAnim(s.animation as CaptionAnimation)
      const cam = rp?.camera_region
      if (cam && typeof cam === 'object') {
        setCamera({ x: cam.x ?? 0, y: cam.y ?? 0, w: cam.w ?? 0.25, h: cam.h ?? 0.25 })
      }
      setCues(await api.getCues(clipId).catch(() => []))
    } catch (e) {
      notifyError(e, 'טעינת הקליפ נכשלה')
    } finally {
      setLoading(false)
    }
  }, [clipId, notifyError])

  useEffect(() => { void load() }, [load])

  const dirtyMeta = clip ? (title !== clip.title || description !== clip.description) : false
  const dirtyExport = clip ? (
    Math.abs(start - clip.source_start) > 0.05 ||
    Math.abs(end - clip.source_end) > 0.05 ||
    aspect !== clip.aspect ||
    layout !== clip.layout ||
    subsOn !== clip.subtitles_enabled ||
    editStyle !== ((clip.render_params as any)?.edit_style ?? 'clean') ||
    captionAnim !== ((clip.subtitle_style as any)?.animation ?? 'pop') ||
    titleCard ||
    JSON.stringify(style) !== JSON.stringify({
      font: (clip.subtitle_style as any)?.font ?? style.font,
      size: (clip.subtitle_style as any)?.size ?? style.size,
      primary_color: (clip.subtitle_style as any)?.primary_color ?? style.primary_color,
      outline_color: (clip.subtitle_style as any)?.outline_color ?? style.outline_color,
      highlight_color: (clip.subtitle_style as any)?.highlight_color ?? style.highlight_color,
      position: (clip.subtitle_style as any)?.position ?? style.position,
      word_level: (clip.subtitle_style as any)?.word_level ?? style.word_level,
    })
  ) : false

  const saveMeta = async () => {
    setSaving(true)
    try {
      const updated = await api.patchClip(clipId, { title, description })
      setClip(updated)
      pushToast({ tone: 'success', title: 'הפרטים נשמרו' })
    } catch (e) { notifyError(e, 'שמירת הפרטים נכשלה') } finally { setSaving(false) }
  }

  const saveCues = async () => {
    setSavingCues(true)
    try {
      const saved = await api.putCues(clipId,
        cues.map((c) => ({ id: c.id, start: c.start, end: c.end, text: c.text })))
      setCues(saved)
      pushToast({
        tone: 'success', title: 'הכתוביות נשמרו',
        body: 'הן ייצרבו בייצוא הבא של הקליפ.',
      })
    } catch (e) { notifyError(e, 'שמירת הכתוביות נכשלה') } finally { setSavingCues(false) }
  }

  const doExport = async () => {
    setExporting(true)
    try {
      const updated = await api.reexport(clipId, {
        source_start: start,
        source_end: end,
        aspect,
        layout,
        subtitles_enabled: subsOn,
        subtitle_style: style,
        camera_region: layout === 'split' ? camera : undefined,
        title_card: titleCard,
        edit_style: editStyle,
        caption_animation: captionAnim,
      })
      setClip(updated)
      setStart(updated.source_start)
      setEnd(updated.source_end)
      pushToast({ tone: 'success', title: 'הקליפ יוצא מחדש' })
      if (videoRef.current) videoRef.current.load()
    } catch (e) { notifyError(e, 'הייצוא מחדש נכשל') } finally { setExporting(false) }
  }

  const saveCameraToSettings = async () => {
    if (!camera) return
    try {
      await api.saveCameraRegion(camera)
      pushToast({
        tone: 'success', title: 'אזור המצלמה נשמר',
        body: 'הוא ישמש כברירת מחדל בשידורים הבאים.',
      })
    } catch (e) { notifyError(e, 'שמירת אזור המצלמה נכשלה') }
  }

  if (loading) {
    return <div className="p-4 sm:p-8 max-w-6xl mx-auto space-y-4">
      <div className="skeleton h-10 w-72" /><div className="skeleton h-96" />
    </div>
  }
  if (!clip) {
    return <div className="p-4 sm:p-8 max-w-6xl mx-auto">
      <EmptyState title="הקליפ לא נמצא"
                  action={<Link to="/clips" className="btn-primary">חזרה לגלריה</Link>} />
    </div>
  }

  const duration = end - start
  const beats = ((clip.render_params as any)?.beats ?? []) as
    { start: number; end: number; zoom: number; speed: number; reason: string }[]
  const rawDuration = Number((clip.render_params as any)?.raw_duration ?? duration)
  const tracked = Number((clip.render_params as any)?.tracked_ratio ?? 0)
  const reframeNote = String((clip.render_params as any)?.reframe_note ?? '')

  return (
    <div className="p-4 sm:p-8 max-w-6xl mx-auto">
      <PageHeader
        title="עריכת קליפ"
        subtitle={clip.title}
        actions={
          <>
            <a href={api.clipDownloadUrl(clip.id)} className="btn-ghost btn-sm">
              <IconDownload className="w-3.5 h-3.5" />הורד
            </a>
            <button className="btn-ghost btn-sm" onClick={() => navigate(-1)}>חזור</button>
          </>
        }
      />

      <div className="grid lg:grid-cols-5 gap-5">
        {/* ---- נגן + חיתוך ---- */}
        <div className="lg:col-span-3 space-y-5">
          <div className="card p-4">
            <video
              ref={videoRef}
              src={api.clipFileUrl(clip.id)}
              controls
              onTimeUpdate={(e) => setPlayhead((e.target as HTMLVideoElement).currentTime)}
              className={`w-full rounded-lg bg-black ${
                clip.aspect === '9:16' ? 'max-h-[56vh] mx-auto w-auto' : ''}`}
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Chip tone={clip.kind === 'short' ? 'brand' : 'default'}>
                {KIND_LABEL[clip.kind]}
              </Chip>
              <Chip><span className="ltr-nums">{clip.width}×{clip.height}</span></Chip>
              <Chip>{clip.aspect}</Chip>
              {clip.layout !== 'center' && <Chip>{LAYOUTS.find(l => l.key === clip.layout)?.label}</Chip>}
              <span className="text-[11px] text-ink-500 mr-auto">
                נגן: <span className="ltr-nums">{formatTimecode(playhead)}</span>
              </span>
            </div>

            {beats.length > 1 && (
              <div className="mt-4 pt-4 border-t border-ink-800">
                <BeatStrip beats={beats} rawDuration={rawDuration}
                           onSeek={(t) => {
                             if (videoRef.current) videoRef.current.currentTime = t
                           }} />
              </div>
            )}
          </div>

          {/* ---- טווח חיתוך ---- */}
          <div className="card-pad">
            <h3 className="section-title mb-1 flex items-center gap-2">
              <IconScissors className="w-4 h-4 text-ink-500" />טווח בשידור המקורי
            </h3>
            <p className="hint mb-4">
              הזמנים הם ביחס לשידור המלא. לחיצה על "קבע מהנגן" משתמשת במיקום
              הנגן ומוסיפה אותו לזמן ההתחלה הנוכחי של הקליפ.
            </p>

            <div className="grid grid-cols-2 gap-4">
              <TimeField label="התחלה" value={start} onChange={setStart}
                         onFromPlayhead={() => setStart(clamp(clip.source_start + playhead, 0, end - 1))} />
              <TimeField label="סיום" value={end} onChange={setEnd}
                         onFromPlayhead={() => setEnd(Math.max(start + 1, clip.source_start + playhead))} />
            </div>

            <div className="mt-3 flex items-center justify-between text-xs">
              <span className="text-ink-400">
                אורך חדש: <span className="ltr-nums text-white">{formatDuration(duration)}</span>
              </span>
              {(start !== clip.source_start || end !== clip.source_end) && (
                <button className="text-brand-400 hover:text-brand-300"
                        onClick={() => { setStart(clip.source_start); setEnd(clip.source_end) }}>
                  שחזר טווח מקורי
                </button>
              )}
            </div>
          </div>

          {/* ---- כתוביות ---- */}
          <div className="card-pad">
            <div className="flex items-center justify-between mb-1">
              <h3 className="section-title">כתוביות</h3>
              {cues.length > 0 && (
                <button className="btn-primary btn-sm" onClick={() => void saveCues()}
                        disabled={savingCues}>
                  {savingCues ? <Spinner className="w-3.5 h-3.5" />
                    : <IconCheck className="w-3.5 h-3.5" />}
                  שמור תיקונים
                </button>
              )}
            </div>
            <p className="hint mb-4">
              התיקונים נשמרים ונצרבים בייצוא הבא. הטקסט המקורי מהתמלול נשמר גם הוא,
              כדי שתמיד אפשר להשוות למה שנאמר בפועל.
            </p>

            {cues.length === 0 ? (
              <p className="text-xs text-ink-500">
                אין כתוביות לקליפ הזה (התמלול היה מושבת או לא היה זמין).
              </p>
            ) : (
              <div className="max-h-96 overflow-y-auto space-y-2 pl-1">
                {cues.map((cue, i) => (
                  <div key={cue.id ?? i}
                       className={`rounded-lg border p-2.5 transition-colors
                         ${playhead >= cue.start && playhead <= cue.end
                           ? 'border-brand-500/50 bg-brand-600/5' : 'border-ink-750 bg-ink-900'}`}>
                    <div className="flex items-center gap-2 mb-1.5">
                      <button className="text-[11px] text-ink-500 hover:text-brand-400 ltr-nums"
                              onClick={() => {
                                if (videoRef.current) videoRef.current.currentTime = cue.start
                              }}
                              title="קפוץ לנקודה זו">
                        {formatTimecode(cue.start)} ← {formatTimecode(cue.end)}
                      </button>
                      {cue.edited && <Chip tone="warn">תוקן</Chip>}
                      {cue.original_text && cue.text !== cue.original_text && (
                        <span className="text-[10px] text-ink-600 truncate"
                              title={`מקור: ${cue.original_text}`}>
                          מקור: {cue.original_text}
                        </span>
                      )}
                    </div>
                    <textarea
                      className="field !py-1.5 text-sm resize-none"
                      rows={Math.min(3, Math.ceil(cue.text.length / 46) || 1)}
                      value={cue.text}
                      onChange={(e) => setCues((prev) => prev.map((c, j) =>
                        j === i ? { ...c, text: e.target.value } : c))}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ---- פאנל צד ---- */}
        <div className="lg:col-span-2 space-y-5">
          {/* פרטים */}
          <div className="card-pad">
            <h3 className="section-title mb-4">פרטי הקליפ</h3>
            <div className="space-y-3">
              <div>
                <label className="label">כותרת</label>
                <input className="field" value={title} maxLength={200}
                       onChange={(e) => setTitle(e.target.value)} />
              </div>
              <div>
                <label className="label">תיאור</label>
                <textarea className="field resize-none" rows={3} value={description}
                          maxLength={1000}
                          onChange={(e) => setDescription(e.target.value)} />
              </div>
              {clip.reason && (
                <div className="rounded-lg bg-ink-900 border border-ink-750 p-3">
                  <div className="text-[11px] text-ink-500 mb-1">סיבת הבחירה</div>
                  <p className="text-xs text-ink-400 leading-relaxed">{clip.reason}</p>
                </div>
              )}
              <button className="btn-ghost w-full" onClick={() => void saveMeta()}
                      disabled={!dirtyMeta || saving}>
                {saving && <Spinner className="w-3.5 h-3.5" />}
                שמור פרטים
              </button>
            </div>
          </div>

          {/* עריכה */}
          <div className="card-pad">
            <h3 className="section-title mb-1">סגנון עריכה</h3>
            <p className="hint mb-4">
              קובע כמה אוויר מת מוסר, האם משתנה גודל הפריים בחיתוכים,
              ואיך נראות הכתוביות. הייצוא מחדש בונה תכנית חדשה מאפס.
            </p>
            <EditStylePicker value={editStyle} onChange={setEditStyle} />

            <div className="mt-4">
              <label className="label">אנימציית כתוביות</label>
              <CaptionAnimationPicker value={captionAnim} onChange={setCaptionAnim}
                                      disabled={!subsOn || cues.length === 0} />
            </div>

            <div className="mt-4">
              <EditSummary params={clip.render_params as Record<string, any>} />
            </div>
          </div>

          {/* תמונות */}
          <ClipImagePanel clip={clip} onChanged={() => void load()} />

          {/* הצעות ויזואליות מהתמלול */}
          {cues.length > 0 && (
            <SuggestVisualsPanel clip={clip} onPlaced={() => void load()} />
          )}

          {/* ---- בדיקת איכות, תכנית ה-AI ואודיו ---- */}
          <QaPanel params={clip.render_params ?? {}} />
          <DirectorPlan params={clip.render_params ?? {}} />
          <AudioMasteringSummary params={clip.render_params ?? {}} />

          {/* פריסה */}
          <div className="card-pad">
            <h3 className="section-title mb-4">פריסה וייצוא</h3>

            <label className="label">יחס מסך</label>
            <div className="grid grid-cols-2 gap-2 mb-4">
              {(['16:9', '9:16'] as Aspect[]).map((a) => (
                <button key={a} onClick={() => setAspect(a)}
                        className={`btn btn-sm ${aspect === a
                          ? 'bg-brand-600/20 text-brand-300 ring-1 ring-brand-500/40'
                          : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-white'}`}>
                  {a === '16:9' ? 'אופקי 16:9' : 'אנכי 9:16'}
                </button>
              ))}
            </div>

            {aspect === '9:16' && (
              <>
                <label className="label">פריסה אנכית</label>
                <div className="space-y-2 mb-4">
                  {LAYOUTS.map((l) => (
                    <label key={l.key}
                           className={`flex items-start gap-2.5 rounded-lg border p-2.5 cursor-pointer
                             transition-colors ${layout === l.key
                               ? 'border-brand-500/50 bg-brand-600/10'
                               : 'border-ink-750 bg-ink-900 hover:border-ink-600'}`}>
                      <input type="radio" name="layout" className="mt-0.5 accent-brand-500"
                             checked={layout === l.key}
                             onChange={() => setLayout(l.key)} />
                      <div>
                        <div className="text-sm text-white">{l.label}</div>
                        <p className="hint mt-0.5">{l.hint}</p>
                      </div>
                    </label>
                  ))}
                </div>

                {reframeNote && (
                  <div className="mb-4 rounded-lg bg-ink-900 border border-ink-750 p-2.5
                                  flex items-start gap-2">
                    <IconAlert className="w-3.5 h-3.5 text-brand-400 shrink-0 mt-0.5" />
                    <p className="text-[11px] text-ink-400 leading-relaxed">
                      {reframeNote}
                      {tracked > 0 && ` (מעקב ב-${Math.round(tracked * 100)}% מהקטע)`}
                    </p>
                  </div>
                )}

                {layout === 'split' && (
                  <CameraPicker
                    jobId={clip.job_id}
                    atSeconds={start + Math.min(2, duration / 3)}
                    region={camera}
                    onChange={setCamera}
                    onSaveDefault={() => void saveCameraToSettings()}
                  />
                )}
              </>
            )}

            <label className="flex items-center gap-2.5 mb-3 cursor-pointer">
              <input type="checkbox" className="accent-brand-500 w-4 h-4"
                     checked={subsOn} onChange={(e) => setSubsOn(e.target.checked)}
                     disabled={cues.length === 0} />
              <span className="text-sm text-ink-300">
                צרוב כתוביות
                {cues.length === 0 && <span className="text-ink-600"> (אין כתוביות)</span>}
              </span>
            </label>

            <label className="flex items-center gap-2.5 mb-4 cursor-pointer">
              <input type="checkbox" className="accent-brand-500 w-4 h-4"
                     checked={titleCard} onChange={(e) => setTitleCard(e.target.checked)} />
              <span className="text-sm text-ink-300">כרטיס כותרת בתחילת הסרטון</span>
            </label>

            {subsOn && cues.length > 0 && (
              <SubtitleStyleEditor style={style} onChange={setStyle} />
            )}

            <button className="btn-primary w-full mt-5" onClick={() => void doExport()}
                    disabled={exporting || duration < 1}>
              {exporting ? <Spinner className="w-4 h-4" /> : <IconFilm className="w-4 h-4" />}
              {exporting ? 'מייצא…' : 'ייצא מחדש'}
            </button>
            {!dirtyExport && !exporting && (
              <p className="hint mt-2 text-center">
                לא שינית כלום — ייצוא מחדש ייצור את אותו קובץ.
              </p>
            )}
          </div>
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
  return (
    <div>
      <label className="label">{label}</label>
      <div className="flex gap-1.5">
        <input type="number" step="0.1" min={0} className="field ltr-nums !px-2"
               value={value.toFixed(1)}
               onChange={(e) => onChange(Math.max(0, parseFloat(e.target.value) || 0))} />
        <button className="btn-ghost btn-sm whitespace-nowrap" onClick={onFromPlayhead}>
          מהנגן
        </button>
      </div>
      <div className="mt-1 text-[11px] text-ink-600 ltr-nums">{formatDuration(value)}</div>
    </div>
  )
}

// --------------------------------------------------------------------------
function SubtitleStyleEditor({ style, onChange }: {
  style: any
  onChange: (s: any) => void
}) {
  const set = (k: string, v: unknown) => onChange({ ...style, [k]: v })
  return (
    <div className="rounded-lg bg-ink-900 border border-ink-750 p-3 space-y-3">
      <div className="text-xs font-medium text-ink-300">עיצוב כתוביות</div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">גופן</label>
          <input className="field !py-1.5 text-xs" value={style.font}
                 onChange={(e) => set('font', e.target.value)} />
        </div>
        <div>
          <label className="label">גודל: <span className="ltr-nums">{style.size}</span></label>
          <input type="range" min={20} max={110} value={style.size} className="range mt-2.5"
                 onChange={(e) => set('size', parseInt(e.target.value, 10))} />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <ColorField label="טקסט" value={style.primary_color}
                    onChange={(v) => set('primary_color', v)} />
        <ColorField label="מתאר" value={style.outline_color}
                    onChange={(v) => set('outline_color', v)} />
        <ColorField label="הדגשה" value={style.highlight_color}
                    onChange={(v) => set('highlight_color', v)} />
      </div>

      <div>
        <label className="label">מיקום</label>
        <div className="grid grid-cols-3 gap-1.5">
          {([['top', 'למעלה'], ['middle', 'באמצע'], ['bottom', 'למטה']] as const)
            .map(([k, l]) => (
              <button key={k} onClick={() => set('position', k)}
                      className={`btn btn-sm ${style.position === k
                        ? 'bg-brand-600/20 text-brand-300 ring-1 ring-brand-500/40'
                        : 'bg-ink-800 text-ink-400 border border-ink-700'}`}>
                {l}
              </button>
            ))}
        </div>
      </div>

      <label className="flex items-center gap-2.5 cursor-pointer">
        <input type="checkbox" className="accent-brand-500 w-4 h-4"
               checked={style.word_level}
               onChange={(e) => set('word_level', e.target.checked)} />
        <span className="text-xs text-ink-300">הדגשת מילה פעילה</span>
      </label>
    </div>
  )
}

function ColorField({ label, value, onChange }: {
  label: string; value: string; onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <input type="color" value={value} onChange={(e) => onChange(e.target.value)}
             className="w-full h-8 rounded-md bg-ink-800 border border-ink-700 cursor-pointer" />
    </div>
  )
}

// --------------------------------------------------------------------------
function CameraPicker({ jobId, atSeconds, region, onChange, onSaveDefault }: {
  jobId: string
  atSeconds: number
  region: { x: number; y: number; w: number; h: number } | null
  onChange: (r: { x: number; y: number; w: number; h: number }) => void
  onSaveDefault: () => void
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<{ x0: number; y0: number } | null>(null)
  const [failed, setFailed] = useState(false)

  const rel = (e: React.MouseEvent) => {
    const r = boxRef.current!.getBoundingClientRect()
    return {
      x: clamp((e.clientX - r.left) / r.width, 0, 1),
      y: clamp((e.clientY - r.top) / r.height, 0, 1),
    }
  }

  const src = useMemo(
    () => api.jobFrameUrl(jobId, Math.max(0, atSeconds), 640),
    [jobId, atSeconds])

  return (
    <div className="mb-4">
      <label className="label">אזור מצלמת הסטרימר</label>
      <p className="hint mb-2">
        גרור מלבן סביב מצלמת הסטרימר בפריים. הבחירה נשמרת עם הקליפ,
        וניתן לשמור אותה כברירת מחדל לשידורים הבאים.
      </p>

      {failed ? (
        <div className="rounded-lg bg-ink-900 border border-ink-750 p-4 text-xs text-ink-500">
          לא ניתן לטעון פריים מהמקור (ייתכן שקובץ המקור נמחק).
          אפשר עדיין להזין ערכים ידנית למטה.
        </div>
      ) : (
        <div ref={boxRef}
             className="relative rounded-lg overflow-hidden border border-ink-700
                        cursor-crosshair select-none bg-ink-900"
             onMouseDown={(e) => { e.preventDefault(); const p = rel(e); setDrag({ x0: p.x, y0: p.y }) }}
             onMouseMove={(e) => {
               if (!drag) return
               const p = rel(e)
               onChange({
                 x: Math.min(drag.x0, p.x), y: Math.min(drag.y0, p.y),
                 w: Math.abs(p.x - drag.x0), h: Math.abs(p.y - drag.y0),
               })
             }}
             onMouseUp={() => setDrag(null)}
             onMouseLeave={() => setDrag(null)}>
          <img src={src} alt="פריים מהשידור" className="w-full block pointer-events-none"
               onError={() => setFailed(true)} draggable={false} />
          {region && region.w > 0.01 && (
            <div className="absolute border-2 border-brand-400 bg-brand-500/15 pointer-events-none"
                 style={{
                   left: `${region.x * 100}%`, top: `${region.y * 100}%`,
                   width: `${region.w * 100}%`, height: `${region.h * 100}%`,
                 }} />
          )}
        </div>
      )}

      <div className="mt-2 grid grid-cols-4 gap-1.5">
        {(['x', 'y', 'w', 'h'] as const).map((k) => (
          <div key={k}>
            <label className="text-[10px] text-ink-600 block mb-0.5 ltr-nums">{k}</label>
            <input type="number" step="0.01" min={0} max={1}
                   className="field !py-1 !px-1.5 text-[11px] ltr-nums"
                   value={(region?.[k] ?? 0).toFixed(2)}
                   onChange={(e) => onChange({
                     x: region?.x ?? 0, y: region?.y ?? 0,
                     w: region?.w ?? 0.25, h: region?.h ?? 0.25,
                     [k]: clamp(parseFloat(e.target.value) || 0, 0, 1),
                   })} />
          </div>
        ))}
      </div>

      <button className="btn-ghost btn-sm w-full mt-2" onClick={onSaveDefault}
              disabled={!region || region.w < 0.02}>
        שמור כברירת מחדל לשידורים הבאים
      </button>
    </div>
  )
}
