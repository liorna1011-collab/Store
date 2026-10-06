import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/ds'
import { api } from '../lib/api'
import { PublishButton } from '../components/publish'
import { useJobs, useStore } from '../lib/store'
import {
  CATEGORY_LABEL, EDIT_STYLE_TONE, formatBytes, formatDuration, KIND_LABEL, scoreTone,
} from '../lib/format'
import { clipIsPlayable } from '../lib/types'
import type { Clip, Job } from '../lib/types'
import {
  Chip, ConfirmDialog, EmptyState, IconDownload, IconEdit, IconFilm, IconPlay,
  IconRefresh, IconTrash, IconX, Modal, Spinner,
} from '../components/ui'

type KindFilter = 'all' | 'long' | 'short' | 'highlights'

export default function ClipsPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const { notifyError, pushToast } = useStore()
  const { jobs } = useJobs()

  const jobFilter = params.get('job') ?? ''
  const [clips, setClips] = useState<Clip[]>([])
  const [loading, setLoading] = useState(true)
  const [kind, setKind] = useState<KindFilter>('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [preview, setPreview] = useState<Clip | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Clip | null>(null)
  const [zipping, setZipping] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setClips(await api.listClips(jobFilter || undefined))
    } catch (e) {
      notifyError(e, t('clips.gallery.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [jobFilter, notifyError])

  useEffect(() => { void load() }, [load])

  const visible = useMemo(
    () => clips.filter((c) => kind === 'all' || c.kind === kind),
    [clips, kind])

  const readySelected = useMemo(
    () => visible.filter((c) => selected.has(c.id) && c.has_file),
    [visible, selected])

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const selectAll = () => {
    const ready = visible.filter((c) => c.has_file).map((c) => c.id)
    setSelected(new Set(
      ready.every((id) => selected.has(id)) ? [] : ready))
  }

  const doZip = async () => {
    if (!readySelected.length) return
    setZipping(true)
    try {
      await api.downloadZip(readySelected.map((c) => c.id), true)
      pushToast({
        tone: 'success', title: t('clips.gallery.downloadStarted'),
        body: t('clips.gallery.zipBody', { count: readySelected.length }),
      })
    } catch (e) { notifyError(e, t('common.errors.zipFailed')) } finally { setZipping(false) }
  }

  const doDelete = async () => {
    if (!confirmDelete) return
    setBusy(true)
    try {
      await api.deleteClip(confirmDelete.id)
      setClips((prev) => prev.filter((c) => c.id !== confirmDelete.id))
      setSelected((prev) => { const n = new Set(prev); n.delete(confirmDelete.id); return n })
      pushToast({ tone: 'success', title: t('clips.deleted') })
    } catch (e) { notifyError(e, t('clips.gallery.deleteFailed')) } finally {
      setBusy(false); setConfirmDelete(null)
    }
  }

  const jobOptions = jobs.filter((j) => (j.clip_counts?.total ?? 0) > 0)

  return (
    <div>
      <PageHeader
        title={t('clips.gallery.title')}
        subtitle={t('clips.gallery.subtitle')}
        actions={
          <button className="btn-ghost btn-sm" onClick={() => void load()}>
            <IconRefresh className="w-4 h-4" />{t('common.refresh')}
          </button>
        }
      />

      {/* ---- סינון ופעולות ---- */}
      <div className="card p-3 mb-5 flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {(['all', 'short', 'long', 'highlights'] as const).map((k) => (
            <button key={k} onClick={() => setKind(k)} aria-pressed={kind === k}
                    className={`btn btn-sm ${kind === k
                      ? 'bg-brand-600/10 text-brand-600 ring-1 ring-brand-500/30'
                      : 'text-ink-400 hover:text-ink-100 hover:bg-ink-800'}`}>
              {t(`clips.gallery.filter.${k}`)}
            </button>
          ))}
        </div>

        <select className="field !w-auto !py-1.5 text-xs" aria-label={t('clips.gallery.projectFilter')}
                value={jobFilter}
                onChange={(e) => {
                  const v = e.target.value
                  setParams(v ? { job: v } : {})
                  setSelected(new Set())
                }}>
          <option value="">{t('clips.gallery.allProjects')}</option>
          {jobOptions.map((j: Job) => (
            <option key={j.id} value={j.id}>
              {j.title || j.id} ({j.clip_counts.total})
            </option>
          ))}
        </select>

        <div className="ms-auto flex items-center gap-2">
          {visible.some((c) => c.has_file) && (
            <button className="btn-ghost btn-sm" onClick={selectAll}>
              {visible.filter((c) => c.has_file).every((c) => selected.has(c.id))
                ? t('clips.gallery.clearSelection') : t('clips.gallery.selectAll')}
            </button>
          )}
          <button className="btn-primary btn-sm" onClick={() => void doZip()}
                  disabled={!readySelected.length || zipping}>
            {zipping ? <Spinner className="w-3.5 h-3.5" />
              : <IconDownload className="w-3.5 h-3.5" />}
            {t('clips.gallery.downloadZip')}{readySelected.length ? ` (${readySelected.length})` : ''}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-72" />)}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<IconFilm className="w-10 h-10" />}
          title={t('clips.gallery.emptyTitle')}
          body={clips.length ? t('clips.gallery.emptyFiltered') : t('clips.gallery.emptyBody')}
          action={<Link to="/new" className="btn-primary">{t('dashboard.newProject')}</Link>}
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {visible.map((c) => (
            <ClipCard key={c.id} clip={c}
                      selected={selected.has(c.id)}
                      onToggle={() => toggle(c.id)}
                      onPreview={() => setPreview(c)}
                      onDelete={() => setConfirmDelete(c)} />
          ))}
        </div>
      )}

      {/* ---- תצוגה מקדימה ---- */}
      <Modal open={Boolean(preview)} onClose={() => setPreview(null)}
             title={preview?.title ?? ''} wide>
        {preview && (
          <div className="space-y-4">
            <video
              key={preview.id}
              src={api.clipFileUrl(preview.id)}
              controls autoPlay
              className={`w-full rounded-lg bg-black
                ${preview.aspect === '9:16' ? 'max-h-[64vh] mx-auto w-auto' : ''}`}
            />
            {preview.description && (
              <p className="text-sm text-ink-300 leading-relaxed">{preview.description}</p>
            )}
            {preview.reason && (
              <div className="rounded-lg bg-ink-900 border border-ink-750 p-3">
                <div className="text-[11px] text-ink-500 mb-1">{t('clips.gallery.whyChosen')}</div>
                <p className="text-xs text-ink-300 leading-relaxed">{preview.reason}</p>
              </div>
            )}
            <div className="flex gap-2">
              <a className="btn-primary btn-sm" href={api.clipDownloadUrl(preview.id)}>
                <IconDownload className="w-3.5 h-3.5" />{t('clips.gallery.downloadMp4')}
              </a>
              {preview.cue_count > 0 && (
                <a className="btn-ghost btn-sm" href={api.clipSrtUrl(preview.id)}>
                  {t('clips.gallery.downloadSrt')}
                </a>
              )}
              <Link className="btn-ghost btn-sm" to={`/clips/${preview.id}/edit`}>
                <IconEdit className="w-3.5 h-3.5" />{t('clips.edit')}
              </Link>
            </div>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        title={t('clips.deleteTitle')}
        body={t('clips.gallery.deleteBody', { title: confirmDelete?.title ?? '' })}
        onConfirm={() => void doDelete()}
        onCancel={() => setConfirmDelete(null)}
        busy={busy}
      />
    </div>
  )
}

