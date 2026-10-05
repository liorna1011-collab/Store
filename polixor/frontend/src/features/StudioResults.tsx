// Polixor Studio – the finished outputs of a project.
//
// Shorts and long-form in tabs. Only what Polixor believes is publish-ready is shown up
// front; clips that need attention, were not verified by the editor (no AI key), or
// failed are in a collapsed section; candidates the editor turned down are listed
// separately. Every video plays from the backend (/api/clips/<id>/file), never from a
// local path. QA mode (a per-browser switch) adds the review controls; ratings are
// saved to the project and can be exported as JSON.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  BadgeCheck, ClipboardCheck, Download, FileArchive, FileJson, Pencil, ThumbsDown, ThumbsUp, TriangleAlert,
} from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { useVisiblePoll } from '../lib/hooks'
import type { StudioClip, StudioGroup, StudioResults as Results, StudioReview } from '../lib/types'
import { formatDuration } from '../lib/i18nFormat'
import { Badge, Button, Callout, Card, EmptyState, Segmented, Skeleton, Spinner, Switch, cx } from '../components/ds'

const QA_KEY = 'polixor.qaMode'

function readQa(): boolean {
  try { return localStorage.getItem(QA_KEY) === '1' } catch { return false }
}

function writeQa(v: boolean) {
  try { localStorage.setItem(QA_KEY, v ? '1' : '0') } catch { /* private window */ }
}

type Choice<T extends string> = { value: T; label: string }

