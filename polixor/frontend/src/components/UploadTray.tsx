// The uploads in progress, on every page: name, how much, speed, time left, pause / resume / cancel,
// and – when Start was pressed early – the project that starts by itself afterwards.

import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Pause, Play, X } from 'lucide-react'
import type { UploadState } from '../lib/upload'
import { uploadManager, useUploads } from '../lib/uploadManager'
import { formatBytes } from '../lib/i18nFormat'
import { ProgressBar } from './ds'

/** One simple speed for a person (MB/s) and an honest, rounded time left. */
export function speedText(state: UploadState, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (!state.rateBps || state.phase === 'complete' || state.phase === 'paused') return ''
  const mbs = state.rateBps / 1e6
  const speed = t('creator.upload.speed', { speed: mbs >= 10 ? mbs.toFixed(0) : mbs.toFixed(1) })
  const eta = state.etaSeconds
  if (eta == null) return speed
  const left = eta < 90 ? t('creator.upload.etaMinute')
    : eta < 3600 ? t('creator.upload.etaMinutes', { n: Math.round(eta / 60) })
      : t('creator.upload.etaHours', { n: (eta / 3600).toFixed(1) })
  return `${speed} · ${left}`
}

export default function UploadTray() {
  const { t } = useTranslation()
  const items = useUploads().filter((x) => !x.dismissed)
  if (!items.length) return null
  return (
    <div className="fixed bottom-4 end-4 z-40 w-[22rem] max-w-[calc(100vw-2rem)] space-y-2" data-testid="upload-tray"
         aria-live="polite">
      {items.map((it) => {
        const s = it.state
        const pct = Math.floor((100 * s.loaded) / Math.max(1, s.total))
        const active = ['starting', 'uploading', 'adjusting', 'retrying', 'finalizing'].includes(s.phase)
        return (
          <div key={it.id} className="card p-3 shadow-pop text-sm" data-testid="upload-tray-item" data-phase={s.phase}>
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-ink-100 bidi-isolate">{it.name}</div>
                <div className="text-xs text-ink-400 ltr-nums">
                  {formatBytes(s.loaded)} / {formatBytes(s.total)} · {pct}%
                  {speedText(s, t) && <> · <span data-testid="upload-speed">{speedText(s, t)}</span></>}
                </div>
              </div>
              {active && s.phase !== 'finalizing' && (
                <button type="button" className="btn-quiet !p-1.5" aria-label={t('creator.upload.pause')}
                        onClick={() => uploadManager.pause(it.id)}><Pause className="w-4 h-4" /></button>)}
              {(s.phase === 'paused' || s.phase === 'failed') && (
                <button type="button" className="btn-quiet !p-1.5" aria-label={t('creator.upload.resume')}
                        onClick={() => uploadManager.resume(it.id)}><Play className="w-4 h-4" /></button>)}
              {s.phase !== 'complete' ? (
                <button type="button" className="btn-quiet !p-1.5" aria-label={t('common.cancel')}
                        onClick={() => void uploadManager.cancel(it.id)}><X className="w-4 h-4" /></button>
              ) : (
                <button type="button" className="btn-quiet !p-1.5" aria-label={t('common.close')}
                        onClick={() => uploadManager.dismiss(it.id)}><X className="w-4 h-4" /></button>
              )}
            </div>
            <div className="mt-2"><ProgressBar value={s.loaded / Math.max(1, s.total)}
                 indeterminate={s.phase === 'finalizing' || s.phase === 'starting'}
                 tone={s.phase === 'failed' ? 'bad' : s.phase === 'complete' ? 'ok' : 'brand'}
                 label={t(`creator.upload.phase.${s.phase}`, { s: s.retryIn })} /></div>
            <div className="mt-1.5 text-xs text-ink-400">
              {it.projectId ? (
                <Link className="link" to={`/projects/${it.projectId}`} onClick={() => uploadManager.dismiss(it.id)}>
                  {t('creator.upload.openProject')}</Link>
              ) : it.projectError ? (
                <span className="text-bad">{it.projectError}{' '}
                  <button type="button" className="link" onClick={() => uploadManager.retryProject(it.id)}>
                    {t('common.retry')}</button></span>
              ) : it.autoStart ? t('creator.upload.projectStartsAfter')
                : s.phase === 'complete' ? <Link className="link" to="/new">{t('creator.upload.finishSetup')}</Link>
                  : s.phase === 'failed' ? (s.error?.message || '') : t(`creator.upload.phase.${s.phase}`, { s: s.retryIn })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
