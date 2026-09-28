// שלבי Mode ו-Settings. ההגדרות נשמרות בפרויקט (PATCH) תוך כדי עריכה,
// כך שאפשר לסגור את התוכנה ולחזור אליהן.

import { useTranslation } from 'react-i18next'
import { Clapperboard, Smartphone, Square, RectangleHorizontal, RectangleVertical } from 'lucide-react'
import type { ProjectAnalysis, ProjectConfig, ProjectMode, ProjectOptions } from '../lib/types'
import { formatMinutes } from '../lib/i18nFormat'
import {
  Callout, Card, CardHeader, Field, Segmented, Select, cx,
} from '../components/ds'
import SubtitleEditor from './SubtitleEditor'

export function ModePicker({ mode, onChange }: { mode: ProjectMode | null; onChange: (m: ProjectMode) => void }) {
  const { t } = useTranslation()
  const cards: { m: ProjectMode; Icon: typeof Smartphone }[] = [
    { m: 'short', Icon: Smartphone }, { m: 'longform', Icon: Clapperboard }]
  return (
    <div className="grid gap-4 md:grid-cols-2" role="radiogroup" aria-label={t('project.steps.mode')}>
      {cards.map(({ m, Icon }) => {
        const active = mode === m
        return (
          <button key={m} type="button" role="radio" aria-checked={active} onClick={() => onChange(m)}
                  className={cx('card p-5 text-start transition-shadow ring-1 ring-inset',
                                active ? 'ring-2 ring-brand-500 bg-brand-600/5' : 'ring-transparent hover:shadow-pop')}>
            <div className="flex items-center gap-3">
              <div className={cx('rounded-xl p-2.5', active ? 'bg-brand-600 text-on' : 'bg-ink-800 text-ink-400')}>
                <Icon className="w-5 h-5" aria-hidden />
              </div>
              <div>
                <div className="font-semibold text-ink-100">{t(`project.mode.${m}.title`)}</div>
                <div className="text-xs text-ink-500">{t(`project.mode.${m}.tagline`)}</div>
              </div>
            </div>
            <p className="mt-3 text-sm text-ink-400 leading-relaxed">{t(`project.mode.${m}.body`)}</p>
          </button>
        )
      })}
    </div>
  )
}

const ASPECT_ICON: Record<string, typeof Square> = {
  '9:16': RectangleVertical, '4:5': RectangleVertical, '1:1': Square, '16:9': RectangleHorizontal,
}

