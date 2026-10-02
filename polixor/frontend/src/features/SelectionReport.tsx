// דוח הבחירה: למה כל קליפ נבחר, ולמה כל "כמעט" וכל כפילות נדחו.
// הנתונים מגיעים מ-/api/projects/{id}/clip-review (מנוע clip_intel).

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import type { ClipReview, ReviewRecord } from '../lib/types'
import { formatDuration } from '../lib/i18nFormat'
import { Badge, Callout, Card, CardHeader } from '../components/ds'

type Group = 'selected' | 'near_misses' | 'duplicates'

function Bar({ label, value, tone }: { label: string; value: number; tone: 'pos' | 'neg' }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-36 shrink-0 text-ink-400">{label}</span>
      <span className="h-1.5 flex-1 rounded bg-ink-800 overflow-hidden">
        <span className={`block h-full ${tone === 'pos' ? 'bg-brand-600' : 'bg-red-500'}`}
              style={{ width: `${tone === 'neg' ? Math.min(100, pct * 3) : pct}%` }} />
      </span>
      <span className="w-10 text-end ltr-nums text-ink-300">{tone === 'neg' ? '−' : ''}{value.toFixed(2)}</span>
    </div>
  )
}

function Record({ r }: { r: ReviewRecord }) {
  const { t } = useTranslation()
  return (
    <details className="rounded-lg border border-ink-800 p-3" data-review-record={r.status}>
      <summary className="cursor-pointer list-none">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={r.status === 'selected' ? 'ok' : r.status === 'near_miss' ? 'warn' : 'neutral'}>
            <span className="ltr-nums">{r.final_score.toFixed(2)}</span>
          </Badge>
          <span className="text-xs text-ink-400 ltr-nums">
            {formatDuration(r.start)}–{formatDuration(r.end)} · {formatDuration(r.duration)}
          </span>
          {r.rejection && <span className="text-xs text-amber-500 bidi-isolate">{r.rejection.text}</span>}
        </div>
        <p className="mt-1.5 text-sm text-ink-200">
          <span className="text-ink-500">{t('project.review.hook')}: </span>
          <bdi dir="auto">{r.hook.text}</bdi>
        </p>
        <p className="text-sm text-ink-200">
          <span className="text-ink-500">{t('project.review.payoff')}: </span>
          <bdi dir="auto">{r.payoff.text}{r.payoff.tail ? ` ${r.payoff.tail}` : ''}</bdi>
        </p>
      </summary>
      <div className="mt-3 grid gap-4 md:grid-cols-2 text-sm">
        <div className="space-y-2">
          <div>
            <div className="label">{t('project.review.whyHook')}</div>
            <ul className="list-disc ps-5 text-ink-400">
              {r.hook.reasons.map((x) => <li key={x.key}>{x.text}</li>)}
              {r.hook.problems.map((x) => <li key={x.key} className="text-amber-500">{x.text}</li>)}
              {!r.hook.reasons.length && !r.hook.problems.length && <li>—</li>}
            </ul>
          </div>
          <div>
            <div className="label">{t('project.review.whyPayoff')}</div>
            <ul className="list-disc ps-5 text-ink-400">
              {r.payoff.reasons.map((x) => <li key={x.key}>{x.text}</li>)}
              {!r.payoff.reasons.length && <li>—</li>}
            </ul>
          </div>
          {r.context.sentences > 0 && (
            <div>
              <div className="label">{t('project.review.context', { count: r.context.sentences, seconds: r.context.seconds.toFixed(1) })}</div>
              <p className="text-ink-400" dir="auto">{r.context.text}</p>
            </div>
          )}
          <div className="text-xs text-ink-500">
            {t('project.review.boundaries', { start: r.boundaries.start_reason.text, end: r.boundaries.end_reason.text })}
            {' · '}{t('project.review.proposedBy')}: {r.proposed_by.map((x) => x.text).join(', ')}
          </div>
        </div>
        <div className="space-y-1.5">
          {r.components.map((c) => <Bar key={c.key} label={c.label} value={c.value} tone="pos" />)}
          {r.penalties.map((c) => <Bar key={c.key} label={c.label} value={c.value} tone="neg" />)}
          {r.low_confidence_words > 0.15 && (
            <p className="text-xs text-amber-500">
              {t('project.review.lowConfidence', { pct: Math.round(r.low_confidence_words * 100) })}
            </p>
          )}
        </div>
      </div>
    </details>
  )
}

export function SelectionReport({ projectId, refreshKey }: { projectId: string; refreshKey?: unknown }) {
  const { t } = useTranslation()
  const [review, setReview] = useState<ClipReview | null>(null)
  const [group, setGroup] = useState<Group>('selected')
  useEffect(() => {
    let alive = true
    api.projectClipReview(projectId).then((r) => { if (alive) setReview(r) }).catch(() => setReview(null))
    return () => { alive = false }
  }, [projectId, refreshKey])
  if (!review?.available) return null
  const counts: Record<Group, number> = {
    selected: review.selected?.length ?? 0,
    near_misses: review.near_misses?.length ?? 0,
    duplicates: review.duplicates?.length ?? 0,
  }
  const items = (review[group] ?? []) as ReviewRecord[]
  return (
    <div data-testid="selection-report"><Card>
      <CardHeader title={t('project.review.title')}
                  subtitle={t('project.review.subtitle', {
                    stories: review.stats?.stories ?? 0, selected: counts.selected,
                    threshold: (review.threshold ?? 0).toFixed(2),
                  })} />
      <div className="px-5 pb-5 space-y-3">
        {review.mode_label && (
          <div data-testid="intelligence-mode" data-mode={review.mode}>
            <Callout tone={review.mode === 'degraded' ? 'warn' : 'brand'}
                     title={t(`project.review.mode.${review.mode === 'degraded' ? 'degraded' : 'semantic'}`)}>
              <bdi dir="auto">{review.mode_label}</bdi>
            </Callout>
          </div>
        )}
        <div className="flex flex-wrap gap-2" role="tablist">
          {(['selected', 'near_misses', 'duplicates'] as Group[]).map((g) => (
            <button key={g} type="button" role="tab" aria-selected={group === g}
                    onClick={() => setGroup(g)}
                    className={`btn-sm ${group === g ? 'btn-primary' : 'btn-ghost'}`}>
              {t(`project.review.groups.${g}`)} <span className="ltr-nums">({counts[g]})</span>
            </button>
          ))}
        </div>
        {items.length ? items.map((r) => <Record key={`${r.status}-${r.id}`} r={r} />)
          : <p className="hint">{t(`project.review.empty.${group}`)}</p>}
      </div>
    </Card></div>
  )
}
