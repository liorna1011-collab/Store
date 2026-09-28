/**
 * ייבוא / פרויקט חדש.
 *
 * שלושה מקורות, מופרדים בבירור: העלאת קובץ, קישור לסרטון, וקישור
 * לשידור חי. מסלול השידור החי מזהה את הזרם לפני ההקלטה ומציג את מה
 * שנמדד בפועל, כדי שהמשתמש ידע מה הוא עומד להקליט.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageHeader } from '../App'
import { api, PolixorApiError } from '../lib/api'
import { useStore } from '../lib/store'
import { formatBytes, formatDuration, PLATFORM_LABEL } from '../lib/format'
import type {
  AppSettings, LiveDetectResult, ProbeResult, ResolveResult,
} from '../lib/types'
import {
  Chip, IconAlert, IconLink, IconLive, IconPlay, IconUpload, ProgressBar, Spinner,
} from '../components/ui'
import { LiveDot, StreamDetectPanel } from '../components/live'

const ACCEPT = '.mp4,.mkv,.mov,.webm,.m4v,.avi,.ts'

type SourceTab = 'upload' | 'url' | 'live'

const TABS: { id: SourceTab; label: string; hint: string }[] = [
  { id: 'upload', label: 'העלאת קובץ', hint: 'וידאו מהמחשב שלך' },
  { id: 'url', label: 'קישור לסרטון', hint: 'YouTube, Twitch, Kick, Drive' },
  { id: 'live', label: 'שידור חי', hint: 'הקלטה מזרם פעיל' },
]

export default function HomePage() {
  const navigate = useNavigate()
  const { pushToast, notifyError, refreshJobs } = useStore()

  const [tab, setTab] = useState<SourceTab>('url')
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [starting, setStarting] = useState(false)

  // --- קישור לסרטון ---
  const [url, setUrl] = useState('')
  const [resolved, setResolved] = useState<ResolveResult | null>(null)
  const [probeData, setProbeData] = useState<ProbeResult | null>(null)
  const [probing, setProbing] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)

  // --- שידור חי ---
  const [liveUrl, setLiveUrl] = useState('')
  const [detecting, setDetecting] = useState(false)
  const [detected, setDetected] = useState<LiveDetectResult | null>(null)
  const [detectError, setDetectError] = useState<string | null>(null)

  // --- העלאה ---
  const [upload, setUpload] = useState<{
    token: string; title: string; duration: number; size: number
  } | null>(null)
  const [uploadPct, setUploadPct] = useState<number | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.getSettings().then((r) => setSettings(r.values)).catch(() => undefined)
  }, [])

  // ---- זיהוי פלטפורמה תוך כדי הקלדה (מקומי, ללא רשת) ----
  useEffect(() => {
    const trimmed = url.trim()
    setProbeData(null)
    setProbeError(null)
    if (!trimmed) { setResolved(null); return }
    let cancelled = false
    const t = setTimeout(() => {
      api.resolve(trimmed)
        .then((r) => { if (!cancelled) setResolved(r) })
        .catch(() => { if (!cancelled) setResolved(null) })
    }, 350)
    return () => { cancelled = true; clearTimeout(t) }
  }, [url])

  // אם הודבק קישור לשידור חי בלשונית הסרטון – מציעים לעבור
  const urlIsLive = Boolean(resolved?.is_live)

  const runProbe = useCallback(async () => {
    const trimmed = url.trim()
    if (!trimmed) return
    setProbing(true)
    setProbeError(null)
    try {
      setProbeData(await api.probe(trimmed))
    } catch (e) {
      setProbeData(null)
      setProbeError(e instanceof PolixorApiError
        ? `${e.message}${e.hint ? ` ${e.hint}` : ''}`
        : 'בדיקת הקישור נכשלה.')
    } finally {
      setProbing(false)
    }
  }, [url])

  const detectStream = useCallback(async () => {
    const trimmed = liveUrl.trim()
    if (!trimmed) return
    setDetecting(true)
    setDetectError(null)
    setDetected(null)
    try {
      setDetected(await api.detectLive(trimmed))
    } catch (e) {
      setDetectError(e instanceof PolixorApiError
        ? `${e.message}${e.hint ? ` ${e.hint}` : ''}`
        : 'זיהוי השידור נכשל.')
    } finally {
      setDetecting(false)
    }
  }, [liveUrl])

  const handleFile = useCallback(async (file: File) => {
    setUploadPct(0)
    try {
      const res = await api.upload(file, (f) => setUploadPct(f))
      setUpload({
        token: res.upload_token, title: res.title,
        duration: res.duration, size: res.file_size,
      })
      pushToast({
        tone: 'success', title: 'הקובץ הועלה',
        body: `${res.title} · ${formatDuration(res.duration)}`,
      })
    } catch (e) {
      notifyError(e, 'העלאת הקובץ נכשלה')
      setUpload(null)
    } finally {
      setUploadPct(null)
    }
  }, [pushToast, notifyError])

  const start = useCallback(async () => {
    setStarting(true)
    try {
      const job = await api.createJob(
        tab === 'upload'
          ? { upload_token: upload?.token ?? '', title: upload?.title ?? '' }
          : tab === 'live'
            ? { url: liveUrl.trim(), live_mode: true,
                title: detected?.title ?? '' }
            : { url: url.trim(), live_mode: false,
                title: probeData?.title ?? '' })
      await refreshJobs()
      pushToast({
        tone: 'success',
        title: tab === 'live' ? 'ההקלטה מתחילה' : 'המשימה נוצרה',
        body: tab === 'live'
          ? 'הקליטה רצה ברקע. עצור אותה כשתרצה שהעריכה תתחיל.'
          : 'העיבוד החל ברקע.',
      })
      navigate(`/jobs/${job.id}`)
    } catch (e) {
      notifyError(e, 'יצירת המשימה נכשלה')
    } finally {
      setStarting(false)
    }
  }, [tab, url, liveUrl, upload, probeData, detected, navigate, pushToast,
    notifyError, refreshJobs])

  const canStart =
    tab === 'upload' ? Boolean(upload)
      : tab === 'live' ? Boolean(detected?.available)
        : Boolean(url.trim() && resolved)

  return (
    <div className="p-4 sm:p-8 max-w-5xl mx-auto">
      <PageHeader
        title="פרויקט חדש"
        subtitle="בחר מקור — קובץ, קישור לסרטון או שידור חי — ו-Polixor ימצא את הרגעים המעניינים ויחתוך אותם."
      />

      {/* ---- בורר מקור ---- */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-5">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`rounded-xl border px-4 py-3 text-right transition-colors
                        ${tab === t.id
                          ? 'border-brand-500 bg-brand-600/12 ring-1 ring-brand-500/25'
                          : 'border-ink-750 bg-ink-850 hover:border-ink-600'}`}
          >
            <div className="flex items-center gap-2">
              {t.id === 'upload' && <IconUpload className={`w-4 h-4 ${
                tab === t.id ? 'text-brand-300' : 'text-ink-500'}`} />}
              {t.id === 'url' && <IconLink className={`w-4 h-4 ${
                tab === t.id ? 'text-brand-300' : 'text-ink-500'}`} />}
              {t.id === 'live' && (tab === t.id
                ? <LiveDot />
                : <IconLive className="w-4 h-4 text-ink-500" />)}
              <span className={`text-sm font-medium ${
                tab === t.id ? 'text-brand-200' : 'text-ink-300'}`}>
                {t.label}
              </span>
            </div>
            <div className="mt-1 text-[11px] text-ink-500">{t.hint}</div>
          </button>
        ))}
      </div>

      {/* ---- העלאת קובץ ---- */}
      {tab === 'upload' && (
        <section
          className={`card border-dashed p-8 text-center transition-colors
                      ${dragging ? 'border-brand-500 bg-brand-600/5' : 'border-ink-700'}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            const f = e.dataTransfer.files?.[0]
            if (f) void handleFile(f)
          }}
        >
          <input ref={fileRef} type="file" accept={ACCEPT} className="hidden"
                 onChange={(e) => {
                   const f = e.target.files?.[0]
                   if (f) void handleFile(f)
                   e.target.value = ''
                 }} />

          {uploadPct !== null ? (
            <div className="max-w-sm mx-auto">
              <div className="text-sm text-white mb-3">מעלה קובץ…</div>
              <ProgressBar value={uploadPct} />
              <div className="mt-2 text-xs text-ink-400 ltr-nums">
                {Math.round(uploadPct * 100)}%
              </div>
            </div>
          ) : upload ? (
            <div>
              <div className="text-sm font-medium text-white">{upload.title}</div>
              <div className="mt-1 text-xs text-ink-400 ltr-nums">
                {formatDuration(upload.duration)} · {formatBytes(upload.size)}
              </div>
              <button className="btn-ghost btn-sm mt-4"
                      onClick={() => { setUpload(null); fileRef.current?.click() }}>
                החלף קובץ
              </button>
            </div>
          ) : (
            <>
              <IconUpload className="w-8 h-8 mx-auto text-ink-600" />
              <div className="mt-3 text-sm text-ink-300">
                גרור לכאן קובץ וידאו, או{' '}
                <button className="text-brand-400 hover:text-brand-300 underline underline-offset-2"
                        onClick={() => fileRef.current?.click()}>
                  בחר מהמחשב
                </button>
              </div>
              <p className="hint mt-2">MP4, MKV, MOV, WEBM, AVI, TS</p>
            </>
          )}
        </section>
      )}

      {/* ---- קישור לסרטון ---- */}
      {tab === 'url' && (
        <section className="card-pad">
          <label className="label" htmlFor="stream-url">קישור לסרטון</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <IconLink className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2
                                   text-ink-500 pointer-events-none" />
              <input
                id="stream-url"
                className="field pr-9"
                dir="ltr"
                placeholder="https://www.youtube.com/watch?v=..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && canStart) void start() }}
              />
            </div>
            <button className="btn-ghost whitespace-nowrap"
                    onClick={() => void runProbe()}
                    disabled={!url.trim() || probing}>
              {probing ? <Spinner /> : null}
              {probing ? 'בודק…' : 'בדוק קישור'}
            </button>
          </div>

          <p className="hint mt-2">
            נתמכים: YouTube, Twitch, Kick, Google Drive (קובץ ששותף להורדה) וקישור ישיר
            לקובץ וידאו. Polixor לא עוקף DRM, הגבלות גישה או הרשאות.
          </p>

          {resolved && (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Chip tone="brand">{PLATFORM_LABEL[resolved.kind] ?? resolved.platform}</Chip>
              {urlIsLive && <Chip tone="warn">זוהה שידור חי</Chip>}
            </div>
          )}

          {urlIsLive && (
            <div className="mt-3 flex items-start gap-2.5 rounded-lg bg-brand-600/10
                            border border-brand-500/25 p-3">
              <IconLive className="w-4 h-4 text-brand-300 shrink-0 mt-px" />
              <div className="text-xs text-brand-200 leading-relaxed flex-1">
                הקישור מצביע על שידור חי. כאן יעובד רק מה שכבר שודר וזמין להורדה.
                כדי להקליט את השידור תוך כדי — עבור ללשונית <b>שידור חי</b>.
                <button className="block mt-2 btn-ghost btn-sm"
                        onClick={() => { setLiveUrl(url); setTab('live') }}>
                  העבר לשידור חי
                </button>
              </div>
            </div>
          )}

          {resolved?.notes?.map((n, i) => (
            <div key={i} className="mt-3 flex items-start gap-2 rounded-lg bg-warn/10
                                    border border-warn/25 p-3">
              <IconAlert className="w-4 h-4 text-warn shrink-0 mt-px" />
              <p className="text-xs text-warn leading-relaxed">{n}</p>
            </div>
          ))}

          {probeError && (
            <div className="mt-3 flex items-start gap-2 rounded-lg bg-bad/10
                            border border-bad/25 p-3">
              <IconAlert className="w-4 h-4 text-bad shrink-0 mt-px" />
              <p className="text-xs text-bad leading-relaxed">{probeError}</p>
            </div>
          )}

          {probeData && (
            <div className="mt-4 flex gap-4 rounded-lg bg-ink-900 border border-ink-750 p-4
                            animate-fade-up">
              {probeData.thumbnail && (
                <img src={probeData.thumbnail} alt=""
                     className="w-40 aspect-video object-cover rounded-md bg-ink-800 shrink-0" />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-white truncate">{probeData.title}</div>
                {probeData.uploader && (
                  <div className="text-xs text-ink-400 mt-0.5">{probeData.uploader}</div>
                )}
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-ink-400">
                  {probeData.duration > 0 && (
                    <span className="ltr-nums">{formatDuration(probeData.duration)}</span>
                  )}
                  {probeData.filesize_approx > 0 && (
                    <span>· {formatBytes(probeData.filesize_approx)}</span>
                  )}
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      {/* ---- שידור חי ---- */}
      {tab === 'live' && (
        <section className="card-pad">
          <label className="label" htmlFor="live-url">קישור לשידור חי</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <IconLive className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2
                                   text-ink-500 pointer-events-none" />
              <input
                id="live-url"
                className="field pr-9"
                dir="ltr"
                placeholder="https://www.twitch.tv/channel"
                value={liveUrl}
                onChange={(e) => { setLiveUrl(e.target.value); setDetected(null) }}
                onKeyDown={(e) => { if (e.key === 'Enter') void detectStream() }}
              />
            </div>
            <button className="btn-ghost whitespace-nowrap"
                    onClick={() => void detectStream()}
                    disabled={!liveUrl.trim() || detecting}>
              {detecting ? <Spinner /> : null}
              {detecting ? 'מזהה שידור…' : 'זהה שידור'}
            </button>
          </div>

          <p className="hint mt-2">
            YouTube Live, Twitch, Kick או כתובת זרם נתמכת אחרת. Polixor מקליט רק
            שידורים שניתן לגשת אליהם באופן חוקי, ואינו עוקף הגבלות גישה.
          </p>

          {detectError && (
            <div className="mt-3 flex items-start gap-2 rounded-lg bg-bad/10
                            border border-bad/25 p-3">
              <IconAlert className="w-4 h-4 text-bad shrink-0 mt-px" />
              <p className="text-xs text-bad leading-relaxed">{detectError}</p>
            </div>
          )}

          {detected && (
            <div className="mt-4">
              <StreamDetectPanel info={detected} />
              {!detected.available && (
                <p className="hint mt-3">
                  לא ניתן להתחיל הקלטה כל עוד השידור אינו זמין. אפשר לנסות לזהות שוב
                  אחרי שהשידור יעלה לאוויר.
                </p>
              )}
            </div>
          )}

          <div className="mt-4 rounded-lg bg-ink-900 border border-ink-750 p-3.5">
            <div className="text-xs text-ink-300 font-medium mb-1.5">איך זה עובד</div>
            <ol className="text-[11px] text-ink-500 leading-relaxed space-y-1
                           list-decimal list-inside">
              <li>ההקלטה רצה ברקע ונשמרת במקטעים. נפילה של הזרם לא מוחקת את מה שכבר נקלט.</li>
              <li>אם החיבור נופל, Polixor מתחבר מחדש אוטומטית וממשיך.</li>
              <li>כשתלחץ <b>עצור הקלטה</b>, החומר הופך למקור של הפרויקט.</li>
              <li>משם זה עובר בדיוק את אותו מסלול של קובץ רגיל: תמלול, ניתוח, עריכה וכתוביות.</li>
            </ol>
          </div>
        </section>
      )}

      {/* ---- סיכום הגדרות + הפעלה ---- */}
      {settings && (
        <section className="card-pad mt-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="section-title">מה ייווצר</h2>
            <button className="text-xs text-brand-400 hover:text-brand-300"
                    onClick={() => navigate('/settings')}>
              שנה בהגדרות
            </button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <SummaryTile
              label="קליפים ארוכים"
              value={settings.long_enabled
                ? <><span className="ltr-nums">{settings.long_count}</span>{' × '}
                    <span className="ltr-nums">
                      {rangeLabel(settings.long_min_seconds, settings.long_max_seconds)}
                    </span></>
                : 'מושבת'}
              sub={settings.long_enabled
                ? (settings.long_mode === 'highlights' ? 'מיטב הרגעים' : 'קטע רציף')
                : undefined}
            />
            <SummaryTile
              label="שורטים"
              value={settings.short_enabled
                ? <><span className="ltr-nums">{settings.short_count}</span>{' × '}
                    <span className="ltr-nums">
                      {rangeLabel(settings.short_min_seconds, settings.short_max_seconds)}
                    </span></>
                : 'מושבת'}
              sub={settings.short_enabled ? LAYOUT_LABEL[settings.short_layout] : undefined}
            />
            <SummaryTile
              label="כתוביות"
              value={settings.subtitles_enabled ? 'מופעלות' : 'מושבתות'}
              sub={settings.subtitles_enabled
                ? (settings.subtitle_word_level ? 'ברמת מילה' : 'ברמת משפט')
                : undefined}
            />
            <SummaryTile
              label="מנוע AI"
              value={AI_LABEL[settings.ai_mode] ?? settings.ai_mode}
              sub={settings.ai_mode === 'cloud' ? settings.ai_provider : undefined}
            />
          </div>
        </section>
      )}

      <div className="mt-6 flex items-center justify-end gap-3">
        {!canStart && (
          <span className="text-xs text-ink-500">
            {tab === 'upload' ? 'העלה קובץ כדי להתחיל'
              : tab === 'live' ? 'זהה שידור זמין כדי להתחיל הקלטה'
                : 'הדבק קישור כדי להתחיל'}
          </span>
        )}
        <button className="btn-primary px-6 py-2.5" onClick={() => void start()}
                disabled={!canStart || starting}>
          {starting ? <Spinner />
            : tab === 'live' ? <IconLive className="w-4 h-4" />
              : <IconPlay className="w-4 h-4" />}
          {tab === 'live' ? 'התחל הקלטה' : 'התחל ניתוח'}
        </button>
      </div>
    </div>
  )
}

const LAYOUT_LABEL: Record<string, string> = {
  center: 'חיתוך מרכזי',
  auto_face: 'מעקב אחרי פנים',
  split: 'מסך מפוצל',
  blur_pad: 'מסגרת מטושטשת',
}

const AI_LABEL: Record<string, string> = {
  heuristic: 'מקומי (היוריסטי)',
  ollama: 'Ollama מקומי',
  cloud: 'מודל בענן',
}

/** טווח אורך קריא: שניות מתחת לדקה, אחרת דקות. */
function rangeLabel(minSeconds: number, maxSeconds: number): string {
  if (maxSeconds < 60) return `${minSeconds}–${maxSeconds} ש׳`
  const fmt = (s: number) => (s % 60 === 0 ? String(s / 60) : (s / 60).toFixed(1))
  return `${fmt(minSeconds)}–${fmt(maxSeconds)} דק׳`
}

function SummaryTile({ label, value, sub }: {
  label: string; value: React.ReactNode; sub?: string
}) {
  return (
    <div className="rounded-lg bg-ink-900 border border-ink-750 p-3">
      <div className="text-ink-500">{label}</div>
      <div className="mt-1 text-sm text-white font-medium">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-ink-500">{sub}</div>}
    </div>
  )
}
