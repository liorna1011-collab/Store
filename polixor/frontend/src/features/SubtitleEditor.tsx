// עורך סגנון כתוביות v2 עם תצוגה מקדימה אמיתית: התמונה מרונדרת בשרת עם
// אותו כותב ASS ואותו libass של הייצוא, כך שמה שרואים כאן הוא מה שייצרב.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ImageOff, RefreshCw, Type } from 'lucide-react'
import { api, PolixorApiError } from '../lib/api'
import type { FontInfo, PresetsResponse, SubtitlePreview, SubtitleStyle } from '../lib/types'
import {
  Badge, Button, Callout, ColorInput, Field, Segmented, Select, Skeleton, Slider, Spinner,
  Switch, cx,
} from '../components/ds'

interface Props {
  value: SubtitleStyle
  onChange: (next: SubtitleStyle) => void
  /** שפת הדיבור (לא שפת הממשק) – קובעת משפט דוגמה וכיווניות. */
  language: 'he' | 'en'
  aspect?: string
  projectId?: string
  clipId?: string
  compact?: boolean
}

function usePresets(language: string) {
  const [data, setData] = useState<PresetsResponse | null>(null)
  useEffect(() => {
    let alive = true
    api.subtitlePresets(language).then((d) => { if (alive) setData(d) }).catch(() => undefined)
    return () => { alive = false }
  }, [language])
  return data
}

function useFonts() {
  const [fonts, setFonts] = useState<FontInfo[] | null>(null)
  useEffect(() => { api.subtitleFonts().then((r) => setFonts(r.fonts)).catch(() => setFonts([])) }, [])
  return fonts
}

