/**
 * רכיבי שידור חי: זיהוי זרם לפני ההקלטה, ולוח מצב בזמן הקלטה.
 *
 * הרכיבים מציגים רק מה שנמדד בפועל. כשנתון לא אומת (למשל אודיו
 * שנקרא מהמטא-דאטה ולא מהזרם), נאמר זאת במפורש.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDuration } from '../lib/format'
import type { LiveState, LiveStatus } from '../lib/types'
import { IconAlert, IconCheck, IconLive, IconStop, Spinner } from './ui'

export const LIVE_STATE_TONE: Record<LiveState, string> = {
  idle: 'text-ink-400',
  detecting: 'text-brand-600',
  connecting: 'text-brand-600',
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

export function LiveStateBadge({ state }: { state: LiveState }) {
  const { t } = useTranslation()
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
      {live ? 'LIVE' : t(`legacy.live.states.${state}`, { defaultValue: state })}
    </span>
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
  const { t } = useTranslation()
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
          <h2 className="text-sm font-semibold text-ink-100">{t('legacy.live.title')}</h2>
        </div>
        <LiveStateBadge state={status.state} />
      </div>

      <div className="p-5">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Stat label={t('legacy.live.duration')}
                value={<span className="ltr-nums">{formatDuration(elapsed)}</span>}
                strong />
          <Stat label={t('legacy.live.segments')}
                value={<span className="ltr-nums">{status.segments}</span>} />
          <Stat label={t('legacy.live.reconnects')}
                value={<span className="ltr-nums">{status.reconnects}</span>}
                tone={status.reconnects > 0 ? 'text-warn' : undefined} />
          <Stat label={t('legacy.live.state')}
                value={t(`legacy.live.states.${status.state}`, { defaultValue: status.state })}
                tone={LIVE_STATE_TONE[status.state]} />
        </div>

        {status.state === 'reconnecting' && (
          <div className="mt-4 flex items-start gap-2.5 rounded-lg bg-warn/10
                          border border-warn/25 p-3">
            <IconAlert className="w-4 h-4 text-warn shrink-0 mt-px" />
            <p className="text-xs text-warn leading-relaxed">
              {t('legacy.live.reconnectingNote', { count: status.segments })}
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
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
            <p className="hint flex-1 min-w-[12rem]">{t('legacy.live.stopNote')}</p>
            <button className="btn-danger whitespace-nowrap"
                    onClick={onStop}
                    disabled={stopping || status.stop_requested}>
              {stopping ? <Spinner /> : <IconStop className="w-3.5 h-3.5" />}
              {status.stop_requested ? t('legacy.live.stopping') : t('legacy.live.stop')}
            </button>
          </div>
        )}

        {status.state === 'completed' && (
          <div className="mt-4 flex items-start gap-2.5 rounded-lg bg-ok/10
                          border border-ok/25 p-3">
            <IconCheck className="w-4 h-4 text-ok shrink-0 mt-px" />
            <p className="text-xs text-ok leading-relaxed">{t('legacy.live.completed')}</p>
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
                       ${tone ?? 'text-ink-100'}`}>
        {value}
      </div>
    </div>
  )
}
