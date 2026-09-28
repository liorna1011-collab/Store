// תוצאות: שורטים כרשת של נגנים, או סרטון ארוך אחד עם פרקים והסבר.

import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Check, ClipboardCopy, Download, FileArchive, Pencil, RotateCcw, Trash2, TriangleAlert,
} from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { clipIsPlayable, type Clip, type LongformChapter } from '../lib/types'
import { formatBytes, formatDuration } from '../lib/i18nFormat'
import {
  Badge, Button, Callout, Card, CardHeader, ConfirmModal, EmptyState, HelpTip, IconButton,
  Spinner, type Tone,
} from '../components/ds'

function statusTone(s: Clip['status']): Tone {
  return s === 'ready' ? 'ok' : s === 'needs_review' ? 'warn' : s === 'failed' ? 'bad' : 'neutral'
}

function QaNote({ clip }: { clip: Clip }) {
  const { t } = useTranslation()
  const qa = (clip.render_params?.qa || {}) as { findings?: { severity: string; message: string }[] }
  const audio = (clip.render_params?.audio || {}) as { issues?: string[] }
  const issues = [
    ...(qa.findings || []).filter((f) => f.severity === 'error').map((f) => f.message),
    ...(audio.issues || []),
  ]
  if (clip.status !== 'needs_review' || !issues.length) return null
  return (
    <Callout tone="warn" title={t('clips.needsReview')}>
      <ul className="list-disc ps-4 space-y-0.5">{issues.slice(0, 3).map((i) => <li key={i} dir="auto">{i}</li>)}</ul>
    </Callout>
  )
}

