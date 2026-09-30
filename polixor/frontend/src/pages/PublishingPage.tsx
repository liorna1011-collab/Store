// מרכז הפרסום: תור והיסטוריה (מקובץ לפי מצב), וחשבונות – חיבור דרך דף
// הכניסה של הפלטפורמה (OAuth; לעולם לא סיסמה), ניתוק, חיבור מחדש, ופרטי
// אפליקציות המפתחים (נשמרים בשרת; הסוד לא מוצג שוב).

import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, CalendarClock, Copy, ExternalLink, KeyRound, Link2, RotateCcw, ShieldCheck, Unlink, X } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { formatDate } from '../lib/i18nFormat'
import type { PublishConfigGroup, PublishHistory, PublishJobItem, PublishPlatform, SocialAccount } from '../lib/types'
import {
  Badge, Button, Callout, Card, ConfirmModal, EmptyState, Field, Input, PageHeader, Segmented, Skeleton, cx,
} from '../components/ds'

type Tab = 'queue' | 'accounts'

const GROUP_TONE: Record<PublishJobItem['group'], 'bad' | 'brand' | 'warn' | 'ok' | 'neutral'> = {
  needs_attention: 'bad', in_progress: 'brand', scheduled: 'warn', published: 'ok', cancelled: 'neutral',
}

export default function PublishingPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const { pushToast, subscribe } = useStore()
  const tab: Tab = params.get('tab') === 'accounts' ? 'accounts' : 'queue'

  // חזרה מ-OAuth: ?connected=… או ?publish_error=…
  useEffect(() => {
    const ok = params.get('connected')
    const err = params.get('publish_error')
    if (!ok && !err) return
    if (ok) pushToast({ tone: 'success', title: t('publishing.accounts.connected') })
    if (err) pushToast({ tone: 'error', title: t(`publishing.errors.${err}`, { defaultValue: t('publishing.errors.oauth_failed') }) })
    const next = new URLSearchParams(params)
    next.delete('connected'); next.delete('publish_error')
    next.set('tab', 'accounts')
    setParams(next, { replace: true })
  }, [params, setParams, pushToast, t])

  const [history, setHistory] = useState<PublishHistory | null>(null)
  const [scheduler, setScheduler] = useState<{ running: boolean; enabled: boolean } | null>(null)
  const loadHistory = useCallback(async () => {
    try { setHistory(await api.publishHistory()) } catch { setHistory({ items: [], groups: [] }) }
  }, [])
  useEffect(() => { void loadHistory() }, [loadHistory])
  useEffect(() => { api.publishPlatforms().then((p) => setScheduler(p.scheduler)).catch(() => undefined) }, [])
  // עדכון חי: כל שינוי בפרסום יוצר התראה → אירוע WebSocket
  useEffect(() => subscribe((e) => { if (e.type === 'notification') void loadHistory() }), [subscribe, loadHistory])
  useEffect(() => {
    if (!history?.items.some((i) => i.group === 'in_progress')) return
    const id = setInterval(() => void loadHistory(), 8000)
    return () => clearInterval(id)
  }, [history, loadHistory])

  return (
    <div className="space-y-6">
      <PageHeader title={t('publishing.page.title')} subtitle={t('publishing.page.subtitle')} />
      <Segmented value={tab} label={t('publishing.page.title')}
                 onChange={(v) => { const n = new URLSearchParams(params); n.set('tab', v); setParams(n, { replace: true }) }}
                 options={[{ value: 'queue', label: t('publishing.tabs.queue') },
                           { value: 'accounts', label: t('publishing.tabs.accounts') }]} />
      {tab === 'queue'
        ? <Queue history={history} reload={loadHistory} schedulerOff={scheduler ? !scheduler.running : false} />
        : <Accounts />}
    </div>
  )
}

