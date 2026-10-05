import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Clapperboard, Film, FolderOpen, Plus, Radio, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { useCoalesced } from '../lib/hooks'
import { UsageCard } from '../components/usage'
import type { Project } from '../lib/types'
import { formatDuration, formatRelative } from '../lib/i18nFormat'
import {
  Badge, Button, Card, ConfirmModal, EmptyState, ErrorState, IconButton, PageHeader,
  ProgressBar, Skeleton, type Tone,
} from '../components/ds'

export function phaseTone(p: Project): Tone {
  if (p.status === 'failed' || p.phase === 'failed') return 'bad'
  if (p.status === 'cancelled') return 'neutral'
  if (p.phase === 'done') return 'ok'
  if (p.phase === 'configure') return 'brand'
  return 'warn'
}

export function phaseLabelKey(p: Project): string {
  if (p.status === 'cancelled') return 'project.phase.cancelled'
  if (p.status === 'failed') return 'project.phase.failed'
  return `project.phase.${p.phase}`
}

export function projectLink(p: Project): string {
  return p.is_live && p.legacy ? `/legacy/jobs/${p.id}` : `/projects/${p.id}`
}

function ProjectCard({ p, onDelete }: { p: Project; onDelete: (p: Project) => void }) {
  const { t } = useTranslation()
  const busy = p.status === 'running' || p.status === 'queued'
  const clips = Object.values(p.clip_counts || {}).reduce((a, b) => a + b, 0)
  const [thumbOk, setThumbOk] = useState(true)
  const thumb = p.source.thumbnail_url && p.source.thumbnail_url.startsWith('https://')
    ? p.source.thumbnail_url : api.projectThumbUrl(p.id)
  return (
    <Card className="group overflow-hidden flex flex-col">
      <Link to={projectLink(p)} className="block focus:outline-none">
        <div className="relative aspect-video bg-ink-800">
          {thumbOk
            ? <img src={thumb} alt="" loading="lazy" onError={() => setThumbOk(false)}
                   className="h-full w-full object-cover" />
            : <div className="flex h-full items-center justify-center text-ink-600">
                <Clapperboard className="w-8 h-8" aria-hidden /></div>}
          <div className="absolute top-2 start-2">
            <Badge tone={phaseTone(p)} className="bg-ink-850/95">{t(phaseLabelKey(p))}</Badge>
          </div>
          {p.source.duration ? (
            <span className="absolute bottom-2 end-2 rounded bg-black/70 px-1.5 py-0.5 text-[11px]
                             font-medium text-white ltr-nums">{formatDuration(p.source.duration)}</span>
          ) : null}
        </div>
      </Link>
      <div className="flex flex-1 flex-col p-4">
        <Link to={projectLink(p)} className="font-medium text-ink-100 hover:text-brand-600 line-clamp-2 bidi-isolate">
          {p.title || t('project.untitled')}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-500">
          <span>{t(`project.platform.${p.source.platform || 'upload'}`, { defaultValue: p.source.platform })}</span>
          {p.mode && <><span aria-hidden>·</span><span>{t(`project.mode.${p.mode}.short`)}</span></>}
          {p.created_at && <><span aria-hidden>·</span><span>{formatRelative(p.created_at)}</span></>}
        </div>
        {busy && (
          <div className="mt-3 space-y-1.5">
            <ProgressBar value={p.overall_progress} label={t(`project.stages.${p.stage}`, { defaultValue: p.stage_label })} />
            <div className="text-xs text-ink-500 truncate">{t(`project.stages.${p.stage}`, { defaultValue: p.stage_label })}</div>
          </div>
        )}
        {p.error && !busy && <p className="mt-2 text-xs text-bad line-clamp-2">{p.error.message}</p>}
        <div className="mt-auto pt-3 flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-xs text-ink-500">
            <Film className="w-3.5 h-3.5" aria-hidden /> {t('dashboard.clipCount', { count: clips })}
          </span>
          <IconButton label={t('dashboard.deleteProject')} icon={<Trash2 className="w-4 h-4" />}
                      onClick={() => onDelete(p)} />
        </div>
      </div>
    </Card>
  )
}