export function SettingsForm({ cfg, onChange, options, analysis, projectId }: {
  cfg: ProjectConfig
  onChange: (next: ProjectConfig) => void
  options: ProjectOptions | null
  analysis: ProjectAnalysis | null
  projectId: string
}) {
  const { t } = useTranslation()
  const set = <K extends keyof ProjectConfig>(k: K, v: ProjectConfig[K]) => onChange({ ...cfg, [k]: v })
  const short = cfg.mode !== 'longform'
  const lengths = options?.clip_lengths ?? [{ min: 15, max: 30 }, { min: 30, max: 60 }, { min: 60, max: 90 }]
  const lengthKey = `${cfg.clip_min_seconds}-${cfg.clip_max_seconds}`
  const known = lengths.some((l) => `${l.min}-${l.max}` === lengthKey)
  const subLang: 'he' | 'en' = cfg.content_language === 'en' ? 'en'
    : cfg.content_language === 'he' ? 'he' : (analysis?.language === 'en' ? 'en' : 'he')
  const facecam = analysis?.facecam?.detected

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title={short ? t('project.settings.shortTitle') : t('project.settings.longTitle')}
                    subtitle={short ? t('project.settings.shortSubtitle') : t('project.settings.longSubtitle')} />
        <div className="px-5 pb-5 grid gap-5 md:grid-cols-2">
          {short ? (
            <>
              <Field label={t('project.settings.aspect')} className="md:col-span-2">
                <Segmented<ProjectConfig['aspect_ratio']> value={cfg.aspect_ratio}
                  onChange={(v) => set('aspect_ratio', v)}
                  options={(options?.aspect_ratios ?? ['9:16', '1:1', '4:5', '16:9']).map((a) => {
                    const I = ASPECT_ICON[a] || Square
                    return {
                      value: a as ProjectConfig['aspect_ratio'],
                      label: <span className="inline-flex items-center gap-1.5"><I className="w-4 h-4" aria-hidden />
                        <span className="ltr-nums">{a}</span><span className="text-ink-500 hidden sm:inline">· {t(`project.settings.aspects.${a.replace(':', '_')}`)}</span></span>,
                    }
                  })} />
              </Field>
              <Field label={t('project.settings.clipLength')} htmlFor="len">
                <Select id="len" value={known ? lengthKey : 'custom'} onChange={(e) => {
                  const [a, b] = e.target.value.split('-').map(Number)
                  if (a && b) onChange({ ...cfg, clip_min_seconds: a, clip_max_seconds: b })
                }}>
                  {lengths.map((l) => (
                    <option key={`${l.min}-${l.max}`} value={`${l.min}-${l.max}`}>
                      {t('project.settings.seconds', { min: l.min, max: l.max })}
                    </option>
                  ))}
                  {!known && <option value="custom">{t('project.settings.seconds', { min: cfg.clip_min_seconds, max: cfg.clip_max_seconds })}</option>}
                </Select>
              </Field>
              <Field label={t('project.settings.clipCount')} htmlFor="cnt" hint={t('project.settings.clipCountHint')}>
                <Select id="cnt" value={cfg.clip_count} onChange={(e) => set('clip_count', Number(e.target.value))}>
                  {Array.from(new Set([...(options?.clip_counts ?? [3, 5, 8, 10, 15]), cfg.clip_count]))
                    .sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}</option>)}
                </Select>
              </Field>
              <Field label={t('project.settings.layout')} htmlFor="layout" className="md:col-span-2"
                     hint={t(`project.settings.layouts.${cfg.layout}.hint`)}>
                <Select id="layout" value={cfg.layout} disabled={cfg.aspect_ratio === '16:9'}
                        onChange={(e) => set('layout', e.target.value as ProjectConfig['layout'])}>
                  {(options?.layouts ?? ['auto', 'reaction', 'face', 'center', 'blur']).map((l) => (
                    <option key={l} value={l}>{t(`project.settings.layouts.${l}.label`)}</option>
                  ))}
                </Select>
              </Field>
              {cfg.aspect_ratio !== '16:9' && facecam && (
                <div className="md:col-span-2">
                  <Callout tone="brand" title={t('project.settings.facecamFound')}>{t('project.settings.facecamFoundBody')}</Callout>
                </div>
              )}
              {cfg.aspect_ratio === '16:9' && (
                <p className="md:col-span-2 hint">{t('project.settings.layoutNotNeeded')}</p>
              )}
            </>
          ) : (
            <>
              <Field label={t('project.settings.targetLength')} htmlFor="target" hint={t('project.settings.targetHint')}>
                <Select id="target" value={cfg.longform_target_seconds}
                        onChange={(e) => set('longform_target_seconds', Number(e.target.value))}>
                  {Array.from(new Set([...(options?.longform_targets ?? [600, 900, 1200, 1800]), cfg.longform_target_seconds]))
                    .sort((a, b) => a - b).map((s) => <option key={s} value={s}>{formatMinutes(s)}</option>)}
                </Select>
              </Field>
              <Field label={t('project.settings.aspect')}>
                <div className="field bg-ink-800 text-ink-400 ltr-nums">16:9</div>
              </Field>
              <div className="md:col-span-2">
                <Callout tone="neutral" title={t('project.settings.longHowTitle')}>{t('project.settings.longHow')}</Callout>
              </div>
            </>
          )}
          <Field label={t('import.contentLanguage')} htmlFor="clang" hint={t('project.settings.contentLanguageHint')}>
            <Select id="clang" value={cfg.content_language}
                    onChange={(e) => set('content_language', e.target.value as ProjectConfig['content_language'])}>
              {(['auto', 'he', 'en'] as const).map((l) => <option key={l} value={l}>{t(`common.contentLanguage.${l}`)}</option>)}
            </Select>
          </Field>
        </div>
      </Card>

      <Card>
        <CardHeader title={t('project.settings.subtitles')} subtitle={t('project.settings.subtitlesSubtitle')}
                    actions={<Segmented<string> size="sm" value={cfg.subtitles.enabled ? 'on' : 'off'}
                                                label={t('project.settings.subtitles')}
                                                onChange={(v) => onChange({ ...cfg, subtitles: { ...cfg.subtitles, enabled: v === 'on' } })}
                                                options={[{ value: 'off', label: t('project.settings.noSubtitles') },
                                                  { value: 'on', label: t('project.settings.addSubtitles') }]} />} />
        {cfg.subtitles.enabled ? (
          <div className="px-5 pb-5">
            {analysis && !analysis.transcript.available && (
              <div className="mb-4"><Callout tone="warn">{t('project.settings.subtitlesNoTranscript')}</Callout></div>
            )}
            <SubtitleEditor value={cfg.subtitles.style} language={subLang}
                            aspect={short ? cfg.aspect_ratio : '16:9'} projectId={projectId}
                            onChange={(style) => onChange({ ...cfg, subtitles: { ...cfg.subtitles, style } })} />
          </div>
        ) : (
          <div className="px-5 pb-5"><p className="hint">{t('project.settings.subtitlesOff')}</p></div>
        )}
      </Card>
      <SwitchNote />
    </div>
  )
}

function SwitchNote() {
  const { t } = useTranslation()
  return <p className="hint">{t('project.settings.savedAutomatically')}</p>
}

