/**
 * סטודיו תמונות AI – שיחה כמו ב-ChatGPT: כותבים, מצרפים תמונות ייחוס,
 * מקבלים תמונה וממשיכים לבקש שינויים עליה. כל תמונה היא נכס רגיל:
 * מורידים, משתמשים בה כייחוס, או מוסיפים לסרטון (אינטרו, אאוטרו,
 * B-roll, שכבה, רקע, תמונת שער).
 *
 * המפתח של OpenAI לא מגיע לדפדפן: הכול עובר דרך השרת. הממשק מציג רק
 * את מה שהמודל הפעיל תומך בו (שכבת היכולות בשרת).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ImagePlus, MessageSquarePlus, Paperclip, Send, Trash2, X, PanelLeft } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import type { Clip, GeneratedImage, ImageAspect, ImageRole, StudioCaps, StudioMessage, StudioThread, StudioThreadSummary } from '../lib/types'
import { Badge, Button, Modal, Select, Spinner } from './ds'
import { AspectPicker, DURATION_PRESETS, OriginBadge, ROLE_OPTIONS } from './images'

const BUSY = new Set(['queued', 'generating'])

export default function ImageStudio({ jobId }: { jobId: string }) {
  const { t } = useTranslation()
  const { notifyError, pushToast, subscribe } = useStore()
  const [caps, setCaps] = useState<StudioCaps | null>(null)
  const [threads, setThreads] = useState<StudioThreadSummary[]>([])
  const [thread, setThread] = useState<StudioThread | null>(null)
  const [showList, setShowList] = useState(false)
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<GeneratedImage[]>([])
  const [aspect, setAspect] = useState<ImageAspect>('9:16')
  const [mode, setMode] = useState<'auto' | 'new' | 'edit'>('auto')
  const [background, setBackground] = useState('auto')
  const [sending, setSending] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [placing, setPlacing] = useState<GeneratedImage | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const loadThreads = useCallback(async () => {
    try {
      const r = await api.studioThreads(jobId || undefined)
      setThreads(r.threads)
      return r.threads
    } catch (e) { notifyError(e); return [] }
  }, [jobId, notifyError])

  const open = useCallback(async (id: string) => {
    try {
      setThread(await api.studioThread(id))
      setShowList(false)
      setMode('auto')
    } catch (e) { notifyError(e) }
  }, [notifyError])

  useEffect(() => {
    api.studioCaps().then(setCaps).catch((e) => notifyError(e))
    void loadThreads().then((list) => { if (list[0]) void open(list[0].id) })
  }, [loadThreads, open, notifyError])

  // עדכון תמונות שעדיין נוצרות: WebSocket, ורשת ביטחון של בדיקה כל 2.5 שניות
  const refreshImage = useCallback((id: string) => {
    api.getImage(id).then((img) => setThread((prev) => {
      if (!prev || !prev.images[id]) return prev
      return { ...prev, images: { ...prev.images, [id]: img } }
    })).catch(() => undefined)
  }, [])
  useEffect(() => subscribe((ev) => {
    if (!ev.type.startsWith('image.')) return
    const id = ev.data?.image_id as string | undefined
    if (id) refreshImage(id)
  }), [subscribe, refreshImage])
  const pending = useMemo(() => Object.values(thread?.images || {})
    .filter((i) => BUSY.has(i.status)).map((i) => i.id).join(','), [thread])
  useEffect(() => {
    if (!pending) return
    const timer = setInterval(() => pending.split(',').forEach(refreshImage), 2500)
    return () => clearInterval(timer)
  }, [pending, refreshImage])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [thread?.messages.length])

  const latestImage = useMemo(() => {
    const msgs = thread?.messages || []
    for (let i = msgs.length - 1; i >= 0; i--) {
      const img = thread?.images[msgs[i].image_id]
      if (msgs[i].role === 'assistant' && img?.status === 'ready') return img
    }
    return null
  }, [thread])

  const maxRefs = caps?.model.max_refs ?? 0
  const canAttach = Boolean(caps?.model.edit && maxRefs > 0)
  const refSlots = maxRefs - (latestImage && mode !== 'new' ? 1 : 0)
  const blocked = Boolean(caps && !caps.ready)

  // שיחה חדשה נשמרת רק עם ההודעה הראשונה – כך לא נשארות שיחות ריקות
  const newThread = () => {
    setThread(null)
    setShowList(false)
    setAttachments([])
    setText('')
    setMode('auto')
  }

  const send = async () => {
    const body = text.trim()
    if (!body || sending || blocked) return
    setSending(true)
    try {
      let th = thread
      if (!th) {
        const created = await api.studioCreateThread(jobId || undefined)
        th = { ...created, images: {} }
      }
      const r = await api.studioSend(th.id, {
        text: body, attachments: attachments.map((a) => a.id),
        aspect: mode === 'new' || !latestImage ? aspect : '', mode, background,
      })
      const next: StudioThread = {
        ...th, title: th.title || body.slice(0, 80),
        messages: [...th.messages, r.user, r.assistant],
        images: { ...th.images, ...r.images },
      }
      setThread(next)
      setText('')
      setAttachments([])
      setMode('auto')
      void loadThreads()
    } catch (e) {
      notifyError(e, t('studio.sendFailed'))
    } finally {
      setSending(false)
    }
  }

  const retry = async (m: StudioMessage) => {
    try {
      const r = await api.studioRetry(m.id)
      setThread((prev) => prev && { ...prev, images: { ...prev.images, ...r.images } })
    } catch (e) { notifyError(e) }
  }

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return
    const room = Math.max(0, refSlots - attachments.length)
    const list = Array.from(files).slice(0, room)
    if (files.length > room) {
      pushToast({ tone: 'warn', title: t('studio.tooManyRefs', { max: maxRefs }) })
    }
    setUploading(true)
    try {
      for (const f of list) {
        if (f.size > (caps?.max_upload_mb ?? 20) * 1024 * 1024) {
          pushToast({ tone: 'error', title: t('studio.fileTooBig', { name: f.name, mb: caps?.max_upload_mb ?? 20 }) })
          continue
        }
        const img = await api.studioUpload(f, jobId || undefined)
        setAttachments((prev) => [...prev, img])
      }
    } catch (e) {
      notifyError(e, t('studio.uploadFailed'))
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const useAsRef = (img: GeneratedImage) => {
    if (!canAttach) return
    if (attachments.some((a) => a.id === img.id)) return
    if (attachments.length >= refSlots) {
      pushToast({ tone: 'warn', title: t('studio.tooManyRefs', { max: maxRefs }) })
      return
    }
    setAttachments((prev) => [...prev, img])
  }

  const removeThread = async () => {
    if (!thread) return
    try {
      await api.studioDelete(thread.id)
      setThread(null)
      setConfirmDelete(false)
      const list = await loadThreads()
      if (list[0]) void open(list[0].id)
    } catch (e) { notifyError(e) }
  }

  const modeOptions = [
    { value: 'auto', label: latestImage ? t('studio.mode.editLatest') : t('studio.mode.create') },
    ...(latestImage ? [{ value: 'new', label: t('studio.mode.new') }] : []),
  ]

  return (
    <div className="grid gap-4 md:grid-cols-[230px_minmax(0,1fr)] min-w-0" data-testid="image-studio">
      {/* ---- שיחות ---- */}
      <aside className={`${showList ? 'block' : 'hidden'} md:block min-w-0`} data-testid="studio-threads">
        <Button variant="primary" size="sm" className="w-full" onClick={newThread}
                icon={<MessageSquarePlus className="w-4 h-4" />} data-testid="studio-new">
          {t('studio.newChat')}
        </Button>
        <div className="mt-3 space-y-1 max-h-[60vh] overflow-y-auto">
          {threads.length === 0 && <p className="hint px-1">{t('studio.noChats')}</p>}
          {threads.map((th) => (
            <button key={th.id} type="button" onClick={() => void open(th.id)}
                    data-testid="studio-thread"
                    className={`w-full flex items-center gap-2 rounded-lg px-2 py-1.5 text-start text-sm transition-colors
                                ${thread?.id === th.id ? 'bg-brand-600/10 text-brand-600' : 'text-ink-300 hover:bg-ink-800'}`}>
              {th.cover?.has_file
                ? <img src={api.imageThumbUrl(th.cover.id)} alt="" className="w-8 h-8 rounded object-cover shrink-0" />
                : <span className="w-8 h-8 rounded bg-ink-800 shrink-0" />}
              <span className="truncate" dir="auto">{th.title || t('studio.untitled')}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* ---- שיחה ---- */}
      <section className="card flex flex-col min-h-[62vh] min-w-0">
        <div className="flex items-center gap-2 border-b border-ink-750 px-3 py-2">
          <button type="button" className="btn-quiet !p-1.5 md:hidden" onClick={() => setShowList((v) => !v)}
                  aria-label={t('studio.chats')} data-testid="studio-toggle-list">
            <PanelLeft className="w-4 h-4" />
          </button>
          <h2 className="text-sm font-semibold truncate flex-1" dir="auto">
            {thread?.title || t('studio.newChatTitle')}
          </h2>
          {caps && (
            <Badge tone={caps.is_ai ? 'brand' : 'warn'}>
              {caps.is_ai ? caps.model.id : t('images.notAi')}
            </Badge>
          )}
          {thread && thread.messages.length > 0 && (
            <button type="button" className="btn-quiet !p-1.5" onClick={() => setConfirmDelete(true)}
                    aria-label={t('studio.deleteChat')} title={t('studio.deleteChat')}>
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-4 space-y-4" data-testid="studio-messages">
          {(!thread || thread.messages.length === 0) && (
            <div className="text-center text-sm text-ink-400 py-10 px-4">
              <ImagePlus className="w-8 h-8 mx-auto text-ink-500 mb-3" />
              <p>{t('studio.emptyTitle')}</p>
              <p className="hint mt-1">{t('studio.emptyBody')}</p>
            </div>
          )}
          {thread?.messages.map((m) => m.role === 'user' ? (
            <div key={m.id} className="flex justify-end" data-testid="studio-user-msg">
              <div className="max-w-[85%] rounded-2xl bg-brand-600/10 px-3 py-2 text-sm text-ink-100">
                <p className="whitespace-pre-wrap break-words" dir="auto">{m.text}</p>
                {m.attachments.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {m.attachments.map((id) => thread.images[id] && (
                      <img key={id} src={api.imageThumbUrl(id)} alt="" className="w-12 h-12 rounded object-cover" />
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <AssistantMessage key={m.id} message={m} image={thread.images[m.image_id]}
                              isLatest={thread.images[m.image_id]?.id === latestImage?.id}
                              canAttach={canAttach}
                              onRetry={() => void retry(m)} onUseAsRef={useAsRef}
                              onPlace={setPlacing} />
          ))}
          <div ref={endRef} />
        </div>

        {/* ---- כתיבה ---- */}
        <div className="border-t border-ink-750 p-3 space-y-2" data-testid="studio-composer">
          {blocked && <p className="text-xs text-warn">{caps?.reason}</p>}
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2" data-testid="studio-attachments">
              {attachments.map((a) => (
                <span key={a.id} className="relative">
                  <img src={api.imageThumbUrl(a.id)} alt="" className="w-14 h-14 rounded-lg object-cover" />
                  <button type="button" onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                          aria-label={t('studio.removeRef')}
                          className="absolute -top-1.5 -end-1.5 rounded-full bg-ink-900 border border-ink-700 p-0.5">
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea className="field min-h-[72px] resize-y" dir="auto" value={text} disabled={blocked}
                    maxLength={caps?.max_prompt ?? 1800} id="img-prompt" data-testid="studio-input"
                    placeholder={latestImage && mode !== 'new' ? t('studio.placeholderEdit') : t('studio.placeholder')}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send() }} />
          <div className="flex flex-wrap items-center gap-2">
            {canAttach && (
              <>
                <input ref={fileRef} type="file" className="hidden" multiple data-testid="studio-file"
                       accept={(caps?.upload_types || []).join(',')}
                       onChange={(e) => void onFiles(e.target.files)} />
                <Button size="sm" variant="secondary" loading={uploading} disabled={blocked || attachments.length >= refSlots}
                        onClick={() => fileRef.current?.click()} icon={<Paperclip className="w-4 h-4" />}
                        data-testid="studio-attach">
                  {t('studio.attach')}
                </Button>
              </>
            )}
            {latestImage ? (
              <Select value={mode} onChange={(e) => setMode(e.target.value as 'auto' | 'new')}
                      className="w-auto py-1 text-xs" aria-label={t('studio.mode.label')} data-testid="studio-mode">
                {modeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            ) : null}
            {(mode === 'new' || !latestImage) && (
              <AspectPicker value={aspect} onChange={setAspect} disabled={blocked} />
            )}
            {caps && caps.backgrounds.includes('transparent') && (
              <Select value={background} onChange={(e) => setBackground(e.target.value)}
                      className="w-auto py-1 text-xs" aria-label={t('studio.background.label')}
                      data-testid="studio-background">
                {caps.backgrounds.map((b) => (
                  <option key={b} value={b}>{t(`studio.background.${b}`)}</option>
                ))}
              </Select>
            )}
            <Button variant="primary" size="sm" className="ms-auto" loading={sending}
                    disabled={blocked || !text.trim()} onClick={() => void send()}
                    icon={<Send className="w-4 h-4" />} data-testid="studio-send">
              {t('studio.send')}
            </Button>
          </div>
          {canAttach && <p className="hint">{t('studio.refsHint', { max: maxRefs })}</p>}
        </div>
      </section>

      <AddToVideoModal image={placing} jobId={jobId} onClose={() => setPlacing(null)} />
      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title={t('studio.deleteChat')} size="sm"
             footer={<>
               <Button variant="secondary" onClick={() => setConfirmDelete(false)}>{t('common.cancel')}</Button>
               <Button variant="danger" onClick={() => void removeThread()}>{t('common.delete')}</Button>
             </>}>
        <p className="text-sm text-ink-300">{t('studio.deleteBody')}</p>
      </Modal>
    </div>
  )
}

function AssistantMessage({ message, image, isLatest, canAttach, onRetry, onUseAsRef, onPlace }: {
  message: StudioMessage
  image?: GeneratedImage
  isLatest: boolean
  canAttach: boolean
  onRetry: () => void
  onUseAsRef: (img: GeneratedImage) => void
  onPlace: (img: GeneratedImage) => void
}) {
  const { t } = useTranslation()
  const busy = !image || BUSY.has(image.status)
  return (
    <div className="flex" data-testid="studio-assistant-msg" data-status={image?.status || 'queued'}>
      <div className="max-w-[92%] sm:max-w-[75%] space-y-2">
        <div className="text-xs text-ink-500">
          {message.mode === 'edit' ? t('studio.edited') : t('studio.created')}
        </div>
        {busy ? (
          <div className="flex items-center gap-2 rounded-xl bg-ink-900 px-4 py-6 text-sm text-ink-300">
            <Spinner /> {image?.status === 'generating' ? t('images.status.generating') : t('images.status.queued')}
          </div>
        ) : image.status === 'ready' && image.has_file ? (
          <a href={api.imageFileUrl(image.id)} target="_blank" rel="noreferrer">
            <img src={api.imageFileUrl(image.id)} alt={image.prompt} data-testid="studio-image"
                 className="rounded-xl max-h-[46vh] w-auto max-w-full object-contain bg-ink-950" />
          </a>
        ) : (
          <div className="rounded-xl border border-bad/30 bg-bad/5 px-3 py-2 text-sm" data-testid="studio-error">
            <p className="text-bad">{image.error || t('images.status.failed')}</p>
            <Button size="sm" variant="secondary" className="mt-2" onClick={onRetry}>{t('common.retry')}</Button>
          </div>
        )}
        {image?.status === 'ready' && (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="studio-actions">
            <OriginBadge image={image} />
            {isLatest && <Badge tone="brand">{t('studio.current')}</Badge>}
            <Button size="sm" variant="primary" onClick={() => onPlace(image)} data-testid="studio-place">
              {t('studio.addToVideo')}
            </Button>
            {canAttach && (
              <Button size="sm" variant="secondary" onClick={() => onUseAsRef(image)} data-testid="studio-use-ref">
                {t('studio.useAsRef')}
              </Button>
            )}
            <a className="btn-ghost btn-sm" href={api.imageDownloadUrl(image.id)}>{t('clips.download')}</a>
          </div>
        )}
        {image?.note && <p className="text-xs text-warn" dir="auto">{image.note}</p>}
      </div>
    </div>
  )
}

function AddToVideoModal({ image, jobId, onClose }: {
  image: GeneratedImage | null
  jobId: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { notifyError, pushToast } = useStore()
  const [clips, setClips] = useState<Clip[] | null>(null)
  const [clipId, setClipId] = useState('')
  const [role, setRole] = useState<ImageRole>('intro')
  const [duration, setDuration] = useState(3)
  const [atTime, setAtTime] = useState(0)
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState<string>('')

  useEffect(() => {
    if (!image) return
    setDone('')
    api.listClips(jobId || image.job_id || undefined)
      .then((list) => {
        const ready = list.filter((c) => c.status === 'ready' || c.status === 'needs_review')
        setClips(ready)
        setClipId((prev) => prev && ready.some((c) => c.id === prev) ? prev : (ready[0]?.id || ''))
      })
      .catch((e) => { setClips([]); notifyError(e) })
  }, [image, jobId, notifyError])

  const roleInfo = ROLE_OPTIONS.find((r) => r.value === role)
  const needsTime = role === 'insert' || role === 'broll' || role === 'overlay'
  const clip = clips?.find((c) => c.id === clipId)

  const submit = async () => {
    if (!image || !clipId) return
    setSaving(true)
    try {
      if (role === 'thumbnail') {
        await api.studioThumbnail(clipId, image.id)
      } else {
        await api.addPlacement(clipId, {
          image_id: image.id, role,
          at_time: needsTime ? atTime : 0,
          duration: roleInfo?.needsDuration ? duration : 0,
          scale: role === 'overlay' ? 0.32 : 1.0,
          position: role === 'overlay' ? 'top_right' : 'center',
        })
      }
      setDone(clipId)
      pushToast({ tone: 'success', title: t('images.clip.placed'),
                  body: role === 'thumbnail' ? t('studio.thumbSet') : t('images.clip.placedBody') })
    } catch (e) {
      notifyError(e, t('images.clip.placeFailed'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={Boolean(image)} onClose={onClose} title={t('studio.addToVideo')} size="lg"
           footer={done ? (
             <>
               <Button variant="secondary" onClick={onClose}>{t('common.close')}</Button>
               <Link className="btn-primary" to={`/clips/${done}/edit`}>{t('studio.openClip')}</Link>
             </>
           ) : (
             <>
               <Button variant="secondary" onClick={onClose}>{t('common.cancel')}</Button>
               <Button variant="primary" loading={saving} disabled={!clipId} onClick={() => void submit()}
                       data-testid="studio-place-confirm">{t('images.clip.placeShort')}</Button>
             </>
           )}>
      {clips === null ? <Spinner /> : clips.length === 0 ? (
        <p className="text-sm text-ink-300">{t('studio.noClips')}</p>
      ) : (
        <div className="space-y-4" data-testid="studio-place-modal">
          <label className="block">
            <span className="label">{t('studio.chooseClip')}</span>
            <Select value={clipId} onChange={(e) => setClipId(e.target.value)} data-testid="studio-place-clip">
              {clips.map((c) => <option key={c.id} value={c.id}>{c.title || c.id}</option>)}
            </Select>
          </label>
          <div>
            <div className="label">{t('images.clip.howToPlace')}</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {ROLE_OPTIONS.map((r) => (
                <button key={r.value} type="button" onClick={() => setRole(r.value)} aria-pressed={role === r.value}
                        data-testid={`studio-role-${r.value}`}
                        className={`rounded-lg border px-3 py-2 text-start transition-colors
                                    ${role === r.value
                                      ? 'border-brand-500 bg-brand-600/10 text-brand-600'
                                      : 'border-ink-700 bg-ink-900 text-ink-400 hover:border-ink-600'}`}>
                  <div className="text-xs font-medium">{r.label}</div>
                </button>
              ))}
            </div>
            {roleInfo && <p className="hint mt-2">{role === 'thumbnail' ? t('studio.thumbHint') : roleInfo.hint}</p>}
          </div>
          {roleInfo?.needsDuration && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="label mb-0">{t('images.clip.duration')}</span>
              {DURATION_PRESETS.map((d) => (
                <button key={d} type="button" onClick={() => setDuration(d)} aria-pressed={duration === d}
                        className={`btn btn-sm ltr-nums ${duration === d
                          ? 'bg-brand-600/10 text-brand-600 ring-1 ring-brand-500/40'
                          : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-ink-100'}`}>
                  {d.toFixed(1)}s
                </button>
              ))}
            </div>
          )}
          {needsTime && (
            <label className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-ink-400">{t('images.clip.entryPoint')}</span>
              <input type="number" min={0} max={clip?.duration || 0} step={0.5}
                     className="field w-24 py-1 text-xs ltr-nums" value={atTime}
                     onChange={(e) => setAtTime(Math.max(0, Number(e.target.value) || 0))} />
              <span className="hint">{t('images.clip.entryHint')}</span>
            </label>
          )}
          {done && <p className="text-sm text-ok" data-testid="studio-placed">{t('studio.placedNote')}</p>}
        </div>
      )}
    </Modal>
  )
}
