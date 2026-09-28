import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { PageHeader } from '../App'
import { LiveCapturePanel } from '../components/live'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import {
  formatBytes, formatDuration, formatEta, formatRelative,
  KIND_LABEL, STAGE_LABEL, STAGE_SEQUENCE, STATUS_LABEL, STATUS_TONE, scoreTone,
} from '../lib/format'
import { clipIsPlayable } from '../lib/types'
import type { Clip, Job, LiveStatus, TimelineData, WsEvent } from '../lib/types'
import {
  Chip, EmptyState, IconAlert, IconCheck, IconFilm, IconLive,
  IconRefresh, IconStop, ProgressBar, Spinner,
} from '../components/ui'

interface LogLine { ts: number; message: string; level: string }

export default function JobDetailPage() {
  const { jobId = '' } = useParams()
  const { subscribe, notifyError, pushToast, refreshJobs } = useStore()

  const [job, setJob] = useState<Job | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [timeline, setTimeline] = useState<TimelineData | null>(null)
  const [logs, setLogs] = useState<LogLine[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'progress' | 'clips' | 'transcript'>('progress')
  const [transcript, setTranscript] = useState<
    { idx: number; start: number; end: number; text: string }[] | null>(null)
  const [live, setLive] = useState<LiveStatus | null>(null)
  const [stopping, setStopping] = useState(false)

  const load = useCallback(async () => {
    try {
      const j = await api.getJob(jobId)
      setJob(j)
      const [c, tl] = await Promise.all([
        api.listClips(jobId).catch(() => [] as Clip[]),
        api.jobTimeline(jobId).catch(() => ({ available: false } as TimelineData)),
      ])
      setClips(c)
      setTimeline(tl)
      if (j.is_live_mode) {
        api.liveStatus(jobId).then(setLive).catch(() => undefined)
      }
    } catch (e) {
      notifyError(e, 'טעינת המשימה נכשלה')
    } finally {
      setLoading(false)
    }
  }, [jobId, notifyError])

  useEffect(() => { void load() }, [load])

  // עצירת הקלטה – אינה ביטול המשימה: החומר שנאסף ממשיך לעריכה
  const stopCapture = useCallback(async () => {
    setStopping(true)
    try {
      const next = await api.stopLive(jobId)
      setLive(next)
      pushToast({
        tone: 'info', title: 'העצירה התקבלה',
        body: next.note || 'המקטע הנוכחי נסגר, והעיבוד ימשיך.',
      })
    } catch (e) {
      notifyError(e, 'עצירת ההקלטה נכשלה')
    } finally {
      setStopping(false)
    }
  }, [jobId, pushToast, notifyError])

  // ---- אירועים חיים ----
  useEffect(() => subscribe((e: WsEvent) => {
    if (e.job_id !== jobId) return
    if (e.type === 'job.progress') {
      setJob((prev) => prev ? {
        ...prev,
        stage: e.data.stage ?? prev.stage,
        stage_progress: e.data.stage_progress ?? prev.stage_progress,
        overall_progress: e.data.overall_progress ?? prev.overall_progress,
        message: e.data.message ?? prev.message,
      } : prev)
    } else if (e.type === 'job.log') {
      setLogs((prev) => [...prev.slice(-120), {
        ts: e.ts, message: e.data.message, level: e.data.level || 'info',
      }])
    } else if (e.type === 'clip.created' || e.type === 'clip.ready' ||
               e.type === 'clip.updated') {
      api.listClips(jobId).then(setClips).catch(() => undefined)
    } else if (e.type === 'live.state' || e.type === 'live.segment' ||
               e.type === 'live.tick') {
      api.liveStatus(jobId).then(setLive).catch(() => undefined)
    } else if (e.type === 'job.status') {
      void load()
      if (['completed', 'failed', 'cancelled'].includes(e.data.status)) {
        api.jobTimeline(jobId).then(setTimeline).catch(() => undefined)
      }
    }
  }), [jobId, subscribe, load])

  const loadTranscript = useCallback(async () => {
    if (transcript) return
    try {
      const t = await api.jobTranscript(jobId)
      setTranscript(t.segments)
    } catch (e) { notifyError(e, 'טעינת התמלול נכשלה') }
  }, [jobId, transcript, notifyError])

  const doCancel = async () => {
    setBusy(true)
    try {
      await api.cancelJob(jobId)
      pushToast({ tone: 'info', title: 'בקשת ביטול נשלחה' })
      await load()
    } catch (e) { notifyError(e) } finally { setBusy(false) }
  }

  const doRetry = async (fromStart: boolean) => {
    setBusy(true)
    try {
      await api.retryJob(jobId, fromStart)
      setLogs([])
      await load()
      await refreshJobs()
    } catch (e) { notifyError(e) } finally { setBusy(false) }
  }

  if (loading) {
    return <div className="p-4 sm:p-8 max-w-6xl mx-auto space-y-4">
      <div className="skeleton h-10 w-72" />
      <div className="skeleton h-40" />
    </div>
  }
  if (!job) {
    return <div className="p-4 sm:p-8 max-w-6xl mx-auto">
      <EmptyState title="המשימה לא נמצאה"
                  body="ייתכן שהיא נמחקה."
                  action={<Link to="/jobs" className="btn-primary">חזרה למשימות</Link>} />
    </div>
  }

  const active = job.status === 'running' || job.status === 'queued'
  const plannedStages = STAGE_SEQUENCE.filter((s) => {
    if (s === 'download' && !job.input_url) return false
    if (s === 'transcribe' && job.completed_stages.length > 0 &&
        !job.completed_stages.includes('transcribe') &&
        job.status === 'completed') return false
    return true
  })

  return (
    <div className="p-4 sm:p-8 max-w-6xl mx-auto">
      <PageHeader
        title={job.title || 'משימה'}
        subtitle={job.input_url || (job.source?.title ?? 'קובץ מקומי')}
        actions={
          <>
            {active && (
              <button className="btn-ghost btn-sm" onClick={() => void doCancel()} disabled={busy}>
                {busy ? <Spinner className="w-3.5 h-3.5" /> : <IconStop className="w-3.5 h-3.5" />}
                עצור
              </button>
            )}
            {(job.status === 'failed' || job.status === 'cancelled') && (
              <>
                <button className="btn-ghost btn-sm" onClick={() => void doRetry(false)} disabled={busy}>
                  <IconRefresh className="w-3.5 h-3.5" />המשך מהשלב האחרון
                </button>
                <button className="btn-ghost btn-sm" onClick={() => void doRetry(true)} disabled={busy}>
                  מהתחלה
                </button>
              </>
            )}
            <Link to="/jobs" className="btn-ghost btn-sm">כל המשימות</Link>
          </>
        }
      />

      {/* ---- לוח שידור חי ---- */}
      {live?.is_live_mode && live.state !== 'idle' && (
        <div className="mb-5">
          <LiveCapturePanel status={live} onStop={() => void stopCapture()}
                            stopping={stopping} />
        </div>
      )}

      {/* ---- סרגל מצב ---- */}
      <div className="card-pad mb-5">
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <span className={`chip ${STATUS_TONE[job.status]}`}>{STATUS_LABEL[job.status]}</span>
          {job.is_live_mode && (
            <Chip tone="warn"><IconLive className="w-3 h-3" />
              שידור חי{live?.segments ? ` · ${live.segments} מקטעים` : ''}
            </Chip>
          )}
          {job.source?.duration ? (
            <Chip>מקור: <span className="ltr-nums">{formatDuration(job.source.duration)}</span></Chip>
          ) : null}
          {job.source?.width ? (
            <Chip>
              <span className="ltr-nums">{job.source.width}×{job.source.height}</span>
            </Chip>
          ) : null}
          {job.source?.file_size ? (
            <Chip>{formatBytes(job.source.file_size)}</Chip>
          ) : null}
          {job.source?.has_audio === false && <Chip tone="warn">ללא אודיו</Chip>}
          <span className="text-xs text-ink-500 mr-auto">
            נוצרה {formatRelative(job.created_at)}
          </span>
        </div>

        {active && (
          <>
            <div className="flex items-center justify-between text-xs mb-1.5">
              <span className="text-ink-300">{job.message || STAGE_LABEL[job.stage]}</span>
              <span className="text-ink-400 ltr-nums">
                {Math.round(job.overall_progress * 100)}%
                {job.eta_seconds ? ` · נותרו ${formatEta(job.eta_seconds)}` : ''}
              </span>
            </div>
            <ProgressBar value={job.overall_progress} striped />
            {job.eta_basis && (
              <p className="hint mt-1.5">{job.eta_basis}</p>
            )}
            {!job.eta_seconds && (
              <p className="hint mt-1.5">
                אין עדיין מספיק מדידות כדי להעריך זמן סיום. ההערכה תופיע אחרי
                שיושלמו כמה משימות.
              </p>
            )}
          </>
        )}

        {job.status === 'failed' && job.error && (
          <div className="rounded-lg bg-bad/10 border border-bad/25 p-3.5 flex items-start gap-2.5">
            <IconAlert className="w-4 h-4 text-bad shrink-0 mt-px" />
            <div>
              <p className="text-sm text-bad">{job.error}</p>
              {job.error_code && (
                <p className="text-[11px] text-bad/70 mt-1 ltr-nums">קוד: {job.error_code}</p>
              )}
            </div>
          </div>
        )}

        {job.notes.length > 0 && (
          <div className="mt-4 space-y-2">
            {job.notes.map((n, i) => (
              <div key={i} className="flex items-start gap-2 rounded-lg bg-ink-900
                                      border border-ink-750 p-3">
                <IconAlert className="w-3.5 h-3.5 text-brand-400 shrink-0 mt-0.5" />
                <p className="text-xs text-ink-300 leading-relaxed">{n}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ---- לשוניות ---- */}
      <div className="flex gap-1 mb-4 border-b border-ink-800">
        {([
          ['progress', 'שלבי עיבוד'],
          ['clips', `קליפים${clips.length ? ` (${clips.length})` : ''}`],
          ['transcript', 'תמלול'],
        ] as const).map(([key, label]) => (
          <button key={key}
                  onClick={() => { setTab(key); if (key === 'transcript') void loadTranscript() }}
                  className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
                    ${tab === key
                      ? 'border-brand-500 text-white'
                      : 'border-transparent text-ink-400 hover:text-ink-200'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'progress' && (
        <div className="grid lg:grid-cols-5 gap-5">
          <div className="lg:col-span-2 card-pad">
            <h3 className="section-title mb-4">שלבים</h3>
            <StageList job={job} stages={plannedStages} />
            {job.timings.length > 0 && (
              <div className="mt-5 pt-4 border-t border-ink-800">
                <h4 className="text-xs font-medium text-ink-400 mb-2">זמני עיבוד בפועל</h4>
                <div className="space-y-1">
                  {job.timings.map((t, i) => (
                    <div key={i} className="flex justify-between text-[11px]">
                      <span className="text-ink-500">{STAGE_LABEL[t.stage] ?? t.stage}</span>
                      <span className="text-ink-400 ltr-nums">{t.seconds.toFixed(1)}s</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="lg:col-span-3 space-y-5">
            <div className="card-pad">
              <h3 className="section-title mb-1">מפת עניין בשידור</h3>
              <p className="hint mb-4">
                הציון המשולב לאורך זמן. הפסים המסומנים הם הקטעים שנחתכו.
              </p>
              <TimelineChart data={timeline} clips={clips}
                             duration={job.source?.duration ?? timeline?.duration ?? 0} />
            </div>

            <div className="card-pad">
              <h3 className="section-title mb-3">יומן עיבוד</h3>
              <LogView lines={logs} active={active} />
            </div>
          </div>
        </div>
      )}

      {tab === 'clips' && (
        clips.length === 0 ? (
          <EmptyState icon={<IconFilm className="w-10 h-10" />}
                      title="עדיין אין קליפים"
                      body={active ? 'הקליפים יופיעו כאן ברגע שהייצוא יתחיל.'
                        : 'לא נוצרו קליפים במשימה הזו.'} />
        ) : (
          <div className="space-y-2">
            {clips.map((c) => (
              <div key={c.id} className="card p-4 flex items-center gap-4">
                <div className="w-28 aspect-video rounded-md bg-ink-900 overflow-hidden shrink-0">
                  {c.has_thumbnail
                    ? <img src={api.clipThumbUrl(c.id)} alt=""
                           className="w-full h-full object-cover" />
                    : <div className="w-full h-full grid place-items-center text-ink-600">
                        <IconFilm className="w-5 h-5" />
                      </div>}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Chip tone={c.kind === 'short' ? 'brand' : 'default'}>
                      {KIND_LABEL[c.kind]}
                    </Chip>
                    <span className={`text-xs font-medium ltr-nums ${scoreTone(c.score)}`}>
                      {Math.round(c.score * 100)}
                    </span>
                    {c.status !== 'ready' && (
                      <Chip tone={c.status === 'failed' ? 'bad' : 'warn'}>
                        {c.status === 'rendering' ? 'מייצא…' :
                         c.status === 'failed' ? 'נכשל' :
                         c.status === 'needs_review' ? 'דורש בדיקה' : 'ממתין'}
                      </Chip>
                    )}
                  </div>
                  <div className="mt-1 text-sm text-white truncate">{c.title}</div>
                  <div className="text-[11px] text-ink-500 ltr-nums mt-0.5">
                    {formatDuration(c.source_start)} → {formatDuration(c.source_end)}
                    {' · '}{formatDuration(c.duration)}
                    {c.width ? ` · ${c.width}×${c.height}` : ''}
                  </div>
                  {c.error && <div className="text-[11px] text-bad mt-1">{c.error}</div>}
                </div>
                {clipIsPlayable(c.status) && (
                  <Link to={`/clips/${c.id}/edit`} className="btn-ghost btn-sm shrink-0">
                    פתח
                  </Link>
                )}
              </div>
            ))}
          </div>
        )
      )}

      {tab === 'transcript' && (
        transcript === null ? (
          <div className="card-pad flex items-center gap-2 text-sm text-ink-400">
            <Spinner />טוען תמלול…
          </div>
        ) : transcript.length === 0 ? (
          <EmptyState title="אין תמלול למשימה הזו"
                      body="התמלול הושבת בהגדרות, או שהמודל לא היה זמין בזמן העיבוד." />
        ) : (
          <div className="card divide-y divide-ink-800 max-h-[60vh] overflow-y-auto">
            {transcript.map((s) => (
              <div key={s.idx} className="flex gap-3 px-4 py-2.5 hover:bg-ink-800/40">
                <span className="text-[11px] text-ink-600 ltr-nums shrink-0 w-16 pt-0.5">
                  {formatDuration(s.start)}
                </span>
                <span className="text-sm text-ink-200 leading-relaxed">{s.text}</span>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
function StageList({ job, stages }: { job: Job; stages: readonly string[] }) {
  return (
    <ol className="space-y-1">
      {stages.map((stage) => {
        const done = job.completed_stages.includes(stage)
        const current = job.stage === stage && !done
        return (
          <li key={stage} className={`flex items-center gap-3 px-3 py-2 rounded-lg
            ${current ? 'bg-brand-600/10 ring-1 ring-brand-500/25' : ''}`}>
            <span className={`w-5 h-5 rounded-full grid place-items-center shrink-0 text-[10px]
              ${done ? 'bg-ok/20 text-ok'
                : current ? 'bg-brand-500/20 text-brand-300'
                : 'bg-ink-800 text-ink-600'}`}>
              {done ? <IconCheck className="w-3 h-3" />
                : current ? <Spinner className="w-3 h-3" /> : '•'}
            </span>
            <span className={`text-sm flex-1 ${done ? 'text-ink-400'
              : current ? 'text-white font-medium' : 'text-ink-600'}`}>
              {STAGE_LABEL[stage] ?? stage}
            </span>
            {current && (
              <span className="text-[11px] text-brand-300 ltr-nums">
                {Math.round(job.stage_progress * 100)}%
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

// --------------------------------------------------------------------------
function TimelineChart({ data, clips, duration }: {
  data: TimelineData | null
  clips: Clip[]
  duration: number
}) {
  const [hover, setHover] = useState<{ x: number; t: number; v: number } | null>(null)
  const W = 620
  const H = 130

  const path = useMemo(() => {
    if (!data?.available || !data.score?.length) return ''
    const pts = data.score
    const step = W / Math.max(1, pts.length - 1)
    return pts.map((v, i) =>
      `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(2)},${(H - v * (H - 10) - 5).toFixed(2)}`
    ).join(' ')
  }, [data])

  if (!data?.available) {
    return (
      <div className="h-32 rounded-lg bg-ink-900 border border-ink-800 grid place-items-center">
        <p className="text-xs text-ink-500">{data?.reason ?? 'הניתוח טרם הושלם.'}</p>
      </div>
    )
  }

  const total = data.duration || duration || 1

  return (
    <div>
      {/* ציר זמן של וידאו נשאר משמאל לימין גם בממשק RTL,
          כמו בכל נגן וידאו — 0:00 בשמאל, סוף השידור בימין. */}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-32" preserveAspectRatio="none"
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => {
             const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
             const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
             const idx = Math.round(x * ((data.score?.length ?? 1) - 1))
             setHover({ x, t: x * total, v: data.score?.[idx] ?? 0 })
           }}>
        <defs>
          <linearGradient id="scoreFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4b7bff" stopOpacity="0.42" />
            <stop offset="100%" stopColor="#4b7bff" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* קווי רשת */}
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" x2={W} y1={H - g * (H - 10) - 5} y2={H - g * (H - 10) - 5}
                stroke="#272c3a" strokeWidth="1" strokeDasharray="3 4" />
        ))}

        {/* טווחי הקליפים שנחתכו */}
        {clips.map((c) => {
          const x0 = (c.source_start / total) * W
          const w = Math.max(2, ((c.source_end - c.source_start) / total) * W)
          return (
            <rect key={c.id} x={x0} y={4} width={w} height={H - 8}
                  fill={c.kind === 'short' ? '#4b7bff' : '#34d399'} opacity="0.13" />
          )
        })}

        {path && <>
          <path d={`${path} L${W},${H} L0,${H} Z`} fill="url(#scoreFill)" />
          <path d={path} fill="none" stroke="#6b96ff" strokeWidth="1.6"
                vectorEffect="non-scaling-stroke" />
        </>}

        {hover && (
          <line x1={hover.x * W} x2={hover.x * W} y1="0" y2={H}
                stroke="#8a93a6" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
      </svg>

      <div className="flex items-center justify-between mt-2 text-[11px] text-ink-600"
           dir="ltr">
        <span className="ltr-nums">0:00</span>
        {hover && (
          <span className="text-ink-300" dir="rtl">
            <span className="ltr-nums">{formatDuration(hover.t)}</span>
            {' · ציון '}
            <span className="ltr-nums">{Math.round(hover.v * 100)}</span>
          </span>
        )}
        <span className="ltr-nums">{formatDuration(total)}</span>
      </div>

      <div className="mt-3 flex items-center gap-4 text-[11px] text-ink-500">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-2 rounded-sm bg-brand-500/40" />שורטים
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-2 rounded-sm bg-ok/40" />קליפים ארוכים
        </span>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
function LogView({ lines, active }: { lines: LogLine[]; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines])

  if (!lines.length) {
    return (
      <p className="text-xs text-ink-500">
        {active ? 'ממתין להודעות מהעיבוד…'
          : 'אין הודעות. היומן מוצג בזמן אמת בזמן שהמשימה רצה.'}
      </p>
    )
  }

  const tone: Record<string, string> = {
    info: 'text-ink-400', warn: 'text-warn', error: 'text-bad',
  }
  return (
    <div ref={ref} className="max-h-56 overflow-y-auto space-y-1 font-mono text-[11px]"
         dir="rtl">
      {lines.map((l, i) => (
        <div key={i} className="flex gap-2">
          <span className="text-ink-700 ltr-nums shrink-0">
            {new Date(l.ts * 1000).toLocaleTimeString('he-IL')}
          </span>
          <span className={tone[l.level] ?? 'text-ink-400'}>{l.message}</span>
        </div>
      ))}
    </div>
  )
}