export default function SubtitleEditor({ value, onChange, language, aspect = '9:16', projectId, clipId, compact }: Props) {
  const { t, i18n } = useTranslation()
  const presets = usePresets(language)
  const fonts = useFonts()
  const set = <K extends keyof SubtitleStyle>(key: K, v: SubtitleStyle[K]) =>
    onChange({ ...value, [key]: v, preset: key === 'preset' ? (v as string) : null })
  const ui = i18n.resolvedLanguage === 'en' ? 'en' : 'he'

  const font = fonts?.find((f) => f.family === value.font)
  const coversLanguage = font ? (language === 'he' ? font.hebrew : font.latin) : true
  const weightInstalled = !font?.weights?.length || font.weights.includes(value.weight)
  const limits = presets?.limits

  return (
    <div className={cx('grid gap-6', compact ? '' : 'xl:grid-cols-[minmax(0,1fr)_minmax(260px,340px)]')}>
      <div className="space-y-6 min-w-0">
        {/* ---- presets ---- */}
        <section>
          <h3 className="section-title">{t('subtitles.presets')}</h3>
          <p className="hint mb-3">{t('subtitles.presetsHint')}</p>
          {!presets ? <Skeleton className="h-20" /> : (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {presets.presets.map((p) => (
                <button key={p.id} type="button" onClick={() => onChange({ ...p.style, font: value.font || p.style.font, preset: p.id })}
                        aria-pressed={value.preset === p.id}
                        className={cx('rounded-xl p-3 text-start ring-1 ring-inset transition-colors',
                                      value.preset === p.id ? 'bg-brand-600/10 ring-brand-500'
                                        : 'bg-ink-850 ring-ink-750 hover:ring-ink-600')}>
                  <div className="text-sm font-semibold text-ink-100">{p.label[ui] || p.id}</div>
                  <div className="mt-1 text-xs text-ink-500 leading-snug line-clamp-2">{p.description[ui]}</div>
                </button>
              ))}
            </div>
          )}
          {value.preset === null && <p className="mt-2 text-xs text-ink-500">{t('subtitles.customized')}</p>}
        </section>

        {/* ---- טקסט ---- */}
        <section className="space-y-4">
          <h3 className="section-title">{t('subtitles.sections.text')}</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('subtitles.font')} htmlFor="sub-font"
                   error={!coversLanguage ? t('subtitles.fontNoCoverage', { language: t(`common.contentLanguage.${language}`) }) : undefined}>
              <Select id="sub-font" value={value.font} onChange={(e) => set('font', e.target.value)}>
                {fonts?.some((f) => f.family === value.font) ? null
                  : <option value={value.font}>{value.font}</option>}
                {(fonts || []).map((f) => (
                  <option key={f.family} value={f.family}>
                    {f.family}{f.hebrew && f.latin ? '' : f.hebrew ? ` · ${t('subtitles.hebrewOnly')}` : ` · ${t('subtitles.latinOnly')}`}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t('subtitles.weight')}
                   hint={!weightInstalled ? t('subtitles.weightNotInstalled', { weights: font?.weights.join(', ') }) : undefined}>
              <Segmented<number> size="sm" value={value.weight} onChange={(v) => set('weight', v)}
                                 options={(limits?.weights || [400, 500, 600, 700, 800, 900]).map((w) => ({
                                   value: w, label: <span className={cx('ltr-nums', font?.weights?.length && !font.weights.includes(w) && 'opacity-50')}>{w}</span>,
                                 }))} />
            </Field>
            <Slider label={t('subtitles.size')} value={value.size} min={limits?.size[0] ?? 2}
                    max={limits?.size[1] ?? 12} step={0.1} onChange={(v) => set('size', v)}
                    format={(v) => t('subtitles.percentOfHeight', { value: v.toFixed(1) })} />
            <div className="grid grid-cols-2 gap-4">
              <Field label={t('subtitles.wordsPerLine')}>
                <Select value={value.words_per_line} onChange={(e) => set('words_per_line', Number(e.target.value))}>
                  {Array.from({ length: 12 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
                </Select>
              </Field>
              <Field label={t('subtitles.maxLines')}>
                <Select value={value.max_lines} onChange={(e) => set('max_lines', Number(e.target.value))}>
                  {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
                </Select>
              </Field>
            </div>
            <ColorInput label={t('subtitles.color')} value={value.color} onChange={(v) => set('color', v)} />
            <ColorInput label={t('subtitles.highlight')} value={value.highlight_color}
                        onChange={(v) => set('highlight_color', v)} />
          </div>
          <Switch checked={value.uppercase} onChange={(v) => set('uppercase', v)}
                  label={t('subtitles.uppercase')} description={t('subtitles.uppercaseHint')} />
        </section>

        {/* ---- רקע, מתאר, צל ---- */}
        <section className="space-y-4">
          <h3 className="section-title">{t('subtitles.sections.look')}</h3>
          <Field label={t('subtitles.background')}>
            <Segmented<SubtitleStyle['background']> value={value.background} onChange={(v) => set('background', v)}
                                                     options={(['none', 'box', 'bar'] as const).map((b) => ({ value: b, label: t(`subtitles.backgrounds.${b}`) }))} />
          </Field>
          {value.background !== 'none' && (
            <div className="grid gap-4 sm:grid-cols-2">
              <ColorInput label={t('subtitles.backgroundColor')} value={value.background_color}
                          onChange={(v) => set('background_color', v)} />
              <Slider label={t('subtitles.backgroundOpacity')} value={value.background_opacity} min={0} max={1}
                      step={0.05} onChange={(v) => set('background_opacity', v)}
                      format={(v) => `${Math.round(v * 100)}%`} />
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Slider label={t('subtitles.outline')} value={value.outline} min={limits?.outline[0] ?? 0}
                    max={limits?.outline[1] ?? 12} step={0.5} onChange={(v) => set('outline', v)} />
            <ColorInput label={t('subtitles.outlineColor')} value={value.outline_color}
                        onChange={(v) => set('outline_color', v)} />
            <Slider label={t('subtitles.shadow')} value={value.shadow} min={limits?.shadow[0] ?? 0}
                    max={limits?.shadow[1] ?? 8} step={0.5} onChange={(v) => set('shadow', v)} />
            <Slider label={t('subtitles.shadowOpacity')} value={value.shadow_opacity} min={0} max={1}
                    step={0.05} onChange={(v) => set('shadow_opacity', v)} format={(v) => `${Math.round(v * 100)}%`} />
          </div>
        </section>

        {/* ---- מיקום ואנימציה ---- */}
        <section className="space-y-4">
          <h3 className="section-title">{t('subtitles.sections.motion')}</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('subtitles.position')}>
              <Segmented<SubtitleStyle['position']> value={value.position} onChange={(v) => set('position', v)}
                                                     options={(['top', 'middle', 'bottom'] as const).map((p) => ({ value: p, label: t(`subtitles.positions.${p}`) }))} />
            </Field>
            <Slider label={t('subtitles.offset')} value={value.offset} min={limits?.offset[0] ?? 0}
                    max={limits?.offset[1] ?? 40} step={0.5} onChange={(v) => set('offset', v)}
                    disabled={value.position === 'middle'} hint={t('subtitles.offsetHint')}
                    format={(v) => t('subtitles.percentOfHeight', { value: v.toFixed(1) })} />
            <Field label={t('subtitles.animation')} htmlFor="sub-anim" hint={t(`subtitles.animationHints.${value.animation}`)}>
              <Select id="sub-anim" value={value.animation}
                      onChange={(e) => set('animation', e.target.value as SubtitleStyle['animation'])}>
                {(['none', 'fade', 'karaoke', 'word', 'pop', 'bounce'] as const).map((a) => (
                  <option key={a} value={a}>{t(`subtitles.animations.${a}`)}</option>
                ))}
              </Select>
            </Field>
          </div>
        </section>
      </div>

      <SubtitlePreviewPanel style={value} language={language} aspect={aspect}
                            projectId={projectId} clipId={clipId} />
    </div>
  )
}

export function SubtitlePreviewPanel({ style, language, aspect, projectId, clipId }: {
  style: SubtitleStyle
  language: 'he' | 'en'
  aspect: string
  projectId?: string
  clipId?: string
}) {
  const { t } = useTranslation()
  const [progress, setProgress] = useState(0.25)
  const [res, setRes] = useState<SubtitlePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const ctrl = useRef<AbortController | null>(null)
  const body = useMemo(() => JSON.stringify({ style, aspect, language, progress, project_id: projectId, clip_id: clipId }),
    [style, aspect, language, progress, projectId, clipId])

  useEffect(() => {
    const timer = setTimeout(async () => {
      ctrl.current?.abort()
      const c = new AbortController()
      ctrl.current = c
      setLoading(true)
      try {
        const r = await api.subtitlePreview(JSON.parse(body), c.signal)
        setRes(r)
        setError(null)
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return
        setError(e instanceof PolixorApiError ? e.message : t('subtitles.previewFailed'))
      } finally {
        if (ctrl.current === c) setLoading(false)
      }
    }, 280)
    return () => clearTimeout(timer)
  }, [body, nonce, t])

  const [w, h] = aspect.split(':').map(Number)
  return (
    <aside className="space-y-3 xl:sticky xl:top-20 self-start" aria-label={t('subtitles.preview')}>
      <div className="flex items-center justify-between">
        <h3 className="section-title !mb-0 inline-flex items-center gap-2"><Type className="w-4 h-4" />{t('subtitles.preview')}</h3>
        <div className="flex items-center gap-2">
          {loading && <Spinner className="w-4 h-4" />}
          <Button size="sm" variant="quiet" onClick={() => setNonce((n) => n + 1)}
                  icon={<RefreshCw className="w-3.5 h-3.5" />}>{t('common.refresh')}</Button>
        </div>
      </div>
      <div className="relative mx-auto overflow-hidden rounded-xl bg-ink-900 ring-1 ring-ink-750"
           style={{ aspectRatio: `${w} / ${h}`, maxHeight: '62vh' }}>
        {res && <img src={res.image} alt={t('subtitles.previewAlt')} className="h-full w-full object-contain" />}
        {!res && !error && <Skeleton className="absolute inset-0 rounded-none" />}
        {error && !res && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center text-sm text-ink-500">
            <ImageOff className="w-6 h-6" aria-hidden />{error}
          </div>
        )}
      </div>
      <Slider label={t('subtitles.previewMoment')} value={progress} min={0} max={0.95} step={0.05}
              onChange={setProgress} format={(v) => `${Math.round(v * 100)}%`} />
      {error && res && <Callout tone="bad">{error}</Callout>}
      {res && (
        <div className="space-y-1.5 text-xs text-ink-500">
          <p>{res.background_note}</p>
          <p>{t('subtitles.previewTruth', { w: res.target_width, h: res.target_height })}</p>
          <div className="flex flex-wrap gap-1.5">
            <Badge>{t('subtitles.linesShown', { count: res.lines[0]?.length ?? 0 })}</Badge>
            <Badge><span className="ltr-nums">{res.elapsed_ms} ms</span></Badge>
          </div>
        </div>
      )}
    </aside>
  )
}
