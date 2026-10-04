// עמוד הפרויקט: Import → Analyze → Mode → Settings → Generate → Results.
// כל שינוי בהגדרות נשמר בפרויקט בשרת, כך שאפשר לסגור ולחזור.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ArrowRight, Play, RefreshCw, Square, Trash2, Wand2 } from 'lucide-react'
import { api, PolixorApiError } from '../lib/api'
import { useStore } from '../lib/store'
import type { Clip, Project, ProjectConfig, ProjectMode, ProjectOptions } from '../lib/types'
import { formatDuration, iso } from '../lib/i18nFormat'
import {
  Badge, Button, Callout, Card, ConfirmModal, ErrorState, PageHeader, ProgressBar, Skeleton,
  Stepper, type Step,
} from '../components/ds'
import AnalysisSummary from '../features/AnalysisSummary'
import { ModePicker, SettingsForm } from '../features/ProjectSettings'
import { LongformResult, ShortResults } from '../features/ProjectResults'
import StudioResults from '../features/StudioResults'
import { SelectionReport } from '../features/SelectionReport'
import { phaseLabelKey, phaseTone } from './DashboardPage'

type View = 'mode' | 'settings' | 'results'
const STEP_KEYS = ['import', 'analyze', 'mode', 'settings', 'generate', 'results'] as const

function stepIndex(p: Project, view: View): number {
  if (p.phase === 'importing') return 0
  if (p.phase === 'analyzing') return 1
  if (p.phase === 'generating') return 4
  if (p.phase === 'failed') return p.analysis ? 4 : 1
  if (view === 'results') return 5
  if (view === 'settings') return 3
  return 2
}

function ProgressPanel({ p, onCancel, cancelling }: { p: Project; onCancel: () => void; cancelling: boolean }) {
  const { t } = useTranslation()
  const queued = p.status === 'queued'
  const stage = t(`project.stages.${p.stage}`, { defaultValue: p.stage_label })
  return (
    <Card className="p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-base font-semibold text-ink-100">{t(`project.progress.${p.phase}`, { defaultValue: p.stage_label })}</div>
          <div className="mt-1 text-sm text-ink-500">{queued ? t('project.progress.queued') : stage}</div>
        </div>
        <Button variant="danger" size="sm" loading={cancelling} onClick={onCancel}
                icon={<Square className="w-3.5 h-3.5" />}>{t('common.cancel')}</Button>
      </div>
      <ProgressBar value={p.overall_progress} indeterminate={queued} label={stage} />
      <div className="flex flex-wrap justify-between gap-2 text-xs text-ink-500">
        <span className="ltr-nums">{Math.round(p.overall_progress * 100)}%</span>
        {p.eta_seconds ? <span>{t('project.progress.eta', { time: iso(formatDuration(p.eta_seconds)) })}</span> : null}
      </div>
      {p.message && !queued && <p className="text-sm text-ink-400 bidi-isolate">{p.message}</p>}
      <p className="hint">{t('project.progress.canLeave')}</p>
    </Card>
  )
}