// --------------------------------------------------------------------------
function Queue({ history, reload, schedulerOff }: { history: PublishHistory | null; reload: () => Promise<void>; schedulerOff: boolean }) {
  const { t } = useTranslation()
  const { notifyError, pushToast } = useStore()
  if (history === null) return <Skeleton className="h-40" />
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try { await fn(); pushToast({ tone: 'success', title: msg }) } catch (e) { notifyError(e) } finally { void reload() }
  }
  return (
    <div className="space-y-6" data-testid="publish-queue">
      {schedulerOff && history.items.some((i) => i.needs_server_online) && (
        <Callout tone="bad">{t('publishing.queue.schedulerOff')}</Callout>
      )}
      {history.groups.length === 0 && (
        <EmptyState icon={<CalendarClock className="w-6 h-6" />} title={t('publishing.queue.empty')} />
      )}
      {history.groups.map((g) => (
        <section key={g.state} data-group={g.state}>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink-300">
            <Badge tone={GROUP_TONE[g.state]}>{g.items.length}</Badge>{g.title}
          </h2>
          <ul className="space-y-2">
            {g.items.map((j) => (
              <li key={j.id}>
                <Card className="p-3 flex flex-col sm:flex-row gap-3" data-testid="publish-item">
                  <div className="w-full sm:w-24 shrink-0">
                    {j.thumbnail_url
                      ? <img src={j.thumbnail_url} alt="" className={cx('rounded-md object-cover bg-ink-800',
                          j.format === 'short' ? 'h-24 w-14 mx-auto sm:mx-0' : 'h-14 w-24')} />
                      : <div className="h-14 w-24 rounded-md bg-ink-800" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-ink-100 truncate" dir="auto">{j.title || j.clip_title}</span>
                      <Badge tone={GROUP_TONE[j.group]}>{j.status_label}</Badge>
                    </div>
                    <div className="text-xs text-ink-400 mt-0.5">
                      {j.platform_name} · <span dir="auto">{j.account_label}</span> · {t(`publishing.dialog.format.${j.format}`)}
                      {' · '}{t(`publishing.privacy.${j.privacy}`, { defaultValue: j.privacy })}
                    </div>
                    {j.schedule_at && ['scheduled', 'scheduled_on_platform'].includes(j.status) && (
                      <div className="text-xs text-ink-300 mt-1">{t('publishing.queue.scheduledFor', { time: formatDate(j.schedule_at) })}</div>
                    )}
                    {j.published_at && (
                      <div className="text-xs text-ink-400 mt-1">{t('publishing.queue.publishedAt', { time: formatDate(j.published_at) })}</div>
                    )}
                    {j.needs_server_online && (
                      <div className="text-xs text-warn mt-1 flex items-center gap-1">
                        <AlertTriangle className="w-3.5 h-3.5" />{t('publishing.queue.serverOnline')}
                      </div>
                    )}
                    {j.status === 'queued' && j.next_attempt_at && (
                      <div className="text-xs text-ink-400 mt-1">
                        {t('publishing.queue.retryAt', { time: formatDate(j.next_attempt_at) })} · {t('publishing.queue.attempts', { count: j.attempts })}
                      </div>
                    )}
                    {j.error && <div className="text-xs text-bad mt-1" dir="auto">{j.error}</div>}
                  </div>
                  <div className="flex sm:flex-col gap-1.5 shrink-0">
                    {j.remote_url && (
                      <a href={j.remote_url} target="_blank" rel="noreferrer noopener" className="btn-ghost btn-sm">
                        <ExternalLink className="w-3.5 h-3.5" />{t('publishing.queue.view')}
                      </a>
                    )}
                    {j.can_retry && (
                      <Button size="sm" icon={<RotateCcw className="w-3.5 h-3.5" />}
                              onClick={() => act(() => api.retryPublish(j.id), t('publishing.queue.retried'))}>
                        {t('publishing.queue.retry')}
                      </Button>
                    )}
                    {j.can_cancel && (
                      <Button size="sm" variant="quiet" icon={<X className="w-3.5 h-3.5" />}
                              onClick={() => act(() => api.cancelPublish(j.id), t('publishing.queue.cancelled'))}>
                        {t('publishing.queue.cancel')}
                      </Button>
                    )}
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------
function Accounts() {
  const { t } = useTranslation()
  const { notifyError } = useStore()
  const [platforms, setPlatforms] = useState<PublishPlatform[] | null>(null)
  const [accounts, setAccounts] = useState<SocialAccount[]>([])
  const [config, setConfig] = useState<PublishConfigGroup[]>([])
  const [confirm, setConfirm] = useState<SocialAccount | null>(null)
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    try {
      const [p, a, c] = await Promise.all([api.publishPlatforms(), api.publishAccounts(), api.publishConfig()])
      setPlatforms(p.platforms); setAccounts(a.accounts); setConfig(c.groups)
    } catch (e) { notifyError(e) }
  }, [notifyError])
  useEffect(() => { void load() }, [load])

  const connect = async (platform: string) => {
    setBusy(platform)
    try {
      const { auth_url } = await api.connectAccount(platform)
      window.location.assign(auth_url)                // דף הכניסה של הפלטפורמה
    } catch (e) { notifyError(e); setBusy('') }
  }

  if (platforms === null) return <Skeleton className="h-40" />
  return (
    <div className="space-y-6" data-testid="publish-accounts">
      <Callout tone="brand" title={t('publishing.accounts.neverPassword')} />

      <Card className="p-5 space-y-3">
        <h2 className="font-semibold text-ink-100">{t('publishing.accounts.title')}</h2>
        {accounts.length === 0 && <p className="text-sm text-ink-400">{t('publishing.accounts.none')}</p>}
        <ul className="divide-y divide-ink-750">
          {accounts.map((a) => (
            <li key={a.id} className="py-2.5 flex flex-wrap items-center gap-3" data-testid="account-row">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink-100 truncate" dir="auto">{a.display_name || a.handle}</div>
                <div className="text-xs text-ink-400">
                  {platforms.find((p) => p.id === a.platform)?.name || a.platform}
                  {a.handle && <> · <span dir="ltr">{a.handle}</span></>}
                  {a.linked_page && <> · {t('publishing.accounts.linkedPage', { page: a.linked_page })}</>}
                </div>
              </div>
              <Badge tone={a.status === 'connected' ? 'ok' : 'warn'}>{a.status_label}</Badge>
              {a.status !== 'connected' && (
                <Button size="sm" icon={<Link2 className="w-3.5 h-3.5" />} loading={busy === a.platform}
                        onClick={() => connect(a.platform)}>{t('publishing.accounts.reconnect')}</Button>
              )}
              <Button size="sm" variant="quiet" icon={<Unlink className="w-3.5 h-3.5" />}
                      onClick={() => setConfirm(a)}>{t('publishing.accounts.disconnect')}</Button>
            </li>
          ))}
        </ul>
      </Card>

      <Card className="p-5 space-y-3">
        <h2 className="font-semibold text-ink-100">{t('publishing.accounts.platforms')}</h2>
        <ul className="grid gap-3 sm:grid-cols-2">
          {platforms.map((p) => (
            <li key={p.id} className="rounded-lg border border-ink-750 p-3 flex items-center gap-3" data-testid={`platform-${p.id}`}>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink-100">{p.sandbox ? t('publishing.accounts.sandbox') : p.name}</div>
                <div className="text-xs text-ink-400">
                  {p.sandbox ? t('publishing.accounts.sandboxHint')
                    : ['facebook', 'instagram'].includes(p.id) && p.available && p.configured ? t('publishing.accounts.metaShared')
                    : p.planned_stage ? t('publishing.accounts.comingIn', { stage: p.planned_stage })
                      : !p.configured ? t('publishing.accounts.needsApp') : ''}
                </div>
              </div>
              <Button size="sm" variant="primary" disabled={!p.available || !p.configured}
                      loading={busy === p.id} icon={<Link2 className="w-3.5 h-3.5" />}
                      data-testid={`connect-${p.id}`} onClick={() => connect(p.id)}>
                {t('publishing.accounts.connect')}
              </Button>
            </li>
          ))}
        </ul>
      </Card>

      <DeveloperApps groups={config} onSaved={load} />

      <ConfirmModal open={!!confirm} onClose={() => setConfirm(null)} danger
                    title={t('publishing.accounts.disconnect')}
                    body={confirm ? t('publishing.accounts.disconnectConfirm', { name: confirm.display_name || confirm.handle }) : ''}
                    confirmLabel={t('publishing.accounts.disconnect')}
                    onConfirm={async () => {
                      if (!confirm) return
                      try { await api.disconnectAccount(confirm.id) } catch (e) { notifyError(e) }
                      setConfirm(null); void load()
                    }} />
    </div>
  )
}

function DeveloperApps({ groups, onSaved }: { groups: PublishConfigGroup[]; onSaved: () => void }) {
  const { t } = useTranslation()
  const { pushToast, notifyError } = useStore()
  const [values, setValues] = useState<Record<string, string>>({})
  const [copied, setCopied] = useState('')
  const save = async (g: PublishConfigGroup) => {
    const payload: Record<string, string> = {}
    g.fields.forEach((f) => { if (values[f.name]) payload[f.name] = values[f.name] })
    if (!Object.keys(payload).length) return
    try {
      await api.savePublishConfig(g.id, payload)
      setValues((v) => { const n = { ...v }; g.fields.forEach((f) => delete n[f.name]); return n })
      pushToast({ tone: 'success', title: t('publishing.apps.saved') })
      onSaved()
    } catch (e) { notifyError(e) }
  }
  return (
    <Card className="p-5 space-y-4">
      <div>
        <h2 className="font-semibold text-ink-100 flex items-center gap-2"><KeyRound className="w-4 h-4" />{t('publishing.apps.title')}</h2>
        <p className="text-sm text-ink-400 mt-1">{t('publishing.apps.subtitle')}</p>
      </div>
      {groups.map((g) => (
        <div key={g.id} className="rounded-lg border border-ink-750 p-4 space-y-3" data-testid={`app-${g.id}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-ink-100">{g.label}</span>
            <Badge tone={g.configured ? 'ok' : 'neutral'} icon={g.configured ? <ShieldCheck className="w-3 h-3" /> : undefined}>
              {g.configured ? t('publishing.apps.configured') : t('publishing.apps.notConfigured')}
            </Badge>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {g.fields.map((f) => (
              <Field key={f.name} label={t(`publishing.apps.fields.${f.name}`, { defaultValue: f.name })}>
                <Input type="password" autoComplete="off" dir="ltr" value={values[f.name] || ''}
                       placeholder={f.masked ? `${f.masked} ${t('publishing.apps.keep')}` : ''}
                       onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))} />
              </Field>
            ))}
          </div>
          <div className="space-y-1">
            <div className="text-xs text-ink-400">{t('publishing.apps.redirect')}</div>
            {g.redirect_uris.map((u) => (
              <div key={u} className="flex items-center gap-2">
                <code className="text-xs bg-ink-800 rounded px-2 py-1 break-all flex-1" dir="ltr">{u}</code>
                <Button size="sm" variant="quiet" icon={<Copy className="w-3.5 h-3.5" />}
                        onClick={() => { void navigator.clipboard?.writeText(u); setCopied(u) }}>
                  {copied === u ? t('publishing.apps.copied') : t('publishing.apps.copy')}
                </Button>
              </div>
            ))}
          </div>
          <Button size="sm" onClick={() => save(g)}
                  disabled={!g.fields.some((f) => values[f.name])}>{t('publishing.apps.save')}</Button>
        </div>
      ))}
    </Card>
  )
}
