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
import { useTranslation } from 'react-i18next'
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

const GROUP_ORDER = ['cut', 'pause', 'frame', 'caption', 'visual', 'pending']


// --------------------------------------------------------------------------
export function DirectorPlan({ params }: { params: Record<string, any> }) {
  const { t } = useTranslation()
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
      <h3 className="section-title mb-1">{t('legacy.director.title')}</h3>
      <p className="hint mb-4">{t('legacy.director.hint')}</p>

      <div className="grid grid-cols-3 gap-2 mb-4">
        <Stat label={t('legacy.director.applied')} value={String(applied.length)} tone="ok" />
        <Stat label={t('legacy.director.considered')} value={String(considered.length)} />
        <Stat label={t('legacy.director.needsApproval')}
              value={String(decisions.filter((d) => d.requires_approval).length)} />
      </div>

      <div className="space-y-2">
        {byGroup.map(([group, list]) => (
          <div key={group} className="rounded-lg border border-ink-750 bg-ink-900">
            <button
              className="w-full flex items-center justify-between gap-2 px-3 py-2 text-start"
              aria-expanded={open === group}
              onClick={() => setOpen(open === group ? null : group)}
            >
              <span className="flex items-center gap-2">
                <IconScissors className="w-3.5 h-3.5 text-ink-500" />
                <span className="text-xs font-medium text-ink-200">
                  {t(`legacy.director.groups.${group}`, { defaultValue: group })}
                </span>
                <span className="chip">{list.length}</span>
              </span>
              <span className="text-[11px] text-ink-500">
                {open === group ? t('legacy.director.hide') : t('legacy.director.show')}
              </span>
            </button>

            {open === group && (
              <ul className="border-t border-ink-750 divide-y divide-ink-850">
                {list.map((d) => (
                  <li key={d.id} className="px-3 py-2">
                    <div className="flex items-start gap-2">
                      <span className={`mt-0.5 shrink-0 ${d.enabled ? 'text-ok' : 'text-ink-500'}`}>
                        {d.enabled
                          ? <IconCheck className="w-3.5 h-3.5" />
                          : <IconX className="w-3.5 h-3.5" />}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs text-ink-100 bidi-isolate">{d.label}</span>
                          <span className="text-[10px] text-ink-500 ltr-nums">
                            {formatDuration(d.start)}–{formatDuration(d.end)}
                          </span>
                          <span className="chip text-[10px]">
                            {t(`legacy.director.priority.${d.priority}`, { defaultValue: String(d.priority) })}
                          </span>
                          {d.requires_approval && (
                            <span className="chip text-[10px] text-warn">{t('legacy.director.needsApproval')}</span>
                          )}
                        </div>
                        <p className="text-[11px] text-ink-400 mt-0.5 leading-relaxed bidi-isolate">
                          {d.reason}
                        </p>
                      </div>
                      <span className="text-[10px] text-ink-500 ltr-nums shrink-0">
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
            <li key={i} className="text-[11px] text-ink-400 leading-relaxed" dir="auto">• {n}</li>
          ))}
        </ul>
      )}

      {unimplemented.length > 0 && (
        <p className="mt-3 text-[11px] text-ink-500 leading-relaxed">
          {t('legacy.director.unimplemented', { list: unimplemented.join(' · ') })}
        </p>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
export function QaPanel({ params }: { params: Record<string, any> }) {
  const { t } = useTranslation()
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
      <h3 className="section-title mb-1">{t('legacy.qa.title')}</h3>
      <p className="hint mb-4">{t('legacy.qa.hint')}</p>

      {clean ? (
        <div className="flex items-center gap-2 text-xs text-ok">
          <IconCheck className="w-4 h-4" />
          <span className="bidi-isolate">{qa?.summary ?? t('legacy.qa.allPassed')}</span>
        </div>
      ) : (
        <ul className="space-y-2">
          {errors.map((f, i) => (
            <li key={`e${i}`} className="flex items-start gap-2">
              <IconX className="w-3.5 h-3.5 text-bad mt-0.5 shrink-0" />
              <span className="text-[11px] text-bad leading-relaxed" dir="auto">{f.message}</span>
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
              <span className="text-[11px] text-warn leading-relaxed" dir="auto">{f.message}</span>
            </li>
          ))}
        </ul>
      )}

      {audio?.summary && (
        <p className="mt-3 text-[11px] text-ink-400 leading-relaxed">
          {t('legacy.qa.audio')} <span className="bidi-isolate">{audio.summary}</span>
        </p>
      )}

      {qa && Object.keys(qa.checks_skipped ?? {}).length > 0 && (
        <p className="mt-2 text-[11px] text-ink-500 leading-relaxed">
          {t('legacy.qa.skipped')}{' '}
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
  const { t } = useTranslation()
  const audio = params?.audio as Record<string, any> | undefined
  const steps: Array<Record<string, any>> = audio?.steps ?? []
  if (steps.length === 0) return null

  return (
    <div className="card-pad">
      <h3 className="section-title mb-1">{t('legacy.audio.title')}</h3>
      <p className="hint mb-4">{t('legacy.audio.hint')}</p>

      {audio?.before_lufs != null && audio?.after_lufs != null && (
        <div className="grid grid-cols-2 gap-2 mb-3">
          <Stat label={t('legacy.editing.before')} value={`${audio.before_lufs} LUFS`} />
          <Stat label={t('legacy.editing.after')} value={`${audio.after_lufs} LUFS`} tone="ok" />
        </div>
      )}

      <ul className="space-y-1.5">
        {steps.map((s, i) => (
          <li key={i} className="flex items-start gap-2">
            <span className={`mt-0.5 shrink-0 ${s.applied ? 'text-ok' : 'text-ink-500'}`}>
              {s.applied
                ? <IconCheck className="w-3.5 h-3.5" />
                : <IconX className="w-3.5 h-3.5" />}
            </span>
            <span className="text-[11px] text-ink-400 leading-relaxed bidi-isolate">{s.reason}</span>
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
      <div className="text-[10px] text-ink-500">{label}</div>
      <div className={`text-xs font-medium ltr-nums ${tone === 'ok' ? 'text-ok' : 'text-ink-100'}`}>
        {value}
      </div>
    </div>
  )
}