export default function ProjectPage() {
  const { projectId = '' } = useParams()
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { subscribe, notifyError, pushToast } = useStore()
  const [p, setP] = useState<Project | null>(null)
  const [loadError, setLoadError] = useState<PolixorApiError | Error | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [cfg, setCfg] = useState<ProjectConfig | null>(null)
  const [options, setOptions] = useState<ProjectOptions | null>(null)
  const [view, setView] = useState<View>('mode')
  const [busy, setBusy] = useState<'' | 'generate' | 'cancel' | 'analyze' | 'delete'>('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const saveTimer = useRef<number | null>(null)
  const viewInit = useRef(false)

  const load = useCallback(async () => {
    try {
      const proj = await api.getProject(projectId)
      setP(proj)
      setLoadError(null)
      setCfg((prev) => prev ?? proj.config)
      if (!viewInit.current && (proj.phase === 'configure' || proj.phase === 'done')) {
        viewInit.current = true
        setView(proj.phase === 'done' ? 'results' : proj.mode ? 'settings' : 'mode')
      }
      if (proj.phase === 'done' || proj.phase === 'generating' || proj.legacy) {
        setClips(await api.projectClips(projectId))
      }
    } catch (e) {
      setLoadError(e as Error)
    }
  }, [projectId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { api.projectDefaults().then((d) => setOptions(d.options)).catch(() => undefined) }, [])

  useEffect(() => subscribe((e) => {
    const mine = e.job_id === projectId || e.data?.project_id === projectId
    if (!mine) return
    if (e.type === 'job.progress') {
      setP((prev) => prev ? {
        ...prev,
        stage: e.data.stage ?? prev.stage,
        stage_label: e.data.stage_label ?? prev.stage_label,
        stage_progress: e.data.stage_progress ?? prev.stage_progress,
        overall_progress: e.data.overall_progress ?? prev.overall_progress,
        message: e.data.message ?? prev.message,
        eta_seconds: e.data.eta_seconds ?? prev.eta_seconds,
      } : prev)
      return
    }
    if (e.type === 'project.updated' || e.type === 'job.status') {
      void load().then(() => {
        if (e.data?.phase === 'done') setView('results')
        if (e.data?.phase === 'configure') setView((v) => (v === 'results' ? 'mode' : v))
      })
    }
    if (e.type.startsWith('clip.')) void api.projectClips(projectId).then(setClips).catch(() => undefined)
  }), [subscribe, projectId, load])

  // שמירה אוטומטית של ההגדרות (debounce)
  const updateCfg = (next: ProjectConfig) => {
    setCfg(next)
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(async () => {
      try {
        const saved = await api.patchProject(projectId, { config: next, mode: next.mode ?? undefined })
        setP(saved)
      } catch (e) {
        notifyError(e)
      }
    }, 600)
  }

  const chooseMode = (m: ProjectMode) => {
    if (!cfg) return
    const next = { ...cfg, mode: m, ...(m === 'longform' ? { aspect_ratio: '16:9' as const } : {}) }
    updateCfg(next)
    setView('settings')
  }

  const generate = async () => {
    if (!cfg) return
    setBusy('generate')
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    try {
      const proj = await api.generateProject(projectId, { mode: cfg.mode ?? 'short', config: cfg })
      setP(proj)
      setClips([])
      pushToast({ tone: 'info', title: t('project.generateStarted') })
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy('')
    }
  }

  const cancel = async () => {
    setBusy('cancel')
    try { setP(await api.cancelProject(projectId)) } catch (e) { notifyError(e) } finally { setBusy('') }
  }

  const reanalyze = async () => {
    setBusy('analyze')
    try { setP(await api.analyzeProject(projectId)); viewInit.current = false } catch (e) { notifyError(e) } finally { setBusy('') }
  }

  const remove = async () => {
    setBusy('delete')
    try {
      await api.deleteProject(projectId, true)
      pushToast({ tone: 'success', title: t('dashboard.deleted') })
      navigate('/')
    } catch (e) {
      notifyError(e)
      setBusy('')
    }
  }

  const steps: Step[] = useMemo(() => {
    if (!p) return []
    const cur = stepIndex(p, view)
    const failed = p.status === 'failed'
    return STEP_KEYS.map((k, i) => ({
      key: k, label: t(`project.steps.${k}`),
      state: i < cur ? 'done' : i === cur ? (failed ? 'error' : 'current') : 'upcoming',
    }))
  }, [p, view, t])

  if (loadError) {
    const notFound = loadError instanceof PolixorApiError && loadError.status === 404
    return (
      <div className="space-y-4">
        <ErrorState title={notFound ? t('project.notFound') : undefined}
                    message={loadError.message} onRetry={notFound ? undefined : load} />
        <Link to="/" className="btn-ghost">{t('project.backToDashboard')}</Link>
      </div>
    )
  }
  if (!p || !cfg) {
    return <div className="space-y-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-64" /></div>
  }

  const running = p.status === 'running' || p.status === 'queued'
  const failed = p.status === 'failed' || p.status === 'cancelled'
  const canConfigure = !running && Boolean(p.analysis)
  const BackIcon = document.documentElement.dir === 'rtl' ? ArrowRight : ArrowLeft
  const longClip = clips.find((c) => c.kind === 'long')

  return (
    <>
      <PageHeader
        back={<Link to="/" className="inline-flex items-center gap-1.5 text-sm text-ink-500 hover:text-ink-100">
          <BackIcon className="w-4 h-4" aria-hidden />{t('project.backToDashboard')}</Link>}
        title={<span className="bidi-isolate">{p.title || t('project.untitled')}</span>}
        subtitle={<span className="inline-flex flex-wrap items-center gap-2">
          <Badge tone={phaseTone(p)}>{t(phaseLabelKey(p))}</Badge>
          <span>{t(`project.platform.${p.source.platform || 'upload'}`, { defaultValue: p.source.platform })}</span>
          {p.source.duration ? <span className="ltr-nums">{formatDuration(p.source.duration)}</span> : null}
          {p.source.section && <span>{t('project.section', { start: iso(formatDuration(p.source.section.start)), end: iso(formatDuration(p.source.section.end)) })}</span>}
        </span>}
        actions={<>
          {canConfigure && <Button size="sm" onClick={reanalyze} loading={busy === 'analyze'}
                                   icon={<RefreshCw className="w-3.5 h-3.5" />}>{t('project.reanalyze')}</Button>}
          <Button size="sm" variant="danger" onClick={() => setConfirmDelete(true)}
                  icon={<Trash2 className="w-3.5 h-3.5" />}>{t('common.delete')}</Button>
        </>} />

      {!p.legacy && <div className="mb-6"><Stepper steps={steps} /></div>}

      <div className="space-y-6">
        {running && <ProgressPanel p={p} onCancel={cancel} cancelling={busy === 'cancel'} />}

        {failed && !running && (
          <ErrorState title={p.status === 'cancelled' ? t('project.cancelledTitle') : t('project.failedTitle')}
                      message={p.error?.message} hint={p.error?.hint}
                      onRetry={p.analysis ? () => setView('settings') : reanalyze} />
        )}

        {p.legacy && (
          <Callout tone="neutral" title={t('project.legacyTitle')}>{t('project.legacyBody')}</Callout>
        )}

        {!running && p.analysis && !p.legacy && view !== 'results' && (
          <AnalysisSummary a={p.analysis} />
        )}

        {canConfigure && !p.legacy && view === 'mode' && (
          <section aria-labelledby="mode-h" className="space-y-3">
            <h2 id="mode-h" className="text-lg font-semibold">{t('project.chooseMode')}</h2>
            <ModePicker mode={cfg.mode} onChange={chooseMode} />
          </section>
        )}

        {canConfigure && !p.legacy && view === 'settings' && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">{t('project.steps.settings')}
                <span className="ms-2 text-sm font-normal text-ink-500">· {t(`project.mode.${cfg.mode ?? 'short'}.title`)}</span></h2>
              <Button size="sm" variant="quiet" onClick={() => setView('mode')}>{t('project.changeMode')}</Button>
            </div>
            <SettingsForm cfg={cfg} onChange={updateCfg} options={options} analysis={p.analysis} projectId={p.id} />
            <Card className="p-5 flex flex-wrap items-center justify-between gap-4 sticky bottom-4 shadow-pop">
              <div className="text-sm text-ink-400">
                {cfg.mode === 'longform'
                  ? t('project.generateLong', { minutes: Math.round(cfg.longform_target_seconds / 60) })
                  : cfg.mode === 'package'
                    ? t('project.generatePackage', { count: cfg.clip_count, aspect: iso(cfg.aspect_ratio) })
                    : t('project.generateShort', { count: cfg.clip_count, aspect: iso(cfg.aspect_ratio) })}
                {p.phase === 'done' && <div className="text-xs text-warn mt-1">{t('project.regenerateWarning')}</div>}
              </div>
              <div className="flex gap-2">
                {p.phase === 'done' && <Button onClick={() => setView('results')}>{t('project.backToResults')}</Button>}
                <Button variant="primary" size="lg" loading={busy === 'generate'} onClick={generate}
                        icon={p.phase === 'done' ? <Wand2 className="w-4 h-4" /> : <Play className="w-4 h-4" />}>
                  {p.phase === 'done' ? t('project.regenerate') : t('project.generate')}
                </Button>
              </div>
            </Card>
          </>
        )}

        {p.legacy && !running && (
          longClip && clips.filter((c) => c.kind === 'long').length === 1 && !clips.some((c) => c.kind !== 'long')
            ? <LongformResult clip={longClip} onRegenerate={() => setView('settings')}
                              onDeleted={(id) => setClips((c) => c.filter((x) => x.id !== id))} />
            : <ShortResults clips={clips} onRegenerate={() => setView('settings')}
                            onDeleted={(id) => setClips((c) => c.filter((x) => x.id !== id))} />
        )}

        {/* Polixor Studio: the finished outputs – while generating (they appear as they are made) and after */}
        {!p.legacy && ((view === 'results' && p.phase === 'done' && !running) || (running && p.phase === 'generating')) && (
          <StudioResults projectId={p.id} refreshKey={p.updated_at ?? undefined} running={running} />
        )}
        {!p.legacy && view === 'results' && p.phase === 'done' && !running && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setView('settings')} icon={<Wand2 className="w-3.5 h-3.5" />}>
              {t('project.results.changeSettings')}</Button>
          </div>
        )}

        {(view === 'results' || p.legacy) && !running && (p.phase === 'done' || p.legacy) && cfg.mode !== 'longform' && (
          <details className="card p-4 text-sm">
            <summary className="cursor-pointer font-medium text-ink-200">{t('creator.diagnostics')}</summary>
            <div className="mt-3"><SelectionReport projectId={p.id} refreshKey={p.updated_at} /></div>
          </details>
        )}

        {p.performance && !running && (
          <details className="card p-4 text-sm" data-testid="performance">
            <summary className="cursor-pointer font-medium text-ink-200">
              {t('project.performance.title')}{' '}
              <span className="text-ink-500 ltr-nums">
                {t('project.performance.summary', {
                  seconds: p.performance.total_seconds.toFixed(0),
                  rtf: p.performance.total_rtf != null ? p.performance.total_rtf.toFixed(3) : '–',
                })}
              </span>
            </summary>
            <p className="hint mt-2">{t('project.performance.hint')}</p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-ink-500 text-start">
                  <th className="text-start py-1 pe-3">{t('project.performance.stage')}</th>
                  <th className="text-end py-1 pe-3">{t('project.performance.seconds')}</th>
                  <th className="text-end py-1">RTF</th>
                </tr></thead>
                <tbody>
                  {p.performance.stages.map((r, i) => (
                    <tr key={`s${i}`} className="border-t border-ink-800">
                      <td className="py-1 pe-3 text-ink-300">{t(`project.stages.${r.stage}`, { defaultValue: r.stage })}</td>
                      <td className="py-1 pe-3 text-end ltr-nums">{r.seconds.toFixed(1)}</td>
                      <td className="py-1 text-end ltr-nums">{r.rtf != null ? r.rtf.toFixed(3) : '–'}</td>
                    </tr>
                  ))}
                  {p.performance.substages.map((r, i) => (
                    <tr key={`u${i}`} className="border-t border-ink-850 text-ink-500">
                      <td className="py-1 pe-3 ps-4"><span dir="ltr">{r.name}</span></td>
                      <td className="py-1 pe-3 text-end ltr-nums">{r.seconds.toFixed(1)}</td>
                      <td className="py-1 text-end ltr-nums">{r.rtf != null ? r.rtf.toFixed(3) : '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}

        {p.notes?.length > 0 && !running && (
          <details className="card p-4 text-sm">
            <summary className="cursor-pointer font-medium text-ink-200">{t('project.notes', { count: p.notes.length })}</summary>
            <ul className="mt-3 space-y-1 text-ink-400 list-disc ps-5">{p.notes.map((n) => <li key={n} className="bidi-isolate">{n}</li>)}</ul>
          </details>
        )}
      </div>

      <ConfirmModal open={confirmDelete} onClose={() => setConfirmDelete(false)} onConfirm={remove}
                    busy={busy === 'delete'} danger title={t('dashboard.deleteTitle')}
                    body={t('dashboard.deleteBody', { title: p.title || t('project.untitled') })}
                    confirmLabel={t('common.delete')} />
    </>
  )
}
