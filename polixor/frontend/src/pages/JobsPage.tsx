import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeader } from '../App'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import {
  formatEta, formatRelative, STAGE_LABEL, STATUS_LABEL, STATUS_TONE,
} from '../lib/format'
import type { Job } from '../lib/types'
import {
  ConfirmDialog, EmptyState, IconLive, IconRefresh, IconStop, IconTasks, IconTrash,
  ProgressBar, Spinner,
} from '../components/ui'

export default function JobsPage() {
  const { jobs, jobsLoading, refreshJobs, removeJob, notifyError, pushToast } = useStore()
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const doCancel = async (id: string) => {
    setBusy(id)
    try {
      await api.cancelJob(id)
      pushToast({ tone: 'info', title: 'בקשת ביטול נשלחה' })
      await refreshJobs()
    } catch (e) { notifyError(e, 'ביטול המשימה נכשל') } finally { setBusy(null) }
  }

  const doRetry = async (id: string, fromStart: boolean) => {
    setBusy(id)
    try {
      await api.retryJob(id, fromStart)
      pushToast({
        tone: 'success',
        title: fromStart ? 'המשימה מופעלת מחדש מההתחלה' : 'המשימה ממשיכה מהשלב האחרון',
      })
      await refreshJobs()
    } catch (e) { notifyError(e, 'חידוש המשימה נכשל') } finally { setBusy(null) }
  }

  const doDelete = async () => {
    if (!confirmId) return
    setBusy(confirmId)
    try {
      await api.deleteJob(confirmId, true)
      removeJob(confirmId)
      pushToast({ tone: 'success', title: 'המשימה והקבצים שלה נמחקו' })
    } catch (e) { notifyError(e, 'מחיקת המשימה נכשלה') } finally {
      setBusy(null); setConfirmId(null)
    }
  }

  return (
    <div className="p-4 sm:p-8 max-w-6xl mx-auto">
      <PageHeader
        title="משימות"
        subtitle="מעקב אחרי הורדה, תמלול, ניתוח וייצוא — עם התקדמות אמיתית לכל שלב."
        actions={
          <button className="btn-ghost btn-sm" onClick={() => void refreshJobs()}>
            <IconRefresh className="w-4 h-4" />רענן
          </button>
        }
      />

      {jobsLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton h-28" />)}
        </div>
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={<IconTasks className="w-10 h-10" />}
          title="אין עדיין משימות"
          body="הדבק קישור לשידור או העלה קובץ כדי להתחיל את הניתוח הראשון."
          action={<Link to="/" className="btn-primary">התחלת ניתוח</Link>}
        />
      ) : (
        <div className="space-y-3">
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} busy={busy === job.id}
                    onCancel={() => void doCancel(job.id)}
                    onRetry={(fs) => void doRetry(job.id, fs)}
                    onDelete={() => setConfirmId(job.id)} />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(confirmId)}
        title="מחיקת משימה"
        body="המשימה, הקליפים שנוצרו והקבצים שלה יימחקו לצמיתות. הפעולה אינה הפיכה."
        onConfirm={() => void doDelete()}
        onCancel={() => setConfirmId(null)}
        busy={Boolean(busy)}
      />
    </div>
  )
}

function JobRow({ job, busy, onCancel, onRetry, onDelete }: {
  job: Job
  busy: boolean
  onCancel: () => void
  onRetry: (fromStart: boolean) => void
  onDelete: () => void
}) {
  const navigate = useNavigate()
  const active = job.status === 'running' || job.status === 'queued'
  const tone = job.status === 'failed' ? 'bad'
    : job.status === 'completed' ? 'ok' : 'brand'
  const eta = formatEta(job.eta_seconds)

  return (
    <div className="card p-5 hover:border-ink-600 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <button className="min-w-0 text-right flex-1"
                onClick={() => navigate(`/jobs/${job.id}`)}>
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`chip ${STATUS_TONE[job.status]}`}>
              {STATUS_LABEL[job.status]}
            </span>
            {job.is_live_mode && (
              <span className="chip bg-warn/15 text-warn">
                <IconLive className="w-3 h-3" />
                שידור חי{job.live_cycles > 0 ? ` · מחזור ${job.live_cycles}` : ''}
              </span>
            )}
            <h3 className="text-sm font-medium text-white truncate">
              {job.title || job.input_url || 'משימה ללא שם'}
            </h3>
          </div>
          <div className="mt-1 text-xs text-ink-500">
            {formatRelative(job.created_at)}
            {job.source?.duration ? (
              <> · מקור <span className="ltr-nums">
                {Math.round(job.source.duration / 60)}</span> דק'</>
            ) : null}
            {job.clip_counts?.total ? (
              <> · <span className="ltr-nums">
                {job.clip_counts.ready}/{job.clip_counts.total}</span> קליפים מוכנים</>
            ) : null}
          </div>
        </button>

        <div className="flex items-center gap-2 shrink-0">
          {active && (
            <button className="btn-ghost btn-sm" onClick={onCancel} disabled={busy}>
              {busy ? <Spinner className="w-3.5 h-3.5" /> : <IconStop className="w-3.5 h-3.5" />}
              עצור
            </button>
          )}
          {(job.status === 'failed' || job.status === 'cancelled') && (
            <>
              <button className="btn-ghost btn-sm" onClick={() => onRetry(false)} disabled={busy}>
                <IconRefresh className="w-3.5 h-3.5" />המשך
              </button>
              <button className="btn-ghost btn-sm" onClick={() => onRetry(true)} disabled={busy}>
                מהתחלה
              </button>
            </>
          )}
          <button className="btn-ghost btn-sm !px-2" onClick={onDelete} disabled={busy}
                  aria-label="מחק משימה">
            <IconTrash className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {active && (
        <div className="mt-4">
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="text-ink-300">
              {STAGE_LABEL[job.stage] ?? job.stage}
              {job.message ? <span className="text-ink-500"> — {job.message}</span> : null}
            </span>
            <span className="text-ink-400 ltr-nums">
              {Math.round(job.overall_progress * 100)}%
              {eta && <span className="text-ink-600"> · נותרו {eta}</span>}
            </span>
          </div>
          <ProgressBar value={job.overall_progress} tone={tone as any} striped />
        </div>
      )}

      {job.status === 'failed' && job.error && (
        <div className="mt-3 rounded-lg bg-bad/10 border border-bad/25 p-3">
          <p className="text-xs text-bad leading-relaxed">{job.error}</p>
        </div>
      )}

      {job.status === 'completed' && (
        <div className="mt-3 flex items-center gap-3">
          <ProgressBar value={1} tone="ok" height="h-1" />
          <Link to={`/clips?job=${job.id}`}
                className="text-xs text-brand-400 hover:text-brand-300 whitespace-nowrap">
            צפה בקליפים
          </Link>
        </div>
      )}
    </div>
  )
}
