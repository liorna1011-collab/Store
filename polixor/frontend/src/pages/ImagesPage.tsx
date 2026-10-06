/**
 * AI Images – יצירת תמונות וגלריית הפרויקט.
 *
 * כל יצירה עוברת דרך השרת. המפתח אינו מגיע לדפדפן בשום שלב, וגם
 * מצב "אין מפתח" מוצג כמצב חסום אמיתי ולא ככפתור שנראה פעיל.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '../components/ds'
import { api } from '../lib/api'
import { useJobs, useStore } from '../lib/store'
import { formatBytes } from '../lib/format'
import type {
  GeneratedImage, ImageAspect, ImageProvidersResponse, Job,
} from '../lib/types'
import {
  Chip, ConfirmDialog, EmptyState, IconAlert, IconDownload, IconEdit, IconFilm,
  IconPlay, IconRefresh, IconTrash, Modal, Spinner,
} from '../components/ui'
import ImageStudio from '../components/image-studio'
import {
  AspectPicker, ElapsedTimer, GeneratingPulse, ImageStatusOverlay, OriginBadge,
  ProviderBanner,
} from '../components/images'

const EXAMPLES = [
  'Create a cinematic dark forest at night with a campfire, realistic',
  'A lonely empty road at night, single street lamp, moody, film grain',
  'Close-up of a mechanical keyboard lit by a monitor, shallow depth of field',
  'A triumphant esports player, confetti, stage lighting, dramatic',
]

export default function ImagesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const { pushToast, notifyError, subscribe } = useStore()
  const { jobs } = useJobs()

  const jobId = params.get('job') ?? ''
  const tab = params.get('tab') === 'gallery' ? 'gallery' : 'studio'
  const setTab = (next: 'studio' | 'gallery') => {
    const p = new URLSearchParams(params)
    if (next === 'studio') p.delete('tab'); else p.set('tab', next)
    setParams(p)
  }
  const [providers, setProviders] = useState<ImageProvidersResponse | null>(null)
  const [images, setImages] = useState<GeneratedImage[]>([])
  const [loading, setLoading] = useState(true)

  const [prompt, setPrompt] = useState('')
  const [aspect, setAspect] = useState<ImageAspect>('9:16')
  const [submitting, setSubmitting] = useState(false)
  const [startedAt, setStartedAt] = useState<Record<string, number>>({})

  const [preview, setPreview] = useState<GeneratedImage | null>(null)
  const [editing, setEditing] = useState<GeneratedImage | null>(null)
  const [editText, setEditText] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<GeneratedImage | null>(null)
  const [deleting, setDeleting] = useState(false)
  const promptRef = useRef<HTMLTextAreaElement>(null)

  const load = useCallback(async () => {
    try {
      const [p, list] = await Promise.all([
        api.imageProviders(),
        api.listImages(jobId || undefined),
      ])
      setProviders(p)
      setImages(list)
    } catch (e) {
      notifyError(e, t('images.page.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [jobId, notifyError, t])

  useEffect(() => { void load() }, [load])

  // אירוע מה-WebSocket => רענון השורה הרלוונטית בלבד
  useEffect(() => subscribe((ev) => {
    if (!ev.type.startsWith('image.')) return
    const id = ev.data?.image_id as string | undefined
    if (!id) return
    api.getImage(id)
      .then((img) => setImages((prev) => {
        const idx = prev.findIndex((x) => x.id === id)
        if (idx < 0) return [img, ...prev]
        const next = [...prev]
        next[idx] = img
        return next
      }))
      .catch(() => undefined)
  }), [subscribe])

  // רשת ביטחון: אם ה-WebSocket מנותק, בודקים תמונות שעדיין רצות
  const pendingIds = useMemo(
    () => images.filter((i) => i.status === 'queued' || i.status === 'generating')
      .map((i) => i.id).join(','),
    [images])

  useEffect(() => {
    if (!pendingIds) return
    const t = setInterval(() => {
      pendingIds.split(',').forEach((id) => {
        api.getImage(id)
          .then((img) => setImages((prev) =>
            prev.map((x) => (x.id === id ? img : x))))
          .catch(() => undefined)
      })
    }, 2500)
    return () => clearInterval(t)
  }, [pendingIds])

  const upsert = useCallback((img: GeneratedImage) => {
    setImages((prev) => [img, ...prev.filter((x) => x.id !== img.id)])
    setStartedAt((prev) => ({ ...prev, [img.id]: Date.now() }))
  }, [])

  const generate = useCallback(async () => {
    const text = prompt.trim()
    if (!text) return
    setSubmitting(true)
    try {
      const img = await api.createImage({
        prompt: text, aspect, job_id: jobId || undefined,
      })
      upsert(img)
      setPrompt('')
    } catch (e) {
      notifyError(e, t('images.status.failed'))
    } finally {
      setSubmitting(false)
    }
  }, [prompt, aspect, jobId, upsert, notifyError, t])

  const act = useCallback(async (
    fn: () => Promise<GeneratedImage>,
    label: string,
  ) => {
    try {
      const next = await fn()
      upsert(next)
      pushToast({ tone: 'info', title: label, body: t('images.page.onTheWay') })
    } catch (e) {
      notifyError(e, t('images.page.actionFailed', { action: label }))
    }
  }, [upsert, pushToast, notifyError, t])

  const remove = useCallback(async () => {
    if (!confirmDelete) return
    setDeleting(true)
    try {
      await api.deleteImage(confirmDelete.id)
      setImages((prev) => prev.filter((x) => x.id !== confirmDelete.id))
      if (preview?.id === confirmDelete.id) setPreview(null)
      pushToast({ tone: 'success', title: t('images.page.deleted') })
    } catch (e) {
      notifyError(e, t('images.page.deleteFailed'))
    } finally {
      setDeleting(false)
      setConfirmDelete(null)
    }
  }, [confirmDelete, preview, pushToast, notifyError, t])

  const jobOptions = useMemo(
    () => jobs.filter((j: Job) => j.status === 'completed' || j.status === 'running'),
    [jobs])

  const blocked = Boolean(providers && !providers.ready)
  const activeJob = jobs.find((j: Job) => j.id === jobId)

  return (
    <div>
      <PageHeader title={t('images.page.title')} subtitle={t('images.page.subtitle')} />

      <div className="mb-5">
        <ProviderBanner providers={providers}
                        onOpenSettings={() => navigate('/settings?tab=images')} />
      </div>

      <div className="mb-4 flex gap-1 border-b border-ink-750" role="tablist">
        {(['studio', 'gallery'] as const).map((k) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} data-testid={`images-tab-${k}`}
                  onClick={() => { setTab(k); if (k === 'gallery') void load() }}
                  className={`px-3 py-2 text-sm -mb-px border-b-2 transition-colors
                              ${tab === k ? 'border-brand-500 text-ink-100' : 'border-transparent text-ink-400 hover:text-ink-200'}`}>
            {k === 'studio' ? t('studio.tab') : t('studio.galleryTab')}
          </button>
        ))}
      </div>

      {tab === 'studio' ? <ImageStudio jobId={jobId} /> : (<>
      {/* ---- יצירה ---- */}
      <section className="card-pad">
        <div className="flex items-start justify-between gap-4 mb-3">
          <label className="label mb-0" htmlFor="img-prompt">{t('images.page.prompt')}</label>
          {jobOptions.length > 0 && (
            <select
              className="field w-auto py-1 text-xs" aria-label={t('images.page.library')}
              value={jobId}
              onChange={(e) => {
                const v = e.target.value
                setParams(v ? { job: v, tab: 'gallery' } : { tab: 'gallery' })
              }}
            >
              <option value="">{t('images.page.generalLibrary')}</option>
              {jobOptions.map((j: Job) => (
                <option key={j.id} value={j.id}>{j.title || j.id}</option>
              ))}
            </select>
          )}
        </div>

        <textarea
          id="img-prompt"
          ref={promptRef}
          className="field min-h-[86px] resize-y"
          dir="auto"
          placeholder="Create a cinematic dark forest at night with a campfire, realistic"
          value={prompt}
          disabled={blocked}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void generate()
          }}
        />

        <div className="mt-2 flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" disabled={blocked}
                    className="chip bg-ink-800 text-ink-400 hover:text-ink-100
                               hover:bg-ink-700 transition-colors max-w-full
                               disabled:opacity-40"
                    onClick={() => { setPrompt(ex); promptRef.current?.focus() }}>
              <span className="truncate" dir="ltr">{ex.slice(0, 46)}…</span>
            </button>
          ))}
        </div>

        <div className="mt-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <span className="label">{t('editor.export.aspect')}</span>
            <AspectPicker value={aspect} onChange={setAspect} disabled={blocked} />
          </div>
          <div className="flex items-center gap-3">
            <span className="hint hidden sm:block">{t('images.page.shortcut')}</span>
            <button className="btn-primary" disabled={blocked || submitting || !prompt.trim()}
                    onClick={() => void generate()}>
              {submitting ? <Spinner /> : null}
              {submitting ? t('images.page.sending') : t('images.suggest.create')}
            </button>
          </div>
        </div>

        {activeJob && (
          <p className="hint mt-3">
            {t('images.page.savedTo', { project: activeJob.title || activeJob.id })}
          </p>
        )}
      </section>

      {/* ---- גלריה ---- */}
      <div className="mt-8 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-100">
          {t('images.page.gallery')}
          {images.length > 0 && (
            <span className="text-ink-500 font-normal ltr-nums"> · {images.length}</span>
          )}
        </h2>
        <button className="btn-ghost btn-sm" onClick={() => void load()}>
          <IconRefresh className="w-3.5 h-3.5" />
          {t('common.refresh')}
        </button>
      </div>

      {loading ? (
        <div className="mt-4 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 items-start">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton aspect-[3/4] rounded-xl" />
          ))}
        </div>
      ) : images.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            icon={<IconFilm className="w-7 h-7" />}
            title={t('images.page.emptyTitle')}
            body={t('images.page.emptyBody')}
          />
        </div>
      ) : (
        <div className="mt-4 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 items-start">
          {images.map((img) => (
            <ImageCard
              key={img.id}
              image={img}
              startedAt={startedAt[img.id]}
              onPreview={() => setPreview(img)}
              onRegenerate={() => void act(() => api.regenerateImage(img.id), t('images.page.regenerate'))}
              onVary={() => void act(() => api.varyImage(img.id), t('images.page.variation'))}
              onEdit={() => { setEditing(img); setEditText(img.prompt) }}
              onDelete={() => setConfirmDelete(img)}
              onCancel={() => { void api.cancelImage(img.id).catch((e) => notifyError(e)) }}
              onUse={() => navigate(`/clips${img.job_id ? `?job=${img.job_id}` : ''}`)}
            />
          ))}
        </div>
      )}

      </>)}

      {/* ---- תצוגה מלאה ---- */}
      <Modal open={Boolean(preview)} onClose={() => setPreview(null)}
             title={preview?.is_ai ? t('images.page.generatedImage') : t('images.page.localCard')} wide>
        {preview && (
          <div className="space-y-4">
            <img src={api.imageFileUrl(preview.id)} alt={preview.prompt}
                 className="w-full max-h-[58vh] object-contain rounded-lg bg-ink-950" />
            <div className="flex flex-wrap items-center gap-2">
              <OriginBadge image={preview} />
              <Chip>{preview.aspect}</Chip>
              <span className="text-[11px] text-ink-500 ltr-nums">
                {preview.width}×{preview.height}
              </span>
              <span className="text-[11px] text-ink-500">
                · {formatBytes(preview.file_size)}
              </span>
            </div>
            {!preview.is_ai && preview.note && (
              <p className="text-xs text-warn leading-relaxed">{preview.note}</p>
            )}
            <div>
              <div className="label">{t('images.page.promptLabel')}</div>
              <p className="text-sm text-ink-200 leading-relaxed" dir="auto">
                {preview.prompt}
              </p>
            </div>
            {preview.revised_prompt && preview.revised_prompt !== preview.prompt && (
              <div>
                <div className="label">{t('images.page.revisedPrompt')}</div>
                <p className="text-xs text-ink-400 leading-relaxed" dir="auto">
                  {preview.revised_prompt}
                </p>
              </div>
            )}
            <div className="flex flex-wrap gap-2 pt-1">
              <a className="btn-ghost btn-sm" href={api.imageDownloadUrl(preview.id)}>
                <IconDownload className="w-3.5 h-3.5" />
                {t('clips.download')}
              </a>
              <button className="btn-ghost btn-sm"
                      onClick={() => { setEditing(preview); setEditText(preview.prompt); setPreview(null) }}>
                <IconEdit className="w-3.5 h-3.5" />
                {t('images.page.editPrompt')}
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* ---- עריכת פרומפט ---- */}
      <Modal open={Boolean(editing)} onClose={() => setEditing(null)}
             title={t('images.page.editPromptTitle')}>
        <p className="hint mb-3">{t('images.page.editPromptHint')}</p>
        <textarea className="field min-h-[110px] resize-y" dir="auto"
                  value={editText} onChange={(e) => setEditText(e.target.value)} />
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setEditing(null)}>{t('common.cancel')}</button>
          <button className="btn-primary"
                  disabled={!editText.trim() || editText.trim() === editing?.prompt}
                  onClick={() => {
                    const target = editing
                    if (!target) return
                    setEditing(null)
                    void act(() => api.editImagePrompt(target.id, editText.trim()),
                             t('images.page.editPrompt'))
                  }}>
            {t('images.page.createWithNew')}
          </button>
        </div>
      </Modal>

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        title={t('images.page.deleteTitle')}
        body={t('images.page.deleteBody')}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmDelete(null)}
        busy={deleting}
      />
    </div>
  )
}