export function ClipCard({ clip, onDeleted }: { clip: Clip; onDeleted: (id: string) => void }) {
  const { t } = useTranslation()
  const { notifyError, pushToast } = useStore()
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const playable = clipIsPlayable(clip.status)
  const vertical = clip.height > clip.width
  const del = async () => {
    setBusy(true)
    try {
      await api.deleteClip(clip.id)
      pushToast({ tone: 'success', title: t('clips.deleted') })
      onDeleted(clip.id)
    } catch (e) { notifyError(e) } finally { setBusy(false); setConfirm(false) }
  }
  return (
    <Card className="overflow-hidden flex flex-col">
      <div className={`relative bg-black ${vertical ? 'aspect-[9/16]' : 'aspect-video'}`}>
        {playable && clip.has_file ? (
          <video controls preload="none" className="h-full w-full object-contain"
                 poster={clip.has_thumbnail ? api.clipThumbUrl(clip.id) : undefined}
                 src={api.clipFileUrl(clip.id)} aria-label={clip.title} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-ink-400">
            {clip.status === 'failed' ? <TriangleAlert className="w-6 h-6 text-bad" /> : <Spinner />}
            {t(`clips.status.${clip.status}`)}
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={statusTone(clip.status)}>{t(`clips.status.${clip.status}`)}</Badge>
          <Badge><span className="ltr-nums">{formatDuration(clip.duration)}</span></Badge>
          <Badge><span className="ltr-nums">{clip.aspect}</span></Badge>
          <span className="inline-flex items-center gap-1 text-xs text-ink-500">
            {t('clips.score', { score: Math.round(clip.score * 100) })}
            <HelpTip text={t('project.scoreDisclaimer')} />
          </span>
        </div>
        <h3 className="font-medium text-ink-100 leading-snug bidi-isolate">{clip.title}</h3>
        {clip.reason && <p className="text-xs text-ink-500 leading-relaxed line-clamp-3" dir="auto">{clip.reason}</p>}
        {clip.error && <p className="text-xs text-bad">{clip.error}</p>}
        <QaNote clip={clip} />
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
          <a href={api.clipDownloadUrl(clip.id)} aria-disabled={!playable}
             className={`btn-primary btn-sm ${playable ? '' : 'pointer-events-none opacity-50'}`}>
            <Download className="w-3.5 h-3.5" aria-hidden />{t('clips.download')}
          </a>
          {clip.subtitles_enabled && clip.cue_count > 0 && (
            <a href={api.clipSrtUrl(clip.id)} className="btn-ghost btn-sm">SRT</a>
          )}
          <Link to={`/clips/${clip.id}/edit`} className="btn-ghost btn-sm">
            <Pencil className="w-3.5 h-3.5" aria-hidden />{t('clips.edit')}
          </Link>
          <span className="ms-auto">
            <IconButton label={t('clips.delete')} icon={<Trash2 className="w-4 h-4" />} onClick={() => setConfirm(true)} />
          </span>
        </div>
        {clip.file_size > 0 && <div className="text-[11px] text-ink-500 ltr-nums">{formatBytes(clip.file_size)}</div>}
      </div>
      <ConfirmModal open={confirm} onClose={() => setConfirm(false)} onConfirm={del} busy={busy} danger
                    title={t('clips.deleteTitle')} body={t('clips.deleteBody')} confirmLabel={t('common.delete')} />
    </Card>
  )
}

export function ShortResults({ clips, onDeleted, onRegenerate }: {
  clips: Clip[]
  onDeleted: (id: string) => void
  onRegenerate: () => void
}) {
  const { t } = useTranslation()
  const { notifyError } = useStore()
  const [zipping, setZipping] = useState(false)
  const ready = clips.filter((c) => clipIsPlayable(c.status))
  if (!clips.length) {
    return <Card><EmptyState title={t('project.results.emptyTitle')} body={t('project.results.emptyBody')}
                             action={<Button onClick={onRegenerate} icon={<RotateCcw className="w-4 h-4" />}>{t('project.results.changeSettings')}</Button>} /></Card>
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-ink-400">{t('project.results.summary', { count: clips.length, ready: ready.length })}</div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={onRegenerate} icon={<RotateCcw className="w-4 h-4" />}>{t('project.results.changeSettings')}</Button>
          <Button variant="primary" loading={zipping} disabled={!ready.length}
                  icon={<FileArchive className="w-4 h-4" />}
                  onClick={async () => {
                    setZipping(true)
                    try { await api.downloadZip(ready.map((c) => c.id), true) } catch (e) { notifyError(e) }
                    finally { setZipping(false) }
                  }}>{t('project.results.downloadAll')}</Button>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {clips.map((c) => <ClipCard key={c.id} clip={c} onDeleted={onDeleted} />)}
      </div>
    </div>
  )
}

export function LongformResult({ clip, onRegenerate, onDeleted }: {
  clip: Clip
  onRegenerate: () => void
  onDeleted: (id: string) => void
}) {
  const { t } = useTranslation()
  const video = useRef<HTMLVideoElement>(null)
  const [copied, setCopied] = useState(false)
  const chapters = (clip.render_params?.chapters || []) as LongformChapter[]
  const explain = (clip.render_params?.longform_explain || []) as string[]
  const ytText = String(clip.render_params?.youtube_chapters || '')
  const playable = clipIsPlayable(clip.status)
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-4 min-w-0">
        <Card className="overflow-hidden">
          <div className="aspect-video bg-black">
            {playable ? <video ref={video} controls preload="metadata" className="h-full w-full"
                               poster={clip.has_thumbnail ? api.clipThumbUrl(clip.id) : undefined}
                               src={api.clipFileUrl(clip.id)} aria-label={clip.title} />
              : <div className="flex h-full items-center justify-center gap-2 text-ink-400"><Spinner />{t(`clips.status.${clip.status}`)}</div>}
          </div>
          <div className="p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone={statusTone(clip.status)}>{t(`clips.status.${clip.status}`)}</Badge>
              <Badge><span className="ltr-nums">{formatDuration(clip.duration)}</span></Badge>
            </div>
            <h3 className="font-semibold text-ink-100 bidi-isolate">{clip.title}</h3>
            <QaNote clip={clip} />
            <div className="flex flex-wrap gap-2">
              <a href={api.clipDownloadUrl(clip.id)} className={`btn-primary btn-sm ${playable ? '' : 'pointer-events-none opacity-50'}`}>
                <Download className="w-3.5 h-3.5" aria-hidden />{t('clips.download')}</a>
              {clip.cue_count > 0 && <a href={api.clipSrtUrl(clip.id)} className="btn-ghost btn-sm">SRT</a>}
              <Link to={`/clips/${clip.id}/edit`} className="btn-ghost btn-sm"><Pencil className="w-3.5 h-3.5" />{t('clips.edit')}</Link>
              <Button size="sm" onClick={onRegenerate} icon={<RotateCcw className="w-3.5 h-3.5" />}>{t('project.results.changeSettings')}</Button>
              <Button size="sm" variant="danger" onClick={async () => {
                await api.deleteClip(clip.id); onDeleted(clip.id)
              }} icon={<Trash2 className="w-3.5 h-3.5" />}>{t('clips.delete')}</Button>
            </div>
          </div>
        </Card>
        {explain.length > 0 && (
          <Card>
            <CardHeader title={t('project.results.howBuilt')} />
            <ul className="px-5 pb-5 space-y-1.5 text-sm text-ink-400 list-disc ps-9">
              {explain.map((l) => <li key={l} className="bidi-isolate">{l}</li>)}
            </ul>
          </Card>
        )}
      </div>
      <Card className="self-start">
        <CardHeader title={t('project.results.chapters')}
                    actions={ytText && (
                      <Button size="sm" variant="quiet" icon={copied ? <Check className="w-3.5 h-3.5" /> : <ClipboardCopy className="w-3.5 h-3.5" />}
                              onClick={async () => { await navigator.clipboard.writeText(ytText); setCopied(true); setTimeout(() => setCopied(false), 1800) }}>
                        {copied ? t('common.copied') : t('project.results.copyChapters')}
                      </Button>)} />
        {chapters.length ? (
          <ol className="px-3 pb-4 space-y-1">
            {chapters.map((c, i) => (
              <li key={`${c.start}-${i}`}>
                <button type="button" onClick={() => { if (video.current) { video.current.currentTime = c.start; void video.current.play() } }}
                        className="flex w-full items-start gap-3 rounded-lg px-2 py-2 text-start hover:bg-ink-800">
                  <span className="text-xs text-brand-600 ltr-nums shrink-0 mt-0.5">{formatDuration(c.start)}</span>
                  <span className="text-sm text-ink-300 bidi-isolate">{c.title}</span>
                </button>
              </li>
            ))}
          </ol>
        ) : <p className="px-5 pb-5 hint">{t('project.results.noChapters')}</p>}
      </Card>
    </div>
  )
}
