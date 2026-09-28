/**
 * רכיבי שידור חי: זיהוי זרם לפני ההקלטה, ולוח מצב בזמן הקלטה.
 *
 * הרכיבים מציגים רק מה שנמדד בפועל. כשנתון לא אומת (למשל אודיו
 * שנקרא מהמטא-דאטה ולא מהזרם), נאמר זאת במפורש.
 */

import { useEffect, useState } from 'react'
import { formatDuration } from '../lib/format'
import type { LiveDetectResult, LiveState, LiveStatus } from '../lib/types'
import {
  Chip, IconAlert, IconCheck, IconLive, IconStop, IconX, Spinner,
} from './ui'

export const LIVE_STATE_TONE: Record<LiveState, string> = {
  idle: 'text-ink-400',
  detecting: 'text-brand-300',
  connecting: 'text-brand-300',
  live: 'text-bad',
  reconnecting: 'text-warn',
  stopping: 'text-ink-300',
  completed: 'text-ok',
  failed: 'text-bad',
}

/** נקודת LIVE אדומה פועמת – אותה שפה של לוח שידור. */
export function LiveDot({ tone = 'bg-bad' }: { tone?: string }) {
  return (
    <span className="relative flex w-2.5 h-2.5">
      <span className={`live-dot absolute inline-flex w-full h-full rounded-full ${tone}`} />
      <span className={`relative inline-flex w-2.5 h-2.5 rounded-full ${tone}`} />
    </span>
  )
}

export function LiveStateBadge({ state, label }: { state: LiveState; label: string }) {
  const live = state === 'live'
  const reconnecting = state === 'reconnecting'
  const working = state === 'detecting' || state === 'connecting' || state === 'stopping'

  return (
    <span className={`inline-flex items-center gap-2 rounded-md px-2.5 py-1
                      text-[11px] font-semibold tracking-wide
                      ${live ? 'bg-bad/15 ring-1 ring-bad/30 text-bad'
                        : reconnecting ? 'bg-warn/15 ring-1 ring-warn/30 text-warn'
                        : state === 'completed' ? 'bg-ok/15 ring-1 ring-ok/30 text-ok'
                        : state === 'failed' ? 'bg-bad/10 ring-1 ring-bad/25 text-bad'
                        : 'bg-ink-800 ring-1 ring-ink-700 text-ink-300'}`}>
      {live && <LiveDot />}
      {reconnecting && <LiveDot tone="bg-warn" />}
      {working && <Spinner className="w-3 h-3" />}
      {live ? 'LIVE' : label}
    </span>
  )
}

/** שורת נתון בלוח הזיהוי. `verified=false` מסמן נתון שלא אומת. */
function DetectRow({ label, value, ok, verified = true, note }: {
  label: string
  value: React.ReactNode
  ok?: boolean
  verified?: boolean
  note?: string
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-2
                    border-b border-ink-800 last:border-0">
      <span className="text-xs text-ink-400 shrink-0">{label}</span>
      <span className="text-xs text-left flex items-center gap-1.5 min-w-0">
        {ok === true && <IconCheck className="w-3.5 h-3.5 text-ok shrink-0" />}
        {ok === false && <IconX className="w-3.5 h-3.5 text-ink-500 shrink-0" />}
        <span className={`truncate ${ok === false ? 'text-ink-500' : 'text-ink-200'}`}>
          {value}
        </span>
        {!verified && (
          <span className="chip bg-ink-800 text-ink-500 shrink-0" title={note}>
            לא אומת
          </span>
        )}
      </span>
    </div>
  )
}