function ImageCard({
  image, startedAt, onPreview, onRegenerate, onVary, onEdit, onDelete, onCancel, onUse,
}: {
  image: GeneratedImage
  startedAt?: number
  onPreview: () => void
  onRegenerate: () => void
  onVary: () => void
  onEdit: () => void
  onDelete: () => void
  onCancel: () => void
  onUse: () => void
}) {
  const { t } = useTranslation()
  const busy = image.status === 'queued' || image.status === 'generating'

  return (
    <div className="card overflow-hidden group animate-fade-up flex flex-col">
      {/*
        יחס אחיד לכל האריחים; היחס האמיתי מוצג כתגית ובתצוגה המלאה.
        התמונה ממוקמת absolute כדי שגובהה הטבעי לא יגבר על היחס
        (min-height:auto של פריט flex מנצח aspect-ratio).
      */}
      <div className="relative aspect-[4/5] overflow-hidden bg-ink-900">
        {image.has_file ? (
          <button type="button" onClick={onPreview} className="absolute inset-0" aria-label={t('images.page.openPreview')}>
            <img
              src={image.thumb_url ? api.imageThumbUrl(image.id) : api.imageFileUrl(image.id)}
              alt={image.prompt}
              loading="lazy"
              className="absolute inset-0 w-full h-full object-cover
                         transition-transform duration-300 group-hover:scale-[1.03]"
            />
          </button>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            {busy ? <GeneratingPulse /> : <IconAlert className="w-6 h-6 text-ink-500" />}
          </div>
        )}

        <ImageStatusOverlay image={image} onCancel={busy ? onCancel : undefined} />

        {image.status === 'ready' && (
          <div className="absolute top-2 start-2 end-2 flex items-start
                          justify-between gap-1.5">
            <OriginBadge image={image} />
            <span className="chip bg-ink-950/75 text-ink-300 ltr-nums backdrop-blur-sm">
              {image.aspect}
            </span>
          </div>
        )}
        {busy && startedAt && (
          <div className="absolute bottom-2 start-2">
            <ElapsedTimer since={startedAt} active />
          </div>
        )}
      </div>

      <div className="p-3 flex-1 flex flex-col">
        <p className="text-[11px] text-ink-400 leading-relaxed line-clamp-2 flex-1"
           dir="auto" title={image.prompt}>
          {image.prompt}
        </p>

        {image.status === 'ready' && (
          <div className="mt-2.5 flex flex-wrap gap-1">
            <IconButton title={t('images.page.useInVideo')} onClick={onUse}>
              <IconPlay className="w-3.5 h-3.5" />
            </IconButton>
            <a className="btn-ghost btn-sm px-2" title={t('clips.download')} aria-label={t('clips.download')}
               href={api.imageDownloadUrl(image.id)}>
              <IconDownload className="w-3.5 h-3.5" />
            </a>
            <IconButton title={t('images.page.regenerate')} onClick={onRegenerate}>
              <IconRefresh className="w-3.5 h-3.5" />
            </IconButton>
            <IconButton title={t('images.page.variation')} onClick={onVary}>
              <span className="text-[11px] leading-none px-0.5">V</span>
            </IconButton>
            <IconButton title={t('images.page.editPrompt')} onClick={onEdit}>
              <IconEdit className="w-3.5 h-3.5" />
            </IconButton>
            <IconButton title={t('common.delete')} onClick={onDelete} danger>
              <IconTrash className="w-3.5 h-3.5" />
            </IconButton>
          </div>
        )}

        {(image.status === 'failed' || image.status === 'cancelled') && (
          <div className="mt-2.5 flex gap-1">
            <IconButton title={t('common.retry')} onClick={onRegenerate}>
              <IconRefresh className="w-3.5 h-3.5" />
            </IconButton>
            <IconButton title={t('images.page.editPrompt')} onClick={onEdit}>
              <IconEdit className="w-3.5 h-3.5" />
            </IconButton>
            <IconButton title={t('common.delete')} onClick={onDelete} danger>
              <IconTrash className="w-3.5 h-3.5" />
            </IconButton>
          </div>
        )}
      </div>
    </div>
  )
}

function IconButton({ children, title, onClick, danger }: {
  children: React.ReactNode
  title: string
  onClick: () => void
  danger?: boolean
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className={`btn-sm inline-flex items-center justify-center rounded-md border
                  px-2 py-1.5 transition-colors
                  ${danger
                    ? 'border-bad/30 text-bad hover:bg-bad/15'
                    : 'border-ink-700 bg-ink-800 text-ink-400 hover:text-ink-100 hover:bg-ink-700'}`}
    >
      {children}
    </button>
  )
}