// --------------------------------------------------------------------------
function ClipCard({ clip, selected, onToggle, onPreview, onDelete }: {
  clip: Clip
  selected: boolean
  onToggle: () => void
  onPreview: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const vertical = clip.aspect !== '16:9'
  const rp = clip.render_params as any
  const category = String(rp?.category ?? '')
  const editStyle = String(rp?.edit_style ?? '')
  const editLabel = String(rp?.edit_style_label ?? '')
  const rawDuration = Number(rp?.raw_duration ?? 0)
  const saved = rawDuration > clip.duration + 0.3 ? rawDuration - clip.duration : 0

  return (
    <div className={`card overflow-hidden group transition-colors
      ${selected ? 'ring-2 ring-brand-500 border-brand-500' : 'hover:border-ink-600'}`}>
      <div className={`relative bg-ink-900 ${vertical ? 'aspect-[3/4]' : 'aspect-video'}`}>
        {clip.has_thumbnail ? (
          <img src={api.clipThumbUrl(clip.id)} alt=""
               className="w-full h-full object-cover" loading="lazy" />
        ) : (
          <div className="w-full h-full grid place-items-center text-ink-700">
            <IconFilm className="w-8 h-8" />
          </div>
        )}

        {clip.has_file && (
          <button onClick={onPreview}
                  className="absolute inset-0 grid place-items-center bg-black/0
                             group-hover:bg-black/45 transition-colors"
                  aria-label={t('clips.gallery.play')}>
            <span className="w-11 h-11 rounded-full bg-white/95 text-ink-900
                             grid place-items-center opacity-0 group-hover:opacity-100
                             transition-opacity scale-90 group-hover:scale-100">
              <IconPlay className="w-4 h-4 rtl:rotate-180" />
            </span>
          </button>
        )}

        <label className="absolute top-2 end-2 cursor-pointer">
          <input type="checkbox" checked={selected} onChange={onToggle} aria-label={t('clips.gallery.select')}
                 disabled={!clip.has_file}
                 className="w-4 h-4 accent-brand-500 disabled:opacity-30" />
        </label>

        <div className="absolute bottom-2 start-2 flex gap-1">
          <span className="chip bg-black/75 text-white ltr-nums backdrop-blur-sm">
            {formatDuration(clip.duration || (clip.source_end - clip.source_start))}
          </span>
        </div>
        <div className="absolute bottom-2 end-2">
          <span className="chip bg-black/75 text-white backdrop-blur-sm">
            {KIND_LABEL[clip.kind]}
          </span>
        </div>

        {clip.status === 'needs_review' && (
          <div className="absolute top-2 start-2">
            <span className="chip bg-warn text-white backdrop-blur-sm">
              {t('clips.status.needs_review')}
            </span>
          </div>
        )}

        {!clipIsPlayable(clip.status) && (
          <div className="absolute inset-0 grid place-items-center bg-ink-950/80">
            <div className="text-center">
              {clip.status === 'failed' ? (
                <>
                  <IconX className="w-6 h-6 text-bad mx-auto" />
                  <div className="mt-2 text-xs text-bad px-3">{clip.error || t('clips.gallery.exportFailed')}</div>
                </>
              ) : (
                <>
                  <Spinner className="w-6 h-6 text-brand-400 mx-auto" />
                  <div className="mt-2 text-xs text-ink-400">
                    {t(`clips.status.${clip.status}`)}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="p-3.5">
        <div className="flex items-start gap-2">
          <h3 className="text-sm text-ink-100 leading-snug line-clamp-2 flex-1 bidi-isolate"
              title={clip.title}>
            {clip.title}
          </h3>
          <span className={`text-xs font-semibold ltr-nums shrink-0 ${scoreTone(clip.score)}`}
                title={t('project.scoreDisclaimer')}>
            {Math.round(clip.score * 100)}
          </span>
        </div>

        {(category || editStyle) && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {category && <Chip>{CATEGORY_LABEL[category] ?? category}</Chip>}
            {editStyle && editStyle !== 'raw' && (
              <span className={`chip ${EDIT_STYLE_TONE[editStyle] ?? ''}`}
                    title={String(rp?.edit_summary ?? '')}>
                ✂ {t(`legacy.editStyle.${editStyle}`, { defaultValue: editLabel || editStyle })}
              </span>
            )}
          </div>
        )}

        {saved > 0 && (
          <div className="mt-1.5 text-[11px] text-ok">
            {t('clips.gallery.deadAirSaved', { seconds: saved.toFixed(1) })}
          </div>
        )}

        <div className="mt-2 text-[11px] text-ink-500">
          {t('clips.gallery.atSource', { time: formatDuration(clip.source_start) })}
          {clip.file_size
            ? <> · <span className="ltr-nums">{formatBytes(clip.file_size)}</span></>
            : null}
          {clip.cue_count
            ? <> · {t('clips.gallery.cues', { count: clip.cue_count })}</>
            : null}
        </div>

        {clip.has_file && (
          <div className="mt-3 flex gap-1.5">
            <Link to={`/clips/${clip.id}/edit`} className="btn-ghost btn-sm flex-1">
              <IconEdit className="w-3.5 h-3.5" />{t('clips.edit')}
            </Link>
            {clipIsPlayable(clip.status) && clip.has_file && <PublishButton clip={clip} />}
            <a href={api.clipDownloadUrl(clip.id)} className="btn-ghost btn-sm !px-2"
               aria-label={t('clips.download')}>
              <IconDownload className="w-3.5 h-3.5" />
            </a>
            <button onClick={onDelete} className="btn-ghost btn-sm !px-2" aria-label={t('clips.delete')}>
              <IconTrash className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