function Pick<T extends string>({ label, value, options, onPick }: {
  label: string; value: T | '' | undefined; options: Choice<T>[]; onPick: (v: T) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-ink-500 w-28 shrink-0">{label}</span>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value}
                onClick={() => onPick(o.value)}
                className={cx('rounded-full px-2.5 py-1 text-xs ring-1 ring-inset transition-colors',
                              value === o.value ? 'bg-brand-600 text-white ring-brand-600'
                                : 'text-ink-300 ring-ink-700 hover:bg-ink-800')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

function ReviewPanel({ clip, onSaved }: { clip: StudioClip; onSaved: (r: Partial<StudioReview>) => void }) {
  const { t } = useTranslation()
  const { notifyError } = useStore()
  const [note, setNote] = useState(clip.review.note || '')
  const save = async (patch: Partial<StudioReview>) => {
    try {
      const out = await api.saveReview(clip.id, patch)
      onSaved(out.review)
    } catch (e) { notifyError(e) }
  }
  const r = clip.review
  return (
    <div className="space-y-2 rounded-xl bg-ink-850 p-3 ring-1 ring-inset ring-ink-750" data-testid="qa-panel">
      <Pick label={t('creator.qa.post')} value={r.post} onPick={(v) => save({ post: v })}
            options={[{ value: 'yes', label: t('creator.qa.yes') }, { value: 'small_fix', label: t('creator.qa.smallFix') },
                      { value: 'no', label: t('creator.qa.no') }]} />
      <Pick label={t('creator.qa.hook')} value={r.hook} onPick={(v) => save({ hook: v })}
            options={[{ value: 'yes', label: t('creator.qa.yes') }, { value: 'no', label: t('creator.qa.no') }]} />
      <Pick label={t('creator.qa.story')} value={r.story} onPick={(v) => save({ story: v })}
            options={[{ value: 'yes', label: t('creator.qa.yes') }, { value: 'no', label: t('creator.qa.no') }]} />
      <Pick label={t('creator.qa.subtitles')} value={r.subtitles} onPick={(v) => save({ subtitles: v })}
            options={[{ value: 'good', label: t('creator.qa.good') }, { value: 'text', label: t('creator.qa.wrongText') },
                      { value: 'timing', label: t('creator.qa.timing') }]} />
      <Pick label={t('creator.qa.edit')} value={r.edit} onPick={(v) => save({ edit: v })}
            options={[{ value: 'good', label: t('creator.qa.good') }, { value: 'cut', label: t('creator.qa.badCut') },
                      { value: 'pacing', label: t('creator.qa.pacing') }, { value: 'framing', label: t('creator.qa.framing') },
                      { value: 'other', label: t('creator.qa.other') }]} />
      <textarea className="field min-h-[56px] text-sm" dir="auto" value={note} maxLength={2000}
                placeholder={t('creator.qa.note')} aria-label={t('creator.qa.note')}
                onChange={(e) => setNote(e.target.value)}
                onBlur={() => { if (note !== (r.note || '')) void save({ note }) }} />
    </div>
  )
}

function OutputCard({ clip, qa, onReview }: {
  clip: StudioClip; qa: boolean; onReview: (id: string, r: Partial<StudioReview>) => void
}) {
  const { t } = useTranslation()
  const { notifyError } = useStore()
  const vertical = clip.height > clip.width || clip.kind !== 'long'
  const playable = clip.group !== 'in_progress' && clip.group !== 'failed'
  const decide = async (decision: 'approved' | 'rejected' | '') => {
    try { onReview(clip.id, (await api.saveReview(clip.id, { decision })).review) } catch (e) { notifyError(e) }
  }
  const decided = clip.review.decision
  return (
    <Card className={cx('overflow-hidden flex flex-col', decided === 'rejected' && 'opacity-60')}
          data-testid="output-card" data-clip-id={clip.id}>
      <div className={cx('relative bg-black', vertical ? 'aspect-[9/16] max-h-[70vh] mx-auto w-full' : 'aspect-video')}>
        {playable ? (
          <video controls playsInline preload="none" className="h-full w-full object-contain"
                 poster={clip.media.thumbnail} src={clip.media.video} aria-label={clip.title} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-sm text-ink-400">
            {clip.group === 'failed' ? <TriangleAlert className="w-6 h-6 text-bad" /> : <Spinner />}
            {clip.group === 'failed' ? (clip.error || t('creator.failed')) : t('creator.making')}
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {clip.group === 'ready' && <Badge tone="ok" icon={<BadgeCheck className="w-3 h-3" />}>{t('creator.publishReady')}</Badge>}
          {clip.group === 'attention' && <Badge tone="warn">{t('creator.needsAttention')}</Badge>}
          {clip.group === 'unverified' && <Badge tone="warn">{t('creator.unverified')}</Badge>}
          {decided === 'approved' && <Badge tone="brand">{t('creator.approved')}</Badge>}
          {decided === 'rejected' && <Badge tone="bad">{t('creator.rejected')}</Badge>}
          <Badge><span className="ltr-nums">{formatDuration(clip.duration)}</span></Badge>
        </div>
        <h3 className="font-medium text-ink-100 leading-snug bidi-isolate">{clip.title}</h3>
        {clip.why && <p className="text-xs text-ink-400 leading-relaxed line-clamp-4" dir="auto">
          <span className="text-ink-500">{t('creator.why')} </span>{clip.why}</p>}
        {clip.group !== 'ready' && clip.publish.reasons.length > 0 && (
          <p className="text-xs text-warn" dir="auto">
            {clip.publish.reasons.map((r) => (r.startsWith('check:')
              ? t(`creator.diag.check.${r.slice(6)}`, { defaultValue: r.slice(6) })
              : t(`creator.reason.${r.split(':')[0]}`, { defaultValue: r }))).join(' · ')}
          </p>
        )}
        {clip.social.caption && (
          <p className="text-xs text-ink-500 bidi-isolate"><span>{t('creator.caption')} </span>{clip.social.caption}</p>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
          <a href={clip.media.download} className={cx('btn-primary btn-sm', !playable && 'pointer-events-none opacity-50')}
             aria-disabled={!playable}>
            <Download className="w-3.5 h-3.5" aria-hidden />{t('creator.download')}
          </a>
          <Button size="sm" variant={decided === 'approved' ? 'primary' : 'secondary'} disabled={!playable}
                  onClick={() => decide(decided === 'approved' ? '' : 'approved')}
                  icon={<ThumbsUp className="w-3.5 h-3.5" />}>{t('creator.approve')}</Button>
          <Button size="sm" variant="secondary" disabled={!playable}
                  onClick={() => decide(decided === 'rejected' ? '' : 'rejected')}
                  icon={<ThumbsDown className="w-3.5 h-3.5" />}>{t('creator.reject')}</Button>
          <Link to={`/clips/${clip.id}/edit`} className="btn-ghost btn-sm ms-auto">
            <Pencil className="w-3.5 h-3.5" aria-hidden />{t('creator.reEdit')}
          </Link>
        </div>
        {qa && playable && <ReviewPanel clip={clip} onSaved={(r) => onReview(clip.id, r)} />}
      </div>
    </Card>
  )
}

const MAIN: StudioGroup[] = ['ready', 'in_progress']

export default function StudioResults({ projectId, refreshKey, running }: {
  projectId: string; refreshKey?: string; running?: boolean
}) {
  const { t } = useTranslation()
  const { notifyError } = useStore()
  const [data, setData] = useState<Results | null>(null)
  const [tab, setTab] = useState<'shorts' | 'long'>('shorts')
  const [qa, setQa] = useState(readQa)

  const load = useCallback(async (quiet = false) => {
    try { setData(await api.studioResults(projectId)) } catch (e) { if (!quiet) notifyError(e) }
  }, [projectId, notifyError])
  useEffect(() => { void load() }, [load, refreshKey])
  // while the project runs: one poll at a time, only while the tab is visible, errors not toasted
  useVisiblePoll(() => load(true), 8000, Boolean(running))
  useEffect(() => {
    if (data && !data.shorts.length && data.long.length) setTab('long')
  }, [data])

  const onReview = (id: string, r: Partial<StudioReview>) => setData((d) => d && ({
    ...d,
    shorts: d.shorts.map((c) => (c.id === id ? { ...c, review: r } : c)),
    long: d.long.map((c) => (c.id === id ? { ...c, review: r } : c)),
  }))

  const list = useMemo(() => (data ? (tab === 'shorts' ? data.shorts : data.long) : []), [data, tab])
  if (!data) return <Skeleton className="h-72" />
  const main = list.filter((c) => MAIN.includes(c.group))
  const rest = list.filter((c) => !MAIN.includes(c.group))
  const s = data.summary
  return (
    <section className="space-y-4" aria-labelledby="results-h" data-testid="studio-results">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="results-h" className="text-lg font-semibold">{t('creator.resultsTitle')}</h2>
          <p className="text-sm text-ink-400">
            {t('creator.resultsSummary', { ready: s.publish_ready, made: data.shorts.length + data.long.length,
                                           shorts: data.shorts.length, long: data.long.length })}
            {s.profile?.profile && <> · {t('creator.profileUsed', {
              profile: t(`creator.profile.${s.profile.profile}`), source: t(`creator.profileSource.${s.profile.source || 'model'}`),
            })}</>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a className="btn-ghost btn-sm" href={api.studioDownloadUrl(projectId, 'shorts')}
             aria-disabled={!data.shorts.length}><FileArchive className="w-3.5 h-3.5" />{t('creator.downloadShorts')}</a>
          <a className="btn-ghost btn-sm" href={api.studioDownloadUrl(projectId, 'long')}
             aria-disabled={!data.long.length}><FileArchive className="w-3.5 h-3.5" />{t('creator.downloadLong')}</a>
          <a className="btn-primary btn-sm" href={api.studioDownloadUrl(projectId, 'package')}>
            <FileArchive className="w-3.5 h-3.5" />{t('creator.downloadPackage')}</a>
        </div>
      </div>

      {s.mode && s.mode !== 'semantic' && (
        <Callout tone="warn" title={t('creator.degradedTitle')}>{t('creator.degradedBody')}</Callout>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented<'shorts' | 'long'> value={tab} onChange={setTab} label={t('creator.tabs')}
                                      options={[
                                        { value: 'shorts', label: `${t('creator.shorts')} (${data.shorts.length})` },
                                        { value: 'long', label: `${t('creator.long')} (${data.long.length})` },
                                      ]} />
        <div className="flex items-center gap-3">
          <Switch checked={qa} onChange={(v) => { setQa(v); writeQa(v) }} label={t('creator.qa.mode')} />
          {qa && <a className="btn-ghost btn-sm" href={api.reviewsExportUrl(projectId)} data-testid="export-reviews">
            <FileJson className="w-3.5 h-3.5" />{t('creator.qa.export')}</a>}
        </div>
      </div>

      {main.length ? (
        <div className={cx('grid gap-4', tab === 'shorts' ? 'sm:grid-cols-2 lg:grid-cols-3' : 'lg:grid-cols-2')}>
          {main.map((c) => <OutputCard key={c.id} clip={c} qa={qa} onReview={onReview} />)}
        </div>
      ) : (
        <Card><EmptyState icon={<ClipboardCheck className="w-6 h-6" />}
                          title={running ? t('creator.workingTitle')
                            : s.mode && s.mode !== 'semantic' ? t('creator.noEditorTitle') : t('creator.noneReadyTitle')}
                          body={running ? t('creator.workingBody')
                            : s.mode && s.mode !== 'semantic' ? t('creator.noEditorBody') : t('creator.noneReadyBody')} /></Card>
      )}

      {rest.length > 0 && (
        <details className="card p-4" data-testid="other-outputs" open={!main.length || undefined}>
          <summary className="cursor-pointer text-sm font-medium text-ink-200">
            {t('creator.otherOutputs', { count: rest.length })}</summary>
          <p className="hint mt-2">{t('creator.otherOutputsHint')}</p>
          <div className={cx('mt-4 grid gap-4', tab === 'shorts' ? 'sm:grid-cols-2 lg:grid-cols-3' : 'lg:grid-cols-2')}>
            {rest.map((c) => <OutputCard key={c.id} clip={c} qa={qa} onReview={onReview} />)}
          </div>
        </details>
      )}

      {tab === 'shorts' && data.other_candidates.length > 0 && (
        <details className="card p-4" data-testid="other-candidates">
          <summary className="cursor-pointer text-sm font-medium text-ink-200">
            {t('creator.otherCandidates', { count: data.other_candidates.length })}</summary>
          <p className="hint mt-2">{t('creator.otherCandidatesHint')}</p>
          <ul className="mt-3 space-y-2 text-sm">
            {data.other_candidates.map((c, i) => (
              <li key={`${c.start}-${i}`} className="rounded-lg bg-ink-850 p-2.5">
                <span className="text-xs text-ink-500 ltr-nums me-2">{formatDuration(c.start)}–{formatDuration(c.end)}</span>
                <span className="text-ink-200 bidi-isolate">{c.title}</span>
                {c.reason && <div className="text-xs text-ink-500 mt-1" dir="auto">{c.reason}</div>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
