// The customer's plan usage: source video minutes only – never a cost, a token or a model call.

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Gauge } from 'lucide-react'
import { api } from '../lib/api'
import type { Usage } from '../lib/types'
import { Card, ProgressBar, Skeleton } from './ds'

export function UsageCard({ detailed = false }: { detailed?: boolean }) {
  const { t, i18n } = useTranslation()
  const [u, setU] = useState<Usage | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    api.usage().then(setU).catch(() => setFailed(true))
  }, [])
  if (failed) return null
  if (!u) return <Skeleton className="h-24" />
  const pct = u.plan.minutes ? Math.min(1, u.used_minutes / u.plan.minutes) : 0
  const renew = new Date(u.period.end).toLocaleDateString(i18n.language === 'he' ? 'he-IL' : 'en-GB',
    { day: 'numeric', month: 'long' })
  return (
    <Card className="p-4" data-testid="usage-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-center gap-2">
          <Gauge className="w-4 h-4 text-brand-500" aria-hidden />
          <h2 className="font-semibold">{t('creator.usage.title')}</h2>
          <span className="text-sm text-ink-400">{t('creator.usage.plan', { name: u.plan.name, price: u.plan.price })}</span>
        </div>
        <span className="text-sm font-medium" data-testid="usage-remaining">
          {t('creator.usage.remaining', { count: u.remaining_minutes })}
        </span>
      </div>
      <div className="mt-3">
        <ProgressBar value={pct} tone={pct > 0.9 ? 'bad' : 'brand'}
                     label={t('creator.usage.used', { used: u.used_minutes, total: u.plan.minutes })} />
      </div>
      <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-ink-500">
        <span data-testid="usage-used"><span className="ltr-nums">{t('creator.usage.used', { used: u.used_minutes, total: u.plan.minutes })}</span></span>
        <span>{t('creator.usage.period', { date: renew })}</span>
      </div>
      {detailed && (
        <div className="mt-4 border-t border-ink-750 pt-3">
          <p className="text-xs text-ink-500 mb-2">{t('creator.usage.note')}</p>
          <h3 className="text-sm font-medium mb-2">{t('creator.usage.history')}</h3>
          {u.projects.length === 0 ? <p className="text-sm text-ink-400">{t('creator.usage.none')}</p> : (
            <ul className="divide-y divide-ink-750 text-sm">
              {u.projects.map((p) => (
                <li key={`${p.project_id}-${p.date}`} className="flex justify-between gap-3 py-1.5">
                  <span className="bidi-isolate truncate">{p.title || p.project_id}</span>
                  <span className="shrink-0 text-ink-400">
                    {p.status === 'processing' && <>{t('creator.usage.processing')} · </>}
                    {t('creator.usage.minutes', { count: p.minutes })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
