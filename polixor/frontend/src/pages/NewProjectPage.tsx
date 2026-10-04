// שלב 1 – Import: קובץ מהמחשב או קישור (YouTube, Twitch, Kick, Drive, קובץ ישיר).
// אחרי הייבוא הפרויקט נוצר והניתוח מתחיל מיד; ההמשך בעמוד הפרויקט.

import { useCallback, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { CloudUpload, FileVideo, Link2, Radio, Search, X } from 'lucide-react'
import { api, PolixorApiError } from '../lib/api'
import { useStore } from '../lib/store'
import type { ContentProfile, ProbeResult, QualityMode, ResolveResult, StudioGoal } from '../lib/types'
import { currentLang } from '../i18n'
import { formatBytes, formatDuration } from '../lib/i18nFormat'
import {
  Badge, Button, Callout, Card, CardHeader, Field, Input, PageHeader, ProgressBar, Segmented,
  Select, Stepper, Switch, cx,
} from '../components/ds'

type SourceTab = 'upload' | 'url'
const ACCEPT = '.mp4,.mkv,.mov,.webm,.avi,.m4v,.ts,.flv,video/*'

function parseClock(v: string): number | null {
  const parts = v.trim().split(':').map((x) => x.trim())
  if (!parts.length || parts.some((x) => !/^\d+(\.\d+)?$/.test(x))) return null
  return parts.reduce((acc, x) => acc * 60 + Number(x), 0)
}

export function ImportStepper({ current }: { current: number }) {
  const { t } = useTranslation()
  const keys = ['import', 'analyze', 'mode', 'settings', 'generate', 'results']
  return (
    <Stepper steps={keys.map((k, i) => ({
      key: k, label: t(`project.steps.${k}`),
      state: i < current ? 'done' : i === current ? 'current' : 'upcoming',
    }))} />
  )
}

export default function NewProjectPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { notifyError, pushToast } = useStore()
  const [tab, setTab] = useState<SourceTab>('upload')
  const [contentLang, setContentLang] = useState<'auto' | 'he' | 'en'>('auto')
  const [vocabulary, setVocabulary] = useState('')
  const [title, setTitle] = useState('')
  // Polixor Studio: what to make – generation starts by itself when the analysis is done
  const [goal, setGoal] = useState<StudioGoal | 'manual'>('package')
  const [profile, setProfile] = useState<ContentProfile>('auto')
  const [quality, setQuality] = useState<QualityMode>('premium')
  const [overlay, setOverlay] = useState(false)
  const [clipCount, setClipCount] = useState(8)
  const [clipLength, setClipLength] = useState<'short' | 'medium' | 'long'>('medium')

  // --- upload ---
  const [file, setFile] = useState<File | null>(null)
  const [drag, setDrag] = useState(false)
  const [uploadPct, setUploadPct] = useState<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // --- url ---
  const [url, setUrl] = useState('')
  const [checking, setChecking] = useState(false)
  const [resolved, setResolved] = useState<ResolveResult | null>(null)
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [urlError, setUrlError] = useState<{ message: string; hint: string } | null>(null)
  const [useSection, setUseSection] = useState(false)
  const [secStart, setSecStart] = useState('0:00')
  const [secEnd, setSecEnd] = useState('30:00')
  const [captureMin, setCaptureMin] = useState(30)
  const [creating, setCreating] = useState(false)

  const pickFile = (f: File | null | undefined) => {
    if (!f) return
    setFile(f)
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, ''))
  }

  const check = useCallback(async () => {
    const value = url.trim()
    if (!value) return
    setChecking(true)
    setUrlError(null)
    setResolved(null)
    setProbe(null)
    try {
      const r = await api.resolve(value)
      setResolved(r)
      try {
        const p = await api.probe(value)
        setProbe(p)
        if (p.needs_section) {
          setUseSection(true)
          setSecEnd(formatDuration(Math.min(p.duration, (p.max_source_hours || 12) * 3600)))
        } else if (p.duration) {
          setSecEnd(formatDuration(p.duration))
        }
        if (p.start_hint) setSecStart(formatDuration(p.start_hint))
        if (!title && p.title) setTitle(p.title)
      } catch (e) {
        // הקישור תקין מבנית אבל המידע המקדים נכשל (רשת, פרטי, DRM וכו')
        if (e instanceof PolixorApiError) setUrlError({ message: e.message, hint: e.hint })
        else throw e
      }
    } catch (e) {
      if (e instanceof PolixorApiError) setUrlError({ message: e.message, hint: e.hint })
      else notifyError(e)
    } finally {
      setChecking(false)
    }
  }, [url, title, notifyError])

  const isLive = Boolean(probe?.is_live || (resolved?.is_live && resolved?.live_certain))
  const sectionStart = parseClock(secStart)
  const sectionEnd = parseClock(secEnd)
  const sectionValid = !useSection || (sectionStart !== null && sectionEnd !== null
    && sectionEnd - sectionStart >= 5)

  const create = async () => {
    setCreating(true)
    try {
      let source: Parameters<typeof api.createProject>[0]['source']
      let preview: Record<string, unknown> | null = null
      if (tab === 'upload') {
        if (!file) return
        abortRef.current = new AbortController()
        setUploadPct(0)
        const up = await api.upload(file, setUploadPct, abortRef.current.signal)
        source = { type: 'upload', upload_token: up.upload_token }
      } else {
        source = {
          type: 'url', url: url.trim(),
          section: useSection && !isLive && sectionStart !== null && sectionEnd !== null
            ? { start: sectionStart, end: sectionEnd } : null,
          live_capture_seconds: isLive ? captureMin * 60 : null,
        }
        if (probe) {
          preview = {
            title: probe.title, duration: probe.duration, platform: probe.platform,
            uploader: probe.uploader, is_live: probe.is_live,
            thumbnail: probe.thumbnail?.startsWith('https://') ? probe.thumbnail : undefined,
          }
        }
      }
      const project = await api.createProject({
        source, title: title.trim(), ui_language: currentLang(), content_language: contentLang,
        preview, vocabulary: vocabulary.trim() || undefined,
        goal: goal === 'manual' ? null : goal, content_profile: profile, quality,
        editorial_overlay: overlay, clip_count: clipCount, clip_length: clipLength,
      })
      pushToast({ tone: 'success', title: t('import.created') })
      navigate(`/projects/${project.id}`)
    } catch (e) {
      if (!(e instanceof PolixorApiError && e.code === 'aborted')) notifyError(e)
    } finally {
      setCreating(false)
      setUploadPct(null)
      abortRef.current = null
    }
  }

  const startLiveMonitor = async () => {
    setCreating(true)
    try {
      const job = await api.createJob({ url: url.trim(), live_mode: true, title: title.trim() })
      navigate(`/legacy/jobs/${job.id}`)
    } catch (e) {
      notifyError(e)
    } finally {
      setCreating(false)
    }
  }

  const canCreate = tab === 'upload' ? Boolean(file)
    : Boolean(resolved) && !urlError && sectionValid
  const uploading = uploadPct !== null

  return (
    <>
      <PageHeader title={t('import.title')} subtitle={t('import.subtitle')} />
      <div className="mb-6"><ImportStepper current={0} /></div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card>
          <div className="px-5 pt-5">
            <Segmented<SourceTab> value={tab} onChange={(v) => setTab(v)} label={t('import.sourceType')}
                                  options={[
                                    { value: 'upload', label: <span className="inline-flex items-center gap-1.5"><CloudUpload className="w-4 h-4" />{t('import.tabs.upload')}</span> },
                                    { value: 'url', label: <span className="inline-flex items-center gap-1.5"><Link2 className="w-4 h-4" />{t('import.tabs.url')}</span> },
                                  ]} />
          </div>

          {tab === 'upload' && (
            <div className="p-5 space-y-4">
              <div
                onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); pickFile(e.dataTransfer.files?.[0]) }}
                className={cx('rounded-xl border-2 border-dashed p-8 text-center transition-colors',
                              drag ? 'border-brand-500 bg-brand-600/5' : 'border-ink-700 bg-ink-900')}>
                <CloudUpload className="mx-auto w-10 h-10 text-brand-600" aria-hidden />
                <p className="mt-3 text-sm font-medium text-ink-200">{t('import.drop.title')}</p>
                <p className="mt-1 hint">{t('import.drop.formats')}</p>
                <Button className="mt-4" onClick={() => inputRef.current?.click()} disabled={uploading}>
                  {t('import.drop.choose')}
                </Button>
                <input ref={inputRef} type="file" accept={ACCEPT} className="sr-only" tabIndex={-1}
                       onChange={(e) => pickFile(e.target.files?.[0])} />
              </div>
              {file && (
                <div className="flex items-center gap-3 rounded-xl bg-ink-800/60 p-3 ring-1 ring-inset ring-ink-750">
                  <FileVideo className="w-5 h-5 text-brand-600 shrink-0" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-ink-100 bidi-isolate">{file.name}</div>
                    <div className="text-xs text-ink-500 ltr-nums">{formatBytes(file.size)}</div>
                    {uploading && <div className="mt-2"><ProgressBar value={uploadPct ?? 0} label={t('import.uploading')} /></div>}
                  </div>
                  {uploading
                    ? <Button size="sm" onClick={() => abortRef.current?.abort()}>{t('common.cancel')}</Button>
                    : <button type="button" onClick={() => setFile(null)} aria-label={t('import.removeFile')}
                              className="btn-quiet !p-1.5"><X className="w-4 h-4" /></button>}
                </div>
              )}
            </div>
          )}

          {tab === 'url' && (
            <div className="p-5 space-y-4">
              <Field label={t('import.url.label')} hint={t('import.url.hint')} htmlFor="src-url">
                <div className="flex gap-2">
                  <Input id="src-url" dir="ltr" value={url} placeholder="https://www.youtube.com/watch?v=…"
                         onChange={(e) => { setUrl(e.target.value); setResolved(null); setProbe(null); setUrlError(null) }}
                         onKeyDown={(e) => { if (e.key === 'Enter') void check() }} />
                  <Button onClick={check} loading={checking} disabled={!url.trim()}
                          icon={<Search className="w-4 h-4" />}>{t('import.url.check')}</Button>
                </div>
              </Field>

              {urlError && <Callout tone="bad" title={urlError.message}>{urlError.hint}</Callout>}

              {resolved && (
                <div className="flex gap-4 rounded-xl bg-ink-800/50 p-3 ring-1 ring-inset ring-ink-750">
                  {probe?.thumbnail?.startsWith('https://') && (
                    <img src={probe.thumbnail} alt="" className="w-40 aspect-video rounded-lg object-cover shrink-0" />
                  )}
                  <div className="min-w-0 space-y-1.5">
                    <div className="flex flex-wrap gap-1.5">
                      <Badge tone="brand">{t(`project.platform.${resolved.platform}`, { defaultValue: resolved.platform })}</Badge>
                      {isLive && <Badge tone="bad" icon={<Radio className="w-3 h-3" />}>{t('import.live')}</Badge>}
                      {probe?.duration ? <Badge><span className="ltr-nums">{formatDuration(probe.duration)}</span></Badge> : null}
                    </div>
                    {probe?.title && <div className="font-medium text-ink-100 bidi-isolate">{probe.title}</div>}
                    {probe?.uploader && <div className="text-xs text-ink-500 bidi-isolate">{probe.uploader}</div>}
                    {!probe && !urlError && <div className="text-xs text-ink-500">{t('import.url.noPreview')}</div>}
                    {(probe?.notes || resolved.notes).map((n) => <p key={n} className="hint">{n}</p>)}
                  </div>
                </div>
              )}

              {resolved && isLive && (
                <Card className="p-4 space-y-3">
                  <div className="text-sm font-medium text-ink-100">{t('import.liveCapture.title')}</div>
                  <p className="hint">{t('import.liveCapture.body')}</p>
                  <Field label={t('import.liveCapture.minutes')} htmlFor="cap-min">
                    <Input id="cap-min" type="number" min={1} max={360} value={captureMin} className="max-w-[8rem]"
                           onChange={(e) => setCaptureMin(Math.max(1, Math.min(360, Number(e.target.value) || 1)))} />
                  </Field>
                  <div className="border-t border-ink-750 pt-3">
                    <p className="hint mb-2">{t('import.liveMonitor.body')}</p>
                    <Button size="sm" onClick={startLiveMonitor} disabled={creating}
                            icon={<Radio className="w-4 h-4" />}>{t('import.liveMonitor.start')}</Button>
                  </div>
                </Card>
              )}

              {resolved && !isLive && (
                <div className="space-y-3">
                  <Switch checked={useSection} onChange={setUseSection} label={t('import.section.toggle')}
                          description={probe?.needs_section
                            ? t('import.section.required', { hours: probe.max_source_hours ?? 12 })
                            : t('import.section.hint')}
                          disabled={probe?.needs_section} />
                  {useSection && (
                    <div className="grid grid-cols-2 gap-3 max-w-sm">
                      <Field label={t('import.section.start')} htmlFor="sec-s">
                        <Input id="sec-s" dir="ltr" value={secStart} onChange={(e) => setSecStart(e.target.value)} />
                      </Field>
                      <Field label={t('import.section.end')} htmlFor="sec-e"
                             error={!sectionValid ? t('import.section.invalid') : undefined}>
                        <Input id="sec-e" dir="ltr" value={secEnd} onChange={(e) => setSecEnd(e.target.value)} />
                      </Field>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title={t('import.details')} />
            <div className="px-5 pb-5 space-y-4">
              <Field label={t('import.projectTitle')} htmlFor="p-title" hint={t('import.projectTitleHint')}>
                <Input id="p-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
              </Field>
              <Field label={t('import.contentLanguage')} htmlFor="p-lang" hint={t('import.contentLanguageHint')}>
                <Select id="p-lang" value={contentLang}
                        onChange={(e) => setContentLang(e.target.value as 'auto' | 'he' | 'en')}>
                  <option value="auto">{t('common.contentLanguage.auto')}</option>
                  <option value="he">{t('common.contentLanguage.he')}</option>
                  <option value="en">{t('common.contentLanguage.en')}</option>
                </Select>
              </Field>
              <Field label={t('creator.goal.label')} htmlFor="p-goal" hint={t(`creator.goal.${goal}Hint`)}>
                <Select id="p-goal" value={goal} data-testid="goal"
                        onChange={(e) => setGoal(e.target.value as StudioGoal | 'manual')}>
                  <option value="package">{t('creator.goal.package')}</option>
                  <option value="short">{t('creator.goal.short')}</option>
                  <option value="longform">{t('creator.goal.longform')}</option>
                  <option value="manual">{t('creator.goal.manual')}</option>
                </Select>
              </Field>
              <details className="rounded-xl bg-ink-850 p-3 ring-1 ring-inset ring-ink-750">
                <summary className="cursor-pointer text-sm font-medium text-ink-200">{t('creator.advanced')}</summary>
                <div className="mt-3 space-y-3">
                  <Field label={t('creator.profileLabel')} htmlFor="p-profile" hint={t('creator.profileHint')}>
                    <Select id="p-profile" value={profile} onChange={(e) => setProfile(e.target.value as ContentProfile)}>
                      {(['auto', 'livestream', 'podcast', 'news', 'solo', 'general'] as ContentProfile[]).map((v) => (
                        <option key={v} value={v}>{t(`creator.profile.${v}`)}</option>))}
                    </Select>
                  </Field>
                  <Field label={t('creator.qualityLabel')} htmlFor="p-quality" hint={t(`creator.quality.${quality}Hint`)}>
                    <Select id="p-quality" value={quality} onChange={(e) => setQuality(e.target.value as QualityMode)}>
                      <option value="premium">{t('creator.quality.premium')}</option>
                      <option value="fast">{t('creator.quality.fast')}</option>
                    </Select>
                  </Field>
                  {goal !== 'longform' && (
                    <div className="grid grid-cols-2 gap-3">
                      <Field label={t('creator.clipCount')} htmlFor="p-count" hint={t('creator.clipCountHint')}>
                        <Input id="p-count" type="number" min={1} max={20} value={clipCount}
                               onChange={(e) => setClipCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
                      </Field>
                      <Field label={t('creator.clipLength')} htmlFor="p-len">
                        <Select id="p-len" value={clipLength}
                                onChange={(e) => setClipLength(e.target.value as 'short' | 'medium' | 'long')}>
                          <option value="short">{t('creator.length.short')}</option>
                          <option value="medium">{t('creator.length.medium')}</option>
                          <option value="long">{t('creator.length.long')}</option>
                        </Select>
                      </Field>
                    </div>
                  )}
                  <Switch checked={overlay} onChange={setOverlay} label={t('creator.overlay')}
                          description={t('creator.overlayHint')} />
                </div>
              </details>
              <Field label={t('import.vocabulary')} htmlFor="p-vocab" hint={t('import.vocabularyHint')}>
                <textarea id="p-vocab" className="field min-h-[72px]" dir="auto" value={vocabulary}
                          maxLength={4000} placeholder={t('import.vocabularyPlaceholder')}
                          onChange={(e) => setVocabulary(e.target.value)} />
              </Field>
              <Button variant="primary" size="lg" className="w-full" disabled={!canCreate || creating}
                      loading={creating} onClick={create}>
                {uploading ? t('import.uploading') : goal === 'manual' ? t('import.submit') : t('creator.start')}
              </Button>
              <p className="hint">{goal === 'manual' ? t('import.nextHint') : t('creator.startHint')}</p>
            </div>
          </Card>
          <Callout tone="neutral" title={t('import.rights.title')}>{t('import.rights.body')}</Callout>
        </div>
      </div>
    </>
  )
}
