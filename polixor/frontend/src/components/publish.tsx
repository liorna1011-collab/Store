// חלון "פרסום": סרטון מוכן → בחירת חשבונות → פרסם עכשיו / תזמן.
// אותה זרימה לקליפים קצרים ולסרטונים ארוכים. בדיקה מוקדמת בשרת לפני השליחה
// (כותרת, אורך, פורמט, פרטיות, חשבון שצריך חיבור מחדש, מועד בעבר), והסבר
// ברור מי מפרסם בזמן המתוזמן – הפלטפורמה, או Polixor (ואז השרת חייב לפעול).

import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, CheckCircle2, Send } from 'lucide-react'
import { api, PolixorApiError } from '../lib/api'
import { useStore } from '../lib/store'
import type { Clip, PreflightResult, PublishPlatform, PublishTargetIn, SocialAccount } from '../lib/types'
import { Badge, Button, Callout, Field, Input, Modal, Segmented, Select, Spinner, cx } from './ds'

type ClipLike = Pick<Clip, 'id' | 'title' | 'description' | 'kind' | 'width' | 'height'>

function localInputValue(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

export function PublishDialog({ clip, open, onClose }: { clip: ClipLike; open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const { pushToast, notifyError } = useStore()
  const [accounts, setAccounts] = useState<SocialAccount[] | null>(null)
  const [platforms, setPlatforms] = useState<PublishPlatform[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [title, setTitle] = useState(clip.title || '')
  const [description, setDescription] = useState(clip.description || '')
  const [tags, setTags] = useState('')
  const [privacy, setPrivacy] = useState<Record<string, string>>({})
  // הצהרות שפלטפורמות דורשות (YouTube: מיועד לילדים, תוכן שנוצר/שונה ב-AI)
  const [madeForKids, setMadeForKids] = useState(false)
  const [synthetic, setSynthetic] = useState(false)
  // כריכה ב-Instagram: רגע מתוך הסרטון (ה-API תומך ב-thumb_offset; אין העלאת תמונה מקומית)
  const [coverAt, setCoverAt] = useState<string>('')
  const [mode, setMode] = useState<'now' | 'schedule'>('now')
  const [when, setWhen] = useState(() => localInputValue(new Date(Date.now() + 2 * 3600e3)))
  const [check, setCheck] = useState<PreflightResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!open) return
    setDone(false)
    Promise.all([api.publishAccounts(), api.publishPlatforms()]).then(([a, p]) => {
      setAccounts(a.accounts)
      setPlatforms(p.platforms)
      setSelected((prev) => prev.length ? prev
        : a.accounts.filter((x) => x.status === 'connected').slice(0, 1).map((x) => x.id))
    }).catch((e) => { setAccounts([]); notifyError(e) })
  }, [open, notifyError])

  const platformOf = (id: string) => platforms.find((p) => p.id === id)
  const scheduleIso = useMemo(() => {
    if (mode !== 'schedule' || !when) return null
    const d = new Date(when)                              // שעה מקומית של הדפדפן
    return Number.isNaN(d.getTime()) ? null : d.toISOString()
  }, [mode, when])

  const targets: PublishTargetIn[] = useMemo(() => selected.map((id) => {
    const acc = accounts?.find((a) => a.id === id)
    const caps = acc ? platformOf(acc.platform)?.capabilities : null
    return {
      account_id: id, title: title.trim(), description,
      tags: tags.split(',').map((x) => x.trim()).filter(Boolean),
      privacy: privacy[id] || caps?.privacy[0] || 'public',
      options: {
        ...(caps?.notes.includes('made_for_kids') ? { made_for_kids: madeForKids, synthetic_media: synthetic } : {}),
        ...(caps?.notes.includes('cover_frame') && coverAt !== '' ? { cover_frame_seconds: Number(coverAt) } : {}),
      },
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [selected, title, description, tags, privacy, accounts, platforms, madeForKids, synthetic, coverAt])

  // בדיקה מוקדמת בכל שינוי (עם השהיה קצרה)
  useEffect(() => {
    if (!open || !accounts) return
    setChecking(true)
    const timer = setTimeout(() => {
      api.publishPreflight({ clip_id: clip.id, targets, mode, schedule_at: scheduleIso })
        .then(setCheck).catch(() => setCheck(null)).finally(() => setChecking(false))
    }, 350)
    return () => clearTimeout(timer)
  }, [open, accounts, clip.id, targets, mode, scheduleIso])

  const submit = async () => {
    setBusy(true)
    try {
      await api.publish({ clip_id: clip.id, targets, mode, schedule_at: scheduleIso })
      setDone(true)
      pushToast({ tone: 'success', title: t('publishing.dialog.done'), body: t('publishing.dialog.doneBody') })
    } catch (e) {
      if (e instanceof PolixorApiError && e.data?.preflight) setCheck(e.data.preflight as PreflightResult)
      else notifyError(e)
    } finally {
      setBusy(false)
    }
  }

  const connected = (accounts || []).filter((a) => a.platform)
  const issuesFor = (id: string) => check?.targets.find((x) => x.account_id === id)?.issues || []
  const byTarget = (id: string) => check?.targets.find((x) => x.account_id === id)
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const fmt = check?.format || ((clip.height > clip.width || clip.kind === 'short') ? 'short' : 'long')

  return (
    <Modal open={open} onClose={onClose} size="lg"
           title={t('publishing.dialog.title', { title: clip.title || '—' })}
           footer={done ? (
             <>
               <Link to="/publishing" className="btn-primary" onClick={onClose}>{t('publishing.dialog.open')}</Link>
             </>
           ) : (
             <>
               <Button variant="secondary" onClick={onClose}>{t('common.cancel')}</Button>
               <Button variant="primary" onClick={submit} disabled={checking || !check?.ok}
                       loading={busy} icon={<Send className="w-4 h-4" />} data-testid="publish-submit">
                 {mode === 'now' ? t('publishing.dialog.submitNow') : t('publishing.dialog.submitSchedule')}
               </Button>
             </>
           )}>
      {done ? (
        <Callout tone="ok" title={t('publishing.dialog.done')}>{t('publishing.dialog.doneBody')}</Callout>
      ) : accounts === null ? (
        <div className="py-8 flex justify-center"><Spinner /></div>
      ) : (
        <div className="space-y-5" data-testid="publish-dialog">
          <Badge tone="neutral">{t(`publishing.dialog.format.${fmt}`)}</Badge>

          <Field label={t('publishing.dialog.accounts')}>
            {connected.length === 0 ? (
              <div className="text-sm text-ink-400">
                {t('publishing.dialog.noAccounts')}{' '}
                <Link to="/publishing?tab=accounts" className="text-brand-500 underline" onClick={onClose}>
                  {t('publishing.dialog.connectFirst')}
                </Link>
              </div>
            ) : (
              <ul className="space-y-2">
                {connected.map((a) => {
                  const p = platformOf(a.platform)
                  const on = selected.includes(a.id)
                  const issues = on ? issuesFor(a.id) : []
                  return (
                    <li key={a.id} className={cx('rounded-lg border p-3', on ? 'border-brand-500/50' : 'border-ink-750')}>
                      <label className="flex items-center gap-3 cursor-pointer">
                        <input type="checkbox" className="accent-brand-500 w-4 h-4" checked={on}
                               data-testid={`publish-account-${a.id}`}
                               onChange={(e) => setSelected((prev) => e.target.checked
                                 ? [...prev, a.id] : prev.filter((x) => x !== a.id))} />
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium text-ink-100 truncate">
                            {p?.name || a.platform} · {a.display_name || a.handle}
                          </span>
                          {a.status !== 'connected' && (
                            <span className="text-xs text-warn">{a.status_label}</span>
                          )}
                          {p?.sandbox && <span className="block text-xs text-ink-500">{t('publishing.dialog.sandboxNote')}</span>}
                          {a.linked_page && <span className="block text-xs text-ink-500">{t('publishing.accounts.linkedPage', { page: a.linked_page })}</span>}
                        </span>
                        {on && p?.capabilities && p.capabilities.privacy.length > 1 && (
                          <Select aria-label={t('publishing.dialog.privacy')} className="!w-auto"
                                  value={privacy[a.id] || p.capabilities.privacy[0]}
                                  onChange={(e) => setPrivacy((prev) => ({ ...prev, [a.id]: e.target.value }))}>
                            {p.capabilities.privacy.map((v) => (
                              <option key={v} value={v}>{t(`publishing.privacy.${v}`, { defaultValue: v })}</option>
                            ))}
                          </Select>
                        )}
                      </label>
                      {on && (byTarget(a.id)?.warnings || []).length > 0 && (
                        <ul className="mt-2 space-y-1 text-xs text-warn" data-testid="publish-warnings">
                          {(byTarget(a.id)?.warnings || []).map((w) => (
                            <li key={w.key} className="flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />{w.text}</li>
                          ))}
                        </ul>
                      )}
                      {issues.length > 0 && (
                        <ul className="mt-2 space-y-1 text-xs text-bad">
                          {issues.map((i) => <li key={i.key} className="flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />{i.text}</li>)}
                        </ul>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </Field>

          <Field label={t('publishing.dialog.titleField')}>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} dir="auto" maxLength={2200}
                   data-testid="publish-title" />
          </Field>
          <Field label={t('publishing.dialog.description')}>
            <textarea className="field min-h-[90px]" dir="auto" value={description}
                      onChange={(e) => setDescription(e.target.value)} maxLength={10000} />
          </Field>
          <Field label={t('publishing.dialog.tags')} hint={t('publishing.dialog.tagsHint')}>
            <Input value={tags} onChange={(e) => setTags(e.target.value)} dir="auto" />
          </Field>

          {selected.some((id) => platformOf(accounts.find((a) => a.id === id)?.platform || '')
            ?.capabilities?.notes.includes('made_for_kids')) && (
            <div className="space-y-2" data-testid="publish-declarations">
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input type="checkbox" className="accent-brand-500 w-4 h-4 mt-0.5" checked={madeForKids}
                       onChange={(e) => setMadeForKids(e.target.checked)} />
                <span>{t('publishing.dialog.madeForKids')}
                  <span className="block text-xs text-ink-500">{t('publishing.dialog.madeForKidsHint')}</span></span>
              </label>
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input type="checkbox" className="accent-brand-500 w-4 h-4 mt-0.5" checked={synthetic}
                       data-testid="publish-synthetic" onChange={(e) => setSynthetic(e.target.checked)} />
                <span>{t('publishing.dialog.synthetic')}
                  <span className="block text-xs text-ink-500">{t('publishing.dialog.syntheticHint')}</span></span>
              </label>
            </div>
          )}

          {selected.some((id) => platformOf(accounts.find((a) => a.id === id)?.platform || '')
            ?.capabilities?.notes.includes('cover_frame')) && (
            <Field label={t('publishing.dialog.coverAt')} hint={t('publishing.dialog.coverAtHint')}>
              <Input type="number" min={0} step={0.5} inputMode="decimal" className="ltr-nums !w-32"
                     value={coverAt} onChange={(e) => setCoverAt(e.target.value)} data-testid="publish-cover" />
            </Field>
          )}

          <Field label={t('publishing.dialog.when')}>
            <Segmented value={mode} onChange={(v) => setMode(v)} label={t('publishing.dialog.when')}
                       options={[{ value: 'now', label: t('publishing.dialog.now') },
                                 { value: 'schedule', label: t('publishing.dialog.schedule') }]} />
          </Field>
          {mode === 'schedule' && (
            <Field label={t('publishing.dialog.scheduleAt')} hint={t('publishing.dialog.yourTimezone', { tz })}>
              <Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)}
                     data-testid="publish-when" className="ltr-nums" />
            </Field>
          )}
          {mode === 'schedule' && selected.map((id) => {
            const x = byTarget(id)
            const acc = accounts.find((a) => a.id === id)
            const name = platformOf(acc?.platform || '')?.name || acc?.platform || ''
            if (!x?.schedule_by) return null
            return (
              <Callout key={id} tone={x.schedule_by === 'polixor' ? 'warn' : 'neutral'}>
                {x.schedule_by === 'polixor'
                  ? t('publishing.dialog.onPolixor', { platform: name })
                  : t('publishing.dialog.onPlatform', { platform: name })}
              </Callout>
            )
          })}

          <div className="text-sm" aria-live="polite">
            {checking ? (
              <span className="text-ink-400 inline-flex items-center gap-2"><Spinner />{t('publishing.dialog.checking')}</span>
            ) : check?.ok ? (
              <span className="text-ok inline-flex items-center gap-2" data-testid="publish-ready">
                <CheckCircle2 className="w-4 h-4" />{t('publishing.dialog.ready')}
              </span>
            ) : check && check.issues.length > 0 ? (
              <div className="text-bad">
                <div>{t('publishing.dialog.fix')}</div>
                <ul className="list-disc ps-5">{check.issues.map((i) => <li key={i.key}>{i.text}</li>)}</ul>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </Modal>
  )
}

export function PublishButton({ clip, className, small = true }: { clip: ClipLike; className?: string; small?: boolean }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} data-testid="publish-open"
              className={cx(small ? 'btn-ghost btn-sm' : 'btn-primary', className)}>
        <Send className="w-3.5 h-3.5" />{t('publishing.publish')}
      </button>
      {open && <PublishDialog clip={clip} open={open} onClose={() => setOpen(false)} />}
    </>
  )
}
