/**
 * תכנית העריכה של הבמאי, ובדיקת האיכות שאחרי הרינדור.
 *
 * שתי דרישות מהמפרט מתממשות כאן:
 *   §33  „למה ה-AI עשה את זה?" — לכל החלטה מוצג ההסבר שנשמר איתה,
 *        כולל החלטות שנשקלו **ולא** בוצעו והסיבה לכך.
 *   §25  ממצא בבדיקת האיכות מוצג במפורש. קליפ שדורש בדיקה אינו
 *        מוצג כתקין.
 *
 * כל הנתונים מגיעים מ-`render_params` שנשמר בזמן הייצוא. אין כאן
 * חישוב מחדש ואין הערכה — מה שמוצג הוא מה שהמערכת באמת החליטה.
 */

import { useMemo, useState } from 'react'
import { IconCheck, IconScissors, IconX } from './ui'
import { formatDuration } from '../lib/format'
import type { QaReport } from '../lib/types'

// --------------------------------------------------------------------------
type Decision = {
  id: string
  group: string
  action: string
  label: string
  start: number
  end: number
  duration: number
  reason: string
  confidence: number
  priority: number
  enabled: boolean
  requires_approval: boolean
  params: Record<string, unknown>
}

type DirectorSegment = {
  source: { start: number; end: number }
  decisions: Decision[]
  hook: Record<string, any> | null
  pacing: Record<string, any> | null
  unimplemented: string[]
}

const GROUP_LABEL: Record<string, string> = {
  cut: 'חיתוכים',
  pause: 'שתיקות שנשמרו',
  frame: 'מסגור',
  caption: 'הדגשות',
  visual: 'חומר נלווה',
  pending: 'ממתין לאישור',
}

const GROUP_ORDER = ['cut', 'pause', 'frame', 'caption', 'visual', 'pending']

const PRIORITY_LABEL: Record<number, string> = {
  1: 'חובה', 2: 'רצוי', 3: 'תוספת',
}

