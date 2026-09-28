// סיכום הניתוח: רק מה שנמדד בפועל. מה שלא ממומש (זיהוי דוברים) מוצג ככזה.

import { useTranslation } from 'react-i18next'
import { AudioLines, Camera, FileText, Monitor, ScanFace, Sparkles, Users } from 'lucide-react'
import type { ProjectAnalysis } from '../lib/types'
import { formatDuration, formatNumber, formatPercent, iso } from '../lib/i18nFormat'
import { Badge, Callout, Card, CardHeader, HelpTip, Stat } from '../components/ds'

export default function AnalysisSummary({ a, onSeek }: {
  a: ProjectAnalysis
  onSeek?: (t: number) => void
}) {
  const { t } = useTranslation()
  const layouts = a.screen?.layouts || {}
  const totalLayout = Object.values(layouts).reduce((x, y) => x + y, 0)
  return (
    <Card>
      <CardHeader icon={<Sparkles className="w-5 h-5" />} title={t('project.analysis.title')}
                  subtitle={t('project.analysis.subtitle')} />
      <div className="px-5 pb-5 space-y-5">
        <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
          <Stat label={t('project.analysis.duration')} value={<span className="ltr-nums">{formatDuration(a.duration)}</span>} />
          <Stat label={t('project.analysis.language')}
                value={a.language ? t(`common.contentLanguage.${a.language}`, { defaultValue: a.language }) : '—'} />
          <Stat label={t('project.analysis.transcript')}
                value={a.transcript.available ? t('project.analysis.words', { count: a.transcript.words, formatted: formatNumber(a.transcript.words) }) : t('project.analysis.none')}
                tone={a.transcript.available ? undefined : 'warn'}
                hint={a.transcript.note || undefined} />
          <Stat label={t('project.analysis.moments')} value={formatNumber(a.moments?.count ?? 0)}
                hint={t('project.analysis.momentsHint')} />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-3">
            <Row icon={<AudioLines className="w-4 h-4" />} label={t('project.analysis.audio')}>
              {a.audio.available ? t('project.analysis.audioLine', {
                speech: iso(formatPercent(a.audio.speech_ratio)),
                silence: iso(formatDuration(a.audio.silence_seconds)),
              }) : t('project.analysis.noAudio')}
              {a.audio.loudness_lufs !== null && (
                <> · <span className="ltr-nums">{a.audio.loudness_lufs} LUFS</span></>
              )}
            </Row>
            <Row icon={<ScanFace className="w-4 h-4" />} label={t('project.analysis.faces')}>
              {a.faces.detected ? t('project.analysis.facesLine', { ratio: iso(formatPercent(a.faces.ratio)) })
                : t('project.analysis.noFaces')}
            </Row>
            <Row icon={<Camera className="w-4 h-4" />} label={t('project.analysis.facecam')}>
              {a.facecam.detected
                ? <>{t('project.analysis.facecamLine', { count: a.facecam.segments, time: iso(formatDuration(a.facecam.seconds)) })}
                    <Badge tone="ok" className="ms-2">{t('project.analysis.reactionReady')}</Badge></>
                : t('project.analysis.noFacecam')}
            </Row>
            <Row icon={<Users className="w-4 h-4" />} label={t('project.analysis.speakers')}>
              <span className="text-ink-500">{a.speakers.note}</span>
            </Row>
          </div>

          <div>
            <div className="text-xs font-medium text-ink-400 mb-2 flex items-center gap-1.5">
              <Monitor className="w-4 h-4" aria-hidden />{t('project.analysis.layouts')}
              <HelpTip text={t('project.analysis.layoutsHint')} />
            </div>
            {totalLayout > 0 ? (
              <>
                <div className="flex h-3 w-full overflow-hidden rounded-full bg-ink-800" aria-hidden>
                  {(['reaction', 'camera', 'screen'] as const).map((k) => (
                    <div key={k} style={{ width: `${(100 * (layouts[k] || 0)) / totalLayout}%` }}
                         className={{ reaction: 'bg-brand-600', camera: 'bg-ok', screen: 'bg-warn' }[k]} />
                  ))}
                </div>
                <ul className="mt-2 space-y-1 text-xs text-ink-400">
                  {(['reaction', 'camera', 'screen'] as const).map((k) => (
                    <li key={k} className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${{ reaction: 'bg-brand-600', camera: 'bg-ok', screen: 'bg-warn' }[k]}`} aria-hidden />
                      {t(`project.analysis.layoutKinds.${k}`)}
                      <span className="ms-auto ltr-nums">{formatDuration(layouts[k] || 0)}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : <p className="text-sm text-ink-500">{a.layout_note || t('project.analysis.noLayouts')}</p>}
          </div>
        </div>

        {a.moments?.top?.length ? (
          <div>
            <div className="text-xs font-medium text-ink-400 mb-2 flex items-center gap-1.5">
              <FileText className="w-4 h-4" aria-hidden />{t('project.analysis.topMoments')}
              <HelpTip text={t('project.scoreDisclaimer')} />
            </div>
            <ul className="divide-y divide-ink-750 rounded-xl ring-1 ring-ink-750">
              {a.moments.top.slice(0, 6).map((m) => (
                <li key={`${m.start}-${m.end}`} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <button type="button" onClick={() => onSeek?.(m.start)} disabled={!onSeek}
                          className="ltr-nums text-xs text-brand-600 hover:underline disabled:no-underline disabled:text-ink-500 shrink-0">
                    {formatDuration(m.start)}–{formatDuration(m.end)}
                  </button>
                  <span className="truncate text-ink-300 bidi-isolate">{m.title}</span>
                  <span className="ms-auto text-xs text-ink-500 ltr-nums shrink-0">{Math.round(m.score * 100)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {!a.transcript.available && (
          <Callout tone="warn" title={t('project.analysis.noTranscriptTitle')}>
            {t('project.analysis.noTranscriptBody')}
          </Callout>
        )}
      </div>
    </Card>
  )
}

function Row({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 text-ink-500 shrink-0">{icon}</div>
      <div className="min-w-0 text-sm">
        <div className="text-xs font-medium text-ink-400">{label}</div>
        <div className="text-ink-300">{children}</div>
      </div>
    </div>
  )
}