export function StreamDetectPanel({ info }: { info: LiveDetectResult }) {
  return (
    <div className="rounded-lg bg-ink-900 border border-ink-750 overflow-hidden
                    animate-fade-up">
      <div className="flex items-start gap-3 p-4 border-b border-ink-800">
        {info.thumbnail && (
          <img src={info.thumbnail} alt=""
               className="w-32 aspect-video object-cover rounded-md bg-ink-800 shrink-0" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <Chip tone="brand">{info.platform}</Chip>
            {info.is_live
              ? <span className="chip bg-bad/15 text-bad ring-1 ring-bad/30
                                 inline-flex items-center gap-1.5">
                  <LiveDot />LIVE
                </span>
              : <Chip>לא משדר</Chip>}
          </div>
          <div className="mt-2 text-sm font-medium text-white truncate">
            {info.title || 'ללא כותרת'}
          </div>
          {info.uploader && (
            <div className="text-xs text-ink-400 mt-0.5 truncate">{info.uploader}</div>
          )}
        </div>
      </div>

      <div className="px-4 py-1">
        <DetectRow label="פלטפורמה" value={info.platform} />
        <DetectRow label="מצב שידור"
                   value={info.is_live ? 'משדר עכשיו' : (info.live_status || 'לא פעיל')}
                   ok={info.is_live} />
        <DetectRow label="רזולוציה"
                   value={info.resolution_label
                     ? <span className="ltr-nums">{info.resolution_label}</span>
                     : 'לא ידועה'}
                   ok={info.width > 0 ? true : undefined} />
        <DetectRow label="אודיו"
                   value={info.has_audio ? 'זוהה ערוץ אודיו' : 'לא זוהה אודיו'}
                   ok={info.has_audio}
                   verified={info.audio_checked}
                   note="הנתון נקרא מהמטא-דאטה של הפלטפורמה ולא אומת מול הזרם עצמו." />
        <DetectRow label="זמינות להקלטה"
                   value={info.available ? 'ניתן להקליט' : (info.reason || 'לא זמין')}
                   ok={info.available} />
      </div>

      {info.notes.length > 0 && (
        <div className="px-4 pb-4 space-y-2">
          {info.notes.map((n, i) => (
            <div key={i} className="flex items-start gap-2 rounded-lg bg-warn/10
                                    border border-warn/25 p-2.5">
              <IconAlert className="w-3.5 h-3.5 text-warn shrink-0 mt-px" />
              <p className="text-[11px] text-warn leading-relaxed">{n}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** טיימר הקלטה. מונה מהזמן שנמדד בשרת ומוסיף את הזמן שעבר מאז. */
function useLiveClock(status: LiveStatus) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (status.state !== 'live') return
    const t = setInterval(() => setTick((v) => v + 1), 1000)
    return () => clearInterval(t)
  }, [status.state, status.captured_seconds])
  return status.captured_seconds + (status.state === 'live' ? tick : 0)
}

export function LiveCapturePanel({ status, onStop, stopping }: {
  status: LiveStatus
  onStop: () => void
  stopping: boolean
}) {
  const elapsed = useLiveClock(status)
  const active = ['detecting', 'connecting', 'live', 'reconnecting', 'stopping']
    .includes(status.state)

  return (
    <section className={`card overflow-hidden ${
      status.state === 'live' ? 'ring-1 ring-bad/25' : ''}`}>
      <div className="flex items-center justify-between gap-3 px-5 py-3.5
                      border-b border-ink-800 bg-ink-900/60">
        <div className="flex items-center gap-2.5">
          <IconLive className="w-4 h-4 text-ink-500" />
          <h2 className="text-sm font-semibold text-white">קליטת שידור</h2>
        </div>
        <LiveStateBadge state={status.state} label={status.state_label} />
      </div>

      <div className="p-5">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Stat label="משך הקלטה"
                value={<span className="ltr-nums">{formatDuration(elapsed)}</span>}
                strong />
          <Stat label="מקטעים שנשמרו"
                value={<span className="ltr-nums">{status.segments}</span>} />
          <Stat label="חיבורים מחדש"
                value={<span className="ltr-nums">{status.reconnects}</span>}
                tone={status.reconnects > 0 ? 'text-warn' : undefined} />
          <Stat label="מצב"
                value={status.state_label}
                tone={LIVE_STATE_TONE[status.state]} />
        </div>

        {status.state === 'reconnecting' && (
          <div className="mt-4 flex items-start gap-2.5 rounded-lg bg-warn/10
                          border border-warn/25 p-3">
            <IconAlert className="w-4 h-4 text-warn shrink-0 mt-px" />
            <p className="text-xs text-warn leading-relaxed">
              החיבור לזרם נפל ומתבצע ניסיון חיבור מחדש.{' '}
              <b>החומר שכבר הוקלט שמור</b> — {status.segments} מקטעים נשמרו עד כה
              ולא יאבדו.
            </p>
          </div>
        )}

        {status.error && status.state === 'failed' && (
          <div className="mt-4 flex items-start gap-2.5 rounded-lg bg-bad/10
                          border border-bad/25 p-3">
            <IconAlert className="w-4 h-4 text-bad shrink-0 mt-px" />
            <p className="text-xs text-bad leading-relaxed">{status.error}</p>
          </div>
        )}

        {status.note && (
          <p className="hint mt-3">{status.note}</p>
        )}

        {active && (
          <div className="mt-5 flex items-center justify-between gap-3">
            <p className="hint flex-1">
              עצירה מסיימת את ההקלטה ושולחת את החומר שנאסף לתמלול, לניתוח
              ולעריכה — אותו מסלול בדיוק של קובץ שהועלה.
            </p>
            <button className="btn-danger whitespace-nowrap"
                    onClick={onStop}
                    disabled={stopping || status.stop_requested}>
              {stopping ? <Spinner /> : <IconStop className="w-3.5 h-3.5" />}
              {status.stop_requested ? 'עוצר…' : 'עצור הקלטה'}
            </button>
          </div>
        )}

        {status.state === 'completed' && (
          <div className="mt-4 flex items-start gap-2.5 rounded-lg bg-ok/10
                          border border-ok/25 p-3">
            <IconCheck className="w-4 h-4 text-ok shrink-0 mt-px" />
            <p className="text-xs text-ok leading-relaxed">
              ההקלטה הושלמה. החומר הפך למקור של הפרויקט וממשיך לתמלול וניתוח.
            </p>
          </div>
        )}
      </div>
    </section>
  )
}

function Stat({ label, value, strong, tone }: {
  label: string
  value: React.ReactNode
  strong?: boolean
  tone?: string
}) {
  return (
    <div>
      <div className="text-[11px] text-ink-500">{label}</div>
      <div className={`mt-1 ${strong ? 'text-xl font-semibold' : 'text-sm font-medium'}
                       ${tone ?? 'text-white'}`}>
        {value}
      </div>
    </div>
  )
}