// --------------------------------------------------------------------------
export function DirectorPlan({ params }: { params: Record<string, any> }) {
  const segments: DirectorSegment[] = useMemo(
    () => (Array.isArray(params?.director) ? params.director : []),
    [params],
  )
  const notes: string[] = useMemo(
    () => (Array.isArray(params?.director_notes) ? params.director_notes : []),
    [params],
  )
  const [open, setOpen] = useState<string | null>(null)

  const decisions = useMemo(
    () => segments.flatMap((s) => s.decisions ?? []),
    [segments],
  )
  if (decisions.length === 0) return null

  const applied = decisions.filter((d) => d.enabled)
  const considered = decisions.filter((d) => !d.enabled)
  const unimplemented = segments[0]?.unimplemented ?? []

  const byGroup = GROUP_ORDER
    .map((g) => [g, decisions.filter((d) => d.group === g)] as const)
    .filter(([, list]) => list.length > 0)

  return (
    <div className="card-pad">
      <h3 className="section-title mb-1">תכנית העריכה של ה-AI</h3>
      <p className="hint mb-4">
        כל החלטה נשמרה עם הסיבה שלה. החלטות שנשקלו ולא בוצעו מופיעות גם הן,
        עם ההסבר למה — כדי שתדע מה המערכת עשתה ולמה, ולא רק מה יצא.
      </p>

      <div className="grid grid-cols-3 gap-2 mb-4">
        <Stat label="בוצעו" value={String(applied.length)} tone="ok" />
        <Stat label="נשקלו ולא בוצעו" value={String(considered.length)} />
        <Stat label="דורש אישור"
              value={String(decisions.filter((d) => d.requires_approval).length)} />
      </div>

      <div className="space-y-2">
        {byGroup.map(([group, list]) => (
          <div key={group} className="rounded-lg border border-ink-750 bg-ink-900">
            <button
              className="w-full flex items-center justify-between gap-2 px-3 py-2 text-right"
              onClick={() => setOpen(open === group ? null : group)}
            >
              <span className="flex items-center gap-2">
                <IconScissors className="w-3.5 h-3.5 text-ink-500" />
                <span className="text-xs font-medium text-ink-200">
                  {GROUP_LABEL[group] ?? group}
                </span>
                <span className="chip">{list.length}</span>
              </span>
              <span className="text-[11px] text-ink-500">
                {open === group ? 'סגור' : 'הצג'}
              </span>
            </button>

            {open === group && (
              <ul className="border-t border-ink-750 divide-y divide-ink-850">
                {list.map((d) => (
                  <li key={d.id} className="px-3 py-2">
                    <div className="flex items-start gap-2">
                      <span className={`mt-0.5 shrink-0 ${d.enabled ? 'text-ok' : 'text-ink-600'}`}>
                        {d.enabled
                          ? <IconCheck className="w-3.5 h-3.5" />
                          : <IconX className="w-3.5 h-3.5" />}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs text-white">{d.label}</span>
                          <span className="text-[10px] text-ink-500 ltr-nums">
                            {formatDuration(d.start)}–{formatDuration(d.end)}
                          </span>
                          <span className="chip text-[10px]">
                            {PRIORITY_LABEL[d.priority] ?? d.priority}
                          </span>
                          {d.requires_approval && (
                            <span className="chip text-[10px] text-warn">דורש אישור</span>
                          )}
                        </div>
                        <p className="text-[11px] text-ink-400 mt-0.5 leading-relaxed">
                          {d.reason}
                        </p>
                      </div>
                      <span className="text-[10px] text-ink-600 ltr-nums shrink-0">
                        {Math.round(d.confidence * 100)}%
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>

      {notes.length > 0 && (
        <ul className="mt-3 space-y-1">
          {notes.map((n, i) => (
            <li key={i} className="text-[11px] text-ink-400 leading-relaxed">• {n}</li>
          ))}
        </ul>
      )}

      {unimplemented.length > 0 && (
        <p className="mt-3 text-[11px] text-ink-500 leading-relaxed">
          קטגוריות שאינן מתוכננות בגרסה הזו ולא בוצעו:{' '}
          {unimplemented.join(' · ')}.
        </p>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
export function QaPanel({ params }: { params: Record<string, any> }) {
  const qa: QaReport | undefined = params?.qa
  const audio = params?.audio as Record<string, any> | undefined
  if (!qa && !audio) return null

  const findings = qa?.findings ?? []
  const errors = findings.filter((f) => f.severity === 'error')
  const warnings = findings.filter((f) => f.severity === 'warning')
  const audioIssues: string[] = audio?.issues ?? []
  const clean = errors.length === 0 && audioIssues.length === 0

  return (
    <div className="card-pad">
      <h3 className="section-title mb-1">בדיקת איכות אחרי הרינדור</h3>
      <p className="hint mb-4">
        הקובץ נבדק אחרי הייצוא — אורך, זרמים, פריימים שחורים, קיפאון, דגימת
        פריימים, גבולות כתוביות ועוצמת אודיו. קוד יציאה 0 של FFmpeg אינו
        נחשב הוכחה.
      </p>

      {clean ? (
        <div className="flex items-center gap-2 text-xs text-ok">
          <IconCheck className="w-4 h-4" />
          <span>{qa?.summary ?? 'כל הבדיקות עברו.'}</span>
        </div>
      ) : (
        <ul className="space-y-2">
          {errors.map((f, i) => (
            <li key={`e${i}`} className="flex items-start gap-2">
              <IconX className="w-3.5 h-3.5 text-bad mt-0.5 shrink-0" />
              <span className="text-[11px] text-bad leading-relaxed">{f.message}</span>
            </li>
          ))}
          {audioIssues.map((m, i) => (
            <li key={`a${i}`} className="flex items-start gap-2">
              <IconX className="w-3.5 h-3.5 text-bad mt-0.5 shrink-0" />
              <span className="text-[11px] text-bad leading-relaxed">{m}</span>
            </li>
          ))}
          {warnings.map((f, i) => (
            <li key={`w${i}`} className="flex items-start gap-2">
              <span className="w-3.5 h-3.5 mt-0.5 shrink-0 text-warn">!</span>
              <span className="text-[11px] text-warn leading-relaxed">{f.message}</span>
            </li>
          ))}
        </ul>
      )}

      {audio?.summary && (
        <p className="mt-3 text-[11px] text-ink-400 leading-relaxed">
          אודיו: {audio.summary}
        </p>
      )}

      {qa && Object.keys(qa.checks_skipped ?? {}).length > 0 && (
        <p className="mt-2 text-[11px] text-ink-500 leading-relaxed">
          בדיקות שלא רצו:{' '}
          {Object.entries(qa.checks_skipped)
            .map(([k, v]) => `${k} (${v})`)
            .join(' · ')}
        </p>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
export function AudioMasteringSummary({ params }: { params: Record<string, any> }) {
  const audio = params?.audio as Record<string, any> | undefined
  const steps: Array<Record<string, any>> = audio?.steps ?? []
  if (steps.length === 0) return null

  return (
    <div className="card-pad">
      <h3 className="section-title mb-1">מה נעשה לאודיו</h3>
      <p className="hint mb-4">
        כל שלב הופעל רק כשהמדידה הראתה שהוא נחוץ. עיבוד אודיו הוא הרסני,
        ולכן מקור תקין נשאר כמו שהוא.
      </p>

      {audio?.before_lufs != null && audio?.after_lufs != null && (
        <div className="grid grid-cols-2 gap-2 mb-3">
          <Stat label="לפני" value={`${audio.before_lufs} LUFS`} />
          <Stat label="אחרי" value={`${audio.after_lufs} LUFS`} tone="ok" />
        </div>
      )}

      <ul className="space-y-1.5">
        {steps.map((s, i) => (
          <li key={i} className="flex items-start gap-2">
            <span className={`mt-0.5 shrink-0 ${s.applied ? 'text-ok' : 'text-ink-600'}`}>
              {s.applied
                ? <IconCheck className="w-3.5 h-3.5" />
                : <IconX className="w-3.5 h-3.5" />}
            </span>
            <span className="text-[11px] text-ink-400 leading-relaxed">{s.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// --------------------------------------------------------------------------
function Stat({ label, value, tone }: {
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