export default function DashboardPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { subscribe, notifyError, pushToast } = useStore()
  const [items, setItems] = useState<Project[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toDelete, setToDelete] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState(false)
  // pages of 30: a reload refreshes as many as are shown; "show more" adds the next page
  const [shown, setShown] = useState(30)
  const [total, setTotal] = useState(0)
  const [more, setMore] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await api.listProjects(shown, 0)
      setItems(res.items)
      setTotal(res.total)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [shown])

  const showMore = async () => {
    if (more || !items) return
    setMore(true)
    try {
      const res = await api.listProjects(30, items.length)
      setItems((prev) => [...(prev ?? []), ...res.items.filter((x) => !prev?.some((p) => p.id === x.id))])
      setTotal(res.total)
      setShown((n) => n + 30)
    } catch (e) { notifyError(e) } finally { setMore(false) }
  }

  useEffect(() => { void load() }, [load])
  const reload = useCoalesced(load, 600)
  useEffect(() => subscribe((e) => {
    if (e.type === 'project.updated' || e.type === 'job.status' || e.type === 'clip.ready') reload()
    if (e.type === 'job.progress' && e.job_id) {
      setItems((prev) => prev?.map((p) => p.id === e.job_id ? {
        ...p, overall_progress: e.data.overall_progress ?? p.overall_progress,
        stage_label: e.data.stage_label ?? p.stage_label,
      } : p) ?? prev)
    }
  }), [subscribe, load, reload])

  const confirmDelete = async () => {
    if (!toDelete) return
    setDeleting(true)
    try {
      await api.deleteProject(toDelete.id, true)
      pushToast({ tone: 'success', title: t('dashboard.deleted') })
      setItems((prev) => prev?.filter((x) => x.id !== toDelete.id) ?? prev)
      setToDelete(null)
    } catch (e) {
      notifyError(e)
    } finally {
      setDeleting(false)
    }
  }

  const active = items?.filter((p) => p.status === 'running' || p.status === 'queued') ?? []

  return (
    <>
      <PageHeader title={t('dashboard.title')} subtitle={t('dashboard.subtitle')}
                  actions={<Button variant="primary" icon={<Plus className="w-4 h-4" />}
                                   onClick={() => navigate('/new')}>{t('dashboard.newProject')}</Button>} />
      <div className="mb-6"><UsageCard /></div>

      {active.length > 0 && (
        <div className="mb-6 flex items-center gap-2 text-sm text-ink-400">
          <Radio className="w-4 h-4 text-warn" aria-hidden />
          {t('dashboard.activeCount', { count: active.length })}
        </div>
      )}

      {error && <div className="mb-6"><ErrorState message={error} onRetry={load} /></div>}

      {items === null && !error && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-72 rounded-xl" />)}
        </div>
      )}

      {items && items.length === 0 && (
        <Card>
          <EmptyState icon={<FolderOpen className="w-7 h-7" />} title={t('dashboard.empty.title')}
                      body={t('dashboard.empty.body')}
                      action={<Button variant="primary" icon={<Plus className="w-4 h-4" />}
                                      onClick={() => navigate('/new')}>{t('dashboard.newProject')}</Button>} />
        </Card>
      )}

      {items && items.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((p) => <ProjectCard key={p.id} p={p} onDelete={setToDelete} />)}
          {items.length < total && (
            <div className="col-span-full flex justify-center">
              <Button onClick={showMore} loading={more} data-testid="projects-more">
                {t('dashboard.showMore', { shown: items.length, total })}
              </Button>
            </div>
          )}
        </div>
      )}

      <ConfirmModal open={toDelete !== null} onClose={() => setToDelete(null)}
                    onConfirm={confirmDelete} busy={deleting} danger
                    title={t('dashboard.deleteTitle')}
                    body={t('dashboard.deleteBody', { title: toDelete?.title || t('project.untitled') })}
                    confirmLabel={t('common.delete')} />
    </>
  )
}
