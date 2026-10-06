// Diagnostics of a project, from what its run recorded (nothing is re-run): what the final
// editor did with the candidates – and why nothing shipped, when nothing did – and where the
// processing time went.

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RotateCcw } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import type { StudioDiagnostics as Diag } from '../lib/types'
import { formatDuration } from '../lib/i18nFormat'
import { Button, Callout } from '../components/ds'
import { LazyDetails } from '../components/ui'

function mins(s: number | null | undefined): string {
  return s == null ? '–' : formatDuration(s)
}

function catLabel(t: (k: string, o?: Record<string, unknown>) => string, c: string): string {
  const [head, rest] = c.split(':')
  return head === 'repair_failed'
    ? t('creator.diag.repairFailed', { what: t(`creator.diag.cat.${rest}`, { defaultValue: rest }) })
    : t(`creator.diag.cat.${head}`, { defaultValue: head })
}

export default function StudioDiagnostics({ projectId, running, refreshKey }: {
  projectId: string; running?: boolean; refreshKey?: string
}) {
  const { t } = useTranslation()
  // nothing is fetched or drawn until the panel is opened (it stays closed for most customers)
  return (
    <LazyDetails className="card p-4 text-sm" testId="diagnostics" summary={t('creator.diag.title')}>
      {() => <DiagnosticsBody projectId={projectId} running={running} refreshKey={refreshKey} />}
    </LazyDetails>
  )
}

function DiagnosticsBody({ projectId, running, refreshKey }: {
  projectId: string; running?: boolean; refreshKey?: string
}) {
  const { t } = useTranslation()
  const { notifyError, pushToast } = useStore()
  const [d, setD] = useState<Diag | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.studioDiagnostics(projectId).then(setD).catch(() => setD(null))
  }, [projectId, refreshKey])
  if (!d) return null
  const g = d.gate
  const p = d.performance
  const retry = async () => {
    setBusy(true)
    try {
      await api.generateProject(projectId, {})
      pushToast({ tone: 'success', title: t('creator.diag.retryStarted') })
    } catch (e) { notifyError(e) } finally { setBusy(false) }
  }
  return (
    <>
      <div className="mt-3 space-y-4">
        {g.mode === 'semantic' && (
          <Callout tone={g.verdict === 'shipped' ? 'ok' : 'warn'} title={t(`creator.diag.verdict.${g.verdict}`)}>
            {t('creator.diag.counts', { candidates: g.candidates, judged: g.judged_by_editor, shipped: g.shipped,
              repaired: g.repaired, rejected: g.rejected, near: g.near_pass, unev: g.not_evaluated })}
          </Callout>
        )}
        {g.not_evaluated > 0 && !running && (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-warn">{t('creator.diag.notEvaluated', { n: g.not_evaluated })}</span>
            <Button size="sm" loading={busy} onClick={retry} icon={<RotateCcw className="w-3.5 h-3.5" />}>
              {t('creator.diag.retryEditor')}</Button>
          </div>
        )}
        {Object.keys(g.rejected_by_category).length > 0 && (
          <div>
            <div className="font-medium text-ink-200 mb-1">{t('creator.diag.whyRejected')}</div>
            <ul className="space-y-0.5 text-ink-400" data-testid="reject-categories">
              {Object.entries(g.rejected_by_category).sort((a, b) => b[1] - a[1]).map(([c, n]) => (
                <li key={c}><span className="ltr-nums">{n}</span> × {catLabel(t, c)}</li>))}
            </ul>
          </div>
        )}
        {g.rejected_clips.length > 0 && (
          <details>
            <summary className="cursor-pointer text-ink-300">{t('creator.diag.rejectedList', { n: g.rejected_clips.length })}</summary>
            <ul className="mt-2 space-y-2">
              {g.rejected_clips.map((r, i) => (
                <li key={i} className="rounded-lg bg-ink-850 p-2.5">
                  <div className="text-ink-200 bidi-isolate">{r.title || '—'}
                    {r.spans?.length ? <span className="ms-2 text-xs text-ink-500 ltr-nums">
                      {formatDuration(r.spans[0][0])}–{formatDuration(r.spans[r.spans.length - 1][1])}</span> : null}</div>
                  <div className="text-xs text-warn">{catLabel(t, r.category)}
                    {r.failed_checks.length > 0 && <> · {r.failed_checks.map((c) => t(`creator.diag.check.${c}`, { defaultValue: c })).join(', ')}</>}
                    {r.repaired && <> · {t('creator.diag.wasRepaired')}</>}</div>
                  {r.reason && <div className="text-xs text-ink-500 mt-1" dir="auto">{r.reason}</div>}
                </li>))}
            </ul>
          </details>
        )}
        <div>
          <div className="font-medium text-ink-200 mb-1">{t('creator.diag.timing')}</div>
          <div className="text-ink-400 ltr-nums" data-testid="perf-summary">
            {t('creator.diag.timingSummary', { total: mins(p.total_seconds), source: mins(p.source_seconds),
              rtf: p.rtf != null ? p.rtf.toFixed(2) : '–' })}
          </div>
          <div className="text-ink-400 ltr-nums">
            {t('creator.diag.milestones', { first: mins(p.milestones.time_to_first_short),
              all: mins(p.milestones.time_to_all_shorts), long: mins(p.milestones.time_to_longform) })}
          </div>
          {p.model && (
            // internal numbers: only present in the admin view of the same data
            <div className="text-ink-400 ltr-nums">
              {t('creator.diag.model', { calls: p.model.calls, cached: p.model.cached,
                hit: p.model.cache_hit_rate != null ? Math.round(p.model.cache_hit_rate * 100) : 0,
                failures: p.model.failures })}
            </div>
          )}
          {p.profile && (
            // admin only: measured processes and full decodes of the source (util/profiler)
            <div className="text-ink-400 ltr-nums" data-testid="perf-processes">
              {t('creator.diag.processes', {
                procs: Object.values(p.profile.subprocesses).reduce((a, b) => a + b, 0),
                decodes: p.profile.full_source_decodes ?? 0 })}
            </div>
          )}
          {p.top_bottlenecks.length > 0 && (
            <ol className="mt-2 list-decimal ps-5 text-ink-400 ltr-nums">
              {p.top_bottlenecks.slice(0, 5).map((b) => (
                <li key={b.name}><span dir="ltr">{b.name}</span> – {mins(b.seconds)}</li>))}
            </ol>
          )}
        </div>
      </div>
    </>
  )
}
