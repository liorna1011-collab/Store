// Internal admin / developer view: unit economics, usage ledger, upload telemetry, job health.
// Reached only at /admin with the admin token; nothing here is linked from the customer interface.

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, PolixorApiError } from '../lib/api'
import { useStore } from '../lib/store'
import { Button, Card, CardHeader, Field, Input, PageHeader, Select, Skeleton } from '../components/ds'

type Row = Record<string, any>

const usd = (v: unknown) => (typeof v === 'number' ? `$${v.toFixed(v < 1 ? 4 : 2)}` : '–')
const num = (v: unknown, d = 1) => (typeof v === 'number' ? v.toFixed(d) : '–')

function Table({ cols, rows, empty }: { cols: [string, (r: Row) => React.ReactNode][]; rows: Row[]; empty: string }) {
  if (!rows.length) return <p className="text-sm text-ink-400 p-4">{empty}</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead><tr className="text-ink-400 text-start">{cols.map(([h]) => <th key={h} className="px-3 py-2 text-start font-medium">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-ink-750">
          {rows.map((r, i) => <tr key={i}>{cols.map(([h, f]) => <td key={h} className="px-3 py-1.5 align-top ltr-nums">{f(r)}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  )
}

export default function AdminPage() {
  const { t } = useTranslation()
  const { notifyError, pushToast } = useStore()
  const [admin, setAdmin] = useState<boolean | null>(null)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [ov, setOv] = useState<Row | null>(null)
  const [ledger, setLedger] = useState<Row[]>([])
  const [uploads, setUploads] = useState<Row[]>([])
  const [health, setHealth] = useState<Row | null>(null)
  const [workers, setWorkers] = useState<Row | null>(null)
  const [bench, setBench] = useState<Row[]>([])
  const [events, setEvents] = useState<{ build: string; paid_ai: boolean; client: Row[]; server: Row[] } | null>(null)
  const [adj, setAdj] = useState({ minutes: '', note: '' })

  const load = useCallback(async () => {
    try {
      const [o, l, u, h, ev, w, b] = await Promise.all([api.adminOverview(), api.adminLedger(), api.adminUploads(),
                                                        api.adminHealth(), api.adminEvents(), api.adminWorkers(),
                                                        api.adminBench()])
      setOv(o); setLedger(l.entries); setUploads(u.uploads); setHealth(h); setEvents(ev); setWorkers(w)
      setBench(b.runs)
    } catch (e) {
      if (e instanceof PolixorApiError && e.status === 403) setAdmin(false)
      else notifyError(e)
    }
  }, [notifyError])

  useEffect(() => { api.adminSession().then((s) => setAdmin(s.admin)).catch(() => setAdmin(false)) }, [])
  useEffect(() => { if (admin) void load() }, [admin, load])

  const signIn = async () => {
    if (busy) return
    setBusy(true)
    try { await api.adminSignIn(token.trim()); setToken(''); setAdmin(true) } catch (e) {
      pushToast({ tone: 'error', title: e instanceof PolixorApiError && e.status === 403 ? t('creator.admin.denied') : String(e) })
    } finally { setBusy(false) }
  }

  if (admin === null) return <Skeleton className="h-64" />
  if (!admin) {
    return (
      <div className="max-w-md mx-auto">
        <PageHeader title={t('creator.admin.title')} subtitle={t('creator.admin.subtitle')} />
        <Card className="p-5 space-y-4">
          <Field label={t('creator.admin.token')} hint={t('creator.admin.tokenHint')} htmlFor="admin-token">
            <Input id="admin-token" type="password" autoComplete="off" value={token}
                   onChange={(e) => setToken(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void signIn() }} />
          </Field>
          <Button variant="primary" onClick={signIn} loading={busy} disabled={!token.trim()}>{t('creator.admin.signIn')}</Button>
        </Card>
      </div>
    )
  }
  if (!ov) return <Skeleton className="h-64" />
  const acc = ov.account
  const C = (k: string) => t(`creator.admin.col.${k}`)
  return (
    <div className="space-y-6" data-testid="admin-page">
      <PageHeader title={t('creator.admin.title')} subtitle={t('creator.admin.subtitle')}
                  actions={<>
                    <Button size="sm" onClick={load}>{t('creator.admin.refresh')}</Button>
                    <Button size="sm" onClick={async () => { await api.adminSignOut(); setAdmin(false) }}>{t('creator.admin.signOut')}</Button>
                  </>} />

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader title={t('creator.admin.account')} />
          <div className="p-4 text-sm space-y-1">
            <div>{acc.plan.name} · {acc.plan.price} {acc.plan.currency} · {acc.plan.minutes} min</div>
            <div className="ltr-nums">{acc.used_minutes} / {acc.plan.minutes} · {acc.remaining_minutes} left · {acc.period.start.slice(0, 10)} → {acc.period.end.slice(0, 10)}</div>
            <div className="flex flex-wrap items-end gap-2 pt-2">
              <Field label={t('creator.admin.setPlan')} htmlFor="plan">
                <Select id="plan" value={acc.plan.code} onChange={async (e) => {
                  try { await api.adminSetPlan(e.target.value); await load() } catch (err) { notifyError(err) }
                }}>
                  {['starter', 'pro', 'studio'].map((c) => <option key={c} value={c}>{c}</option>)}
                </Select>
              </Field>
            </div>
            <div className="flex flex-wrap items-end gap-2 pt-2">
              <Field label={t('creator.admin.adjust')} htmlFor="adj-min">
                <Input id="adj-min" inputMode="decimal" value={adj.minutes} onChange={(e) => setAdj({ ...adj, minutes: e.target.value })} />
              </Field>
              <Field label={t('creator.admin.note')} htmlFor="adj-note">
                <Input id="adj-note" value={adj.note} onChange={(e) => setAdj({ ...adj, note: e.target.value })} />
              </Field>
              <Button size="sm" disabled={!adj.minutes || Number.isNaN(Number(adj.minutes))} onClick={async () => {
                try {
                  await api.adminAdjust(Number(adj.minutes), adj.note, `${Date.now()}`)
                  setAdj({ minutes: '', note: '' }); await load()
                } catch (err) { notifyError(err) }
              }}>{t('creator.admin.apply')}</Button>
            </div>
          </div>
        </Card>
        <Card>
          <CardHeader title={t('creator.admin.totals')} />
          <div className="p-4 text-sm grid grid-cols-2 gap-2 ltr-nums">
            <div>{C('revenue')}: {usd(ov.totals.revenue_usd)}</div>
            <div>{C('ai')}: {usd(ov.totals.ai_cost_usd)}</div>
            <div>{C('infra')}: {usd(ov.totals.infra_usd)}</div>
            <div>{C('margin')}: {usd(ov.totals.gross_margin_usd)}</div>
            <div className="col-span-2 text-ink-500">{t('creator.admin.machine')}: {ov.machine.cpus} CPU · {ov.machine.ram_gb} GB · GPU {ov.machine.gpus} · {ov.machine.disk_free_gb} GB free · load {ov.machine.load_1m}</div>
            <div className="col-span-2 text-ink-500">render ×{ov.tuning.render_workers} · ASR threads {ov.tuning.asr_threads} ({ov.tuning.asr_device}) · upload {ov.tuning.upload_concurrency.min}–{ov.tuning.upload_concurrency.max} · jobs ×{ov.tuning.background_jobs}</div>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title={t('creator.admin.projects')} />
        <Table empty={t('creator.admin.none')} rows={ov.projects} cols={[
          [C('project'), (r) => <span className="bidi-isolate">{r.title || r.project_id}</span>],
          [C('status'), (r) => `${r.status} / ${r.phase}`],
          [C('source'), (r) => `${num(r.source_seconds / 60)} min`],
          [C('minutes'), (r) => `${r.minutes_deducted} (${num(r.minutes_charged_exact, 2)})`],
          [C('state'), (r) => r.billing_state],
          [C('ai'), (r) => usd(r.ai_cost_usd)],
          [C('infra'), (r) => usd(r.infra_usd)],
          [C('revenue'), (r) => usd(r.revenue_usd)],
          [C('margin'), (r) => `${usd(r.gross_margin_usd)}${r.gross_margin_pct != null ? ` (${r.gross_margin_pct}%)` : ''}`],
          [C('calls'), (r) => `${r.model.calls} · ${r.model.input_tokens}/${r.model.output_tokens} tok`],
          [C('cache'), (r) => (r.model.cache_hit_rate != null ? `${Math.round(r.model.cache_hit_rate * 100)}%` : '–')],
        ]} />
      </Card>

      <Card data-testid="admin-failures">
        <CardHeader title={t('creator.admin.failures')}
                    subtitle={events ? `build ${events.build} · paid AI ${events.paid_ai ? 'on' : 'OFF'}` : undefined} />
        <Table empty={t('creator.admin.none')} rows={events?.client || []} cols={[
          [C('when'), (r) => new Date(r.at * 1000).toLocaleString()],
          [C('project'), (r) => r.project_id || '–'],
          ['action', (r) => <span className="text-xs">{r.action || r.route}</span>],
          [C('status'), (r) => r.status || '–'],
          [C('category'), (r) => r.category],
          ['ms', (r) => r.elapsed_ms],
          [C('size'), (r) => (r.detail?.request_bytes ? `${(r.detail.request_bytes / 1048576).toFixed(1)} MiB` : '–')],
          [C('request'), (r) => <code className="text-xs">{r.request_id}</code>],
          ['', (r) => <span className="text-xs text-ink-500">{r.code || r.message}</span>],
        ]} />
        <Table empty={t('creator.admin.none')} rows={events?.server || []} cols={[
          [C('when'), (r) => new Date(r.at * 1000).toLocaleString()],
          ['request', (r) => `${r.method} ${r.path}`], [C('status'), (r) => r.status],
          ['ms', (r) => r.elapsed_ms], [C('request'), (r) => <code className="text-xs">{r.request_id}</code>],
        ]} />
      </Card>

      <Card>
        <CardHeader title={t('creator.admin.health')} />
        <div className="p-4 text-sm space-y-2">
          <div>{t('creator.admin.attention')}: {(health?.needs_attention || []).length}</div>
          <Table empty={t('creator.admin.none')} rows={health?.running || []} cols={[
            [C('project'), (r) => r.project_id], [C('status'), (r) => r.stage],
            ['silent / limit (s)', (r) => `${r.silent_seconds} / ${r.limit_seconds}`],
          ]} />
          <Table empty={t('creator.admin.none')} rows={health?.events || []} cols={[
            [t('creator.admin.events'), (r) => r.kind], [C('project'), (r) => r.job_id],
            [C('when'), (r) => new Date(r.at * 1000).toLocaleString()],
          ]} />
        </div>
      </Card>

      <Card data-testid="admin-workers">
        <CardHeader title={t('creator.admin.workers')} />
        <div className="p-4 text-sm space-y-2">
          <div className="ltr-nums" dir="ltr">
            mode {workers?.mode} · queue {JSON.stringify(workers?.counts || {})} · wanted {JSON.stringify(workers?.wanted || {})}
            {' '}· encoder {workers?.encoders?.auto_choice} (hardware {(workers?.encoders?.working_hardware || []).join(', ') || 'none'})
          </div>
          <Table empty={t('creator.admin.none')} rows={workers?.workers || []} cols={[
            ['pid', (r) => r.pid], ['role', (r) => r.role], [C('state'), (r) => r.state], ['task', (r) => r.task_id || '–'],
            ['alive', (r) => (r.alive ? 'yes' : 'no')], ['build', (r) => <code className="text-xs">{r.build}</code>],
          ]} />
          <Table empty={t('creator.admin.none')} rows={workers?.tasks || []} cols={[
            ['#', (r) => r.id], ['kind', (r) => r.kind], [C('project'), (r) => r.job_id], ['prio', (r) => r.priority],
            [C('state'), (r) => r.status], ['tries', (r) => r.attempts], ['lease (s)', (r) => r.lease_left ?? '–'],
            ['error', (r) => <span className="text-xs">{r.error}</span>],
          ]} />
        </div>
      </Card>

      <Card data-testid="admin-bench">
        <CardHeader title={t('creator.admin.bench')} />
        <Table empty={t('creator.admin.none')} rows={bench} cols={[
          ['run', (r) => r.label || r.file], ['source', (r) => `${num(r.media_seconds / 60, 1)} min`],
          ['wall', (r) => `${num(r.wall_seconds, 0)} s`], ['RTF', (r) => num(r.rtf_total, 3)],
          ['1st Short', (r) => `${r.time_to_first_short ?? '–'} s`], ['all Shorts', (r) => `${r.time_to_all_shorts ?? '–'} s`],
          ['long-form', (r) => `${r.time_to_longform ?? '–'} s`],
          ['processes', (r) => Object.values(r.subprocesses || {}).reduce((a: number, b) => a + Number(b), 0)],
        ]} />
      </Card>

      <Card>
        <CardHeader title={t('creator.admin.uploads')} />
        <Table empty={t('creator.admin.none')} rows={uploads} cols={[
          [C('size'), (r) => `${num(r.size / 1024 ** 3, 2)} GB`], [C('status'), (r) => r.status],
          // MB/s = megabytes per second; Mbps = megabits per second (×8)
          ['MB/s · Mbps', (r) => r.avg_MBps != null ? `${num(r.avg_MBps)} · ${num(r.avg_MBps * 8, 0)}` : '–'],
          ['browser hash MB/s', (r) => num(r.path?.browser_hash_MBps)],
          ['request MB/s', (r) => num(r.path?.browser_request_MBps)],
          ['server receive MB/s', (r) => num(r.path?.server_receive_MBps)],
          ['server write MB/s', (r) => num(r.path?.server_write_MBps)],
          [C('retries'), (r) => r.retries], [C('failed'), (r) => r.failed_chunks], [C('resumes'), (r) => r.resumes],
          [C('chunk'), (r) => `${r.chunk_size / 1024 ** 2} MB`], [C('conc'), (r) => r.concurrency ?? '–'],
          [C('finalize'), (r) => num(r.finalize_seconds)],
        ]} />
      </Card>

      <Card>
        <CardHeader title={t('creator.admin.ledger')} />
        <Table empty={t('creator.admin.none')} rows={ledger} cols={[
          [C('when'), (r) => (r.created_at || '').replace('T', ' ').slice(0, 19)], [C('project'), (r) => r.project_id],
          [C('type'), (r) => r.usage_type], [C('state'), (r) => `${r.status}${r.reason ? ` (${r.reason})` : ''}`],
          [C('source'), (r) => `${num(r.source_duration_seconds, 3)} s`], [C('key'), (r) => <code className="text-xs">{r.idempotency_key}</code>],
        ]} />
      </Card>
    </div>
  )
}
