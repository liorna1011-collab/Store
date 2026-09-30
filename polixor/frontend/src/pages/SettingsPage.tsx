import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { PageHeader, Segmented } from '../components/ds'
import { useTheme } from '../lib/theme'
import { iso } from '../lib/i18nFormat'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { STAGE_LABEL, formatBytes } from '../lib/format'
import type {
  AppSettings, CaptionAnimation, EditStyleName, SettingsResponse, SystemInfo,
} from '../lib/types'
import { CaptionAnimationPicker, EditStylePicker } from '../components/editing'
import { DirectorSettings } from '../components/director-settings'
import {
  Chip, IconAlert, IconCheck, IconRefresh, IconTrash, Spinner,
} from '../components/ui'

type Tab = 'general' | 'analysis' | 'editing' | 'director' | 'clips' | 'subtitles' | 'ai'
  | 'images' | 'live' | 'system'

type MusicProfile = AppSettings['music_profile']

const MUSIC_PROFILES: MusicProfile[] = ['minimal', 'balanced', 'energetic']

const TABS: Tab[] = [
  'general', 'analysis', 'editing', 'director', 'clips', 'subtitles', 'ai', 'images', 'live', 'system',
]

function isTab(v: string | null): v is Tab {
  return v !== null && (TABS as string[]).includes(v)
}

export default function SettingsPage() {
  const { t } = useTranslation()
  const { pushToast, notifyError } = useStore()
  const [params, setParams] = useSearchParams()
  const [theme, setTheme] = useTheme()
  const [data, setData] = useState<SettingsResponse | null>(null)
  const [draft, setDraft] = useState<AppSettings | null>(null)
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [storage, setStorage] = useState<Record<string, any> | null>(null)
  const [benchmarks, setBenchmarks] = useState<Record<string, any> | null>(null)
  // הלשונית נקראת מה-URL (?tab=images), כך שקישור ממסך אחר פותח אותה ישירות.
  const requested = params.get('tab')
  const tab: Tab = isTab(requested) ? requested : 'general'
  const setTab = (next: Tab) => setParams(next === 'general' ? {} : { tab: next }, { replace: true })
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, sys] = await Promise.all([api.getSettings(), api.system()])
      setData(s)
      setDraft(s.values)
      setSystem(sys)
      api.storage().then(setStorage).catch(() => undefined)
      api.benchmarks().then(setBenchmarks).catch(() => undefined)
    } catch (e) { notifyError(e, t('settings.loadFailed')) }
  }, [notifyError, t])

  useEffect(() => { void load() }, [load])

  const set = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) =>
    setDraft((prev) => prev ? { ...prev, [key]: value } : prev)

  const dirty = Boolean(data && draft &&
    JSON.stringify(data.values) !== JSON.stringify(draft))

  const save = async () => {
    if (!draft) return
    setSaving(true)
    try {
      const res = await api.updateSettings(draft as unknown as Record<string, unknown>)
      setData(res)
      setDraft(res.values)
      pushToast({ tone: 'success', title: t('settings.saved') })
    } catch (e) { notifyError(e, t('settings.saveFailed')) } finally { setSaving(false) }
  }

  if (!draft || !data) {
    return <div className="max-w-4xl mx-auto space-y-4">
      <div className="skeleton h-10 w-64" /><div className="skeleton h-96" />
    </div>
  }

  return (
    <div className="max-w-4xl mx-auto">
      <PageHeader title={t('settings.title')} subtitle={t('settings.subtitle')} />

      <div role="tablist" aria-label={t('settings.tabsLabel')}
           className="flex sm:flex-wrap gap-x-1 mb-5 border-b border-ink-750 overflow-x-auto sm:overflow-visible">
        {TABS.map((key) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
                  className={`px-3 sm:px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap
                    transition-colors ${tab === key
                      ? 'border-brand-500 text-ink-100'
                      : 'border-transparent text-ink-400 hover:text-ink-200'}`}>
            {t(`settings.tabs.${key}`)}
          </button>
        ))}
      </div>

      {tab === 'general' && (
        <div className="space-y-5">
          <Section title={t('settings.general.title')}>
            <Row label={t('settings.general.theme')} hint={t('settings.general.themeHint')}>
              <Segmented value={theme} onChange={setTheme} label={t('settings.general.theme')}
                         options={[
                           { value: 'light', label: t('common.theme.light') },
                           { value: 'dark', label: t('common.theme.dark') },
                         ]} />
            </Row>
          </Section>
        </div>
      )}

      {tab === 'analysis' && (
        <div className="space-y-5">
          <Section title={t('settings.analysis.moments')}>
            <Slider label={t('settings.analysis.sensitivity')} value={draft.sensitivity} min={0} max={1} step={0.05}
                    display={`${Math.round(draft.sensitivity * 100)}%`}
                    hint={t('settings.analysis.sensitivityHint')}
                    onChange={(v) => set('sensitivity', v)} />
            <Slider label={t('settings.analysis.sampleFps')} value={draft.visual_sample_fps}
                    min={0.25} max={4} step={0.25} display={t('settings.units.perSec', { value: draft.visual_sample_fps })}
                    hint={t('settings.analysis.sampleFpsHint')}
                    onChange={(v) => set('visual_sample_fps', v)} />
            <Row label={t('settings.analysis.profile')} hint={t('settings.analysis.profileHint')}>
              <Select value={draft.performance_profile ?? 'auto'}
                      onChange={(v) => set('performance_profile', v as any)}
                      options={[['auto', t('settings.analysis.profiles.auto')],
                                ['fast', t('settings.analysis.profiles.fast')],
                                ['quality', t('settings.analysis.profiles.quality')]]} />
            </Row>
            <Row label={t('settings.analysis.longSource')} hint={t('settings.analysis.longSourceHint')}>
              <NumberInput value={draft.long_source_minutes ?? 20} min={1} max={600} step={5}
                           onChange={(v) => set('long_source_minutes', Math.round(v))}
                           suffix={t('settings.units.min')} />
            </Row>
            <Row label={t('settings.analysis.padBefore')} hint={t('settings.analysis.padBeforeHint')}>
              <NumberInput value={draft.context_pad_before} min={0} max={15} step={0.5}
                           onChange={(v) => set('context_pad_before', v)} suffix={t('settings.units.sec')} />
            </Row>
            <Row label={t('settings.analysis.padAfter')}>
              <NumberInput value={draft.context_pad_after} min={0} max={15} step={0.5}
                           onChange={(v) => set('context_pad_after', v)} suffix={t('settings.units.sec')} />
            </Row>
            <Toggle label={t('settings.analysis.chat')}
                    hint={t('settings.analysis.chatHint')}
                    checked={draft.use_chat_signal}
                    onChange={(v) => set('use_chat_signal', v)} />
          </Section>

          <Section title={t('settings.analysis.transcription')}>
            <Row label={t('settings.analysis.engine')}>
              <Select value={draft.transcript_provider}
                      onChange={(v) => set('transcript_provider', v)}
                      options={[
                        ['faster-whisper', t('settings.analysis.engineLocal')],
                        ['none', t('settings.analysis.engineNone')],
                      ]} />
            </Row>
            {draft.transcript_provider === 'faster-whisper' && (
              <>
                <Row label={t('settings.analysis.model')} hint={t('settings.analysis.modelHint')}>
                  <Select value={draft.whisper_model}
                          onChange={(v) => set('whisper_model', v)}
                          options={[
                            ['auto', t('settings.analysis.models.auto')],
                            ['tiny', t('settings.analysis.models.tiny')],
                            ['base', t('settings.analysis.models.base')],
                            ['small', t('settings.analysis.models.small')],
                            ['medium', t('settings.analysis.models.medium')],
                            ['large-v3-turbo', t('settings.analysis.models.turbo')],
                            ['large-v3', t('settings.analysis.models.large')],
                            ['hebrew', t('settings.analysis.models.hebrew')],
                            ...(['auto', 'tiny', 'base', 'small', 'medium', 'large-v3-turbo', 'large-v3', 'hebrew']
                              .includes(draft.whisper_model) ? [] : [[draft.whisper_model, draft.whisper_model] as [string, string]]),
                          ]} />
                </Row>
                <Row label={t('settings.analysis.vocabulary')} hint={t('settings.analysis.vocabularyHint')}>
                  <textarea className="field min-h-[88px]" dir="auto"
                            value={(draft.asr_vocabulary ?? []).join('\n')}
                            onChange={(e) => set('asr_vocabulary', e.target.value.split('\n'))} />
                </Row>
                <Toggle label={t('settings.analysis.timingRepair')} hint={t('settings.analysis.timingRepairHint')}
                        checked={draft.subtitle_timing_repair ?? true}
                        onChange={(v) => set('subtitle_timing_repair', v)} />
                <Toggle label={t('settings.analysis.forcedAlignment')} hint={t('settings.analysis.forcedAlignmentHint')}
                        checked={draft.subtitle_forced_alignment ?? false}
                        onChange={(v) => set('subtitle_forced_alignment', v)} />
                <Toggle label={t('settings.analysis.cloudFallback')} hint={t('settings.analysis.cloudFallbackHint')}
                        checked={draft.asr_cloud_fallback ?? false}
                        onChange={(v) => set('asr_cloud_fallback', v)} />
                <Row label={t('settings.analysis.device')}>
                  <Select value={draft.whisper_device}
                          onChange={(v) => set('whisper_device', v)}
                          options={(['auto', 'cpu', 'cuda'] as const).map((d) => [d, t(`settings.analysis.devices.${d}`)])} />
                </Row>
                <Row label={t('settings.analysis.speechLanguage')}>
                  <Select value={draft.transcribe_language}
                          onChange={(v) => set('transcribe_language', v)}
                          options={(['auto', 'he', 'en'] as const).map((l) => [l, t(`common.contentLanguage.${l}`)])} />
                </Row>
                {system && !system.modules.faster_whisper.available && (
                  <Warning>{t('settings.analysis.whisperMissing')}</Warning>
                )}
                <p className="hint">{t('settings.analysis.firstDownload')}</p>
              </>
            )}
            {draft.transcript_provider === 'none' && (
              <Warning tone="info">{t('settings.analysis.noTranscript')}</Warning>
            )}
          </Section>
        </div>
      )}

      {tab === 'editing' && (
        <div className="space-y-5">
          <Section title={t('settings.editing.howTitle')}>
            <p className="hint">{t('settings.editing.howBody')}</p>
          </Section>

          <Section title={t('settings.editing.shorts')}>
            <EditStylePicker value={draft.edit_style_short}
                             onChange={(v: EditStyleName) => set('edit_style_short', v)} />
          </Section>

          <Section title={t('settings.editing.long')}>
            <EditStylePicker value={draft.edit_style_long}
                             onChange={(v: EditStyleName) => set('edit_style_long', v)} />
          </Section>

          <Section title={t('settings.editing.fine')}>
            <Toggle label={t('settings.editing.removeSilence')}
                    hint={t('settings.editing.removeSilenceHint')}
                    checked={draft.remove_silence}
                    onChange={(v) => set('remove_silence', v)} />
            <Toggle label={t('settings.editing.angle')}
                    hint={t('settings.editing.angleHint')}
                    checked={draft.angle_changes}
                    onChange={(v) => set('angle_changes', v)} />
            <Row label={t('settings.editing.minGap')}
                 hint={t('settings.editing.minGapHint')}>
              <NumberInput value={draft.silence_min_gap} min={0} max={2} step={0.05}
                           onChange={(v) => set('silence_min_gap', v)} suffix={t('settings.units.sec')} />
            </Row>
            <Row label={t('settings.editing.maxRemoved')}
                 hint={t('settings.editing.maxRemovedHint')}>
              <NumberInput value={draft.max_removed_ratio} min={0} max={0.7} step={0.05}
                           onChange={(v) => set('max_removed_ratio', v)} />
            </Row>
            <Warning tone="info">{t('settings.editing.dramatic')}</Warning>
          </Section>

          <Section title={t('settings.editing.music')}>
            <Toggle label={t('settings.editing.musicEnable')} checked={draft.music_enabled}
                    onChange={(v) => set('music_enabled', v)} />
            {draft.music_enabled && (
              <>
                <Row label={t('settings.editing.musicFile')}
                     hint={t('settings.editing.musicFileHint')}>
                  <input className="field ltr-nums" dir="ltr"
                         value={draft.music_path}
                         placeholder="C:\\Music\\track.mp3"
                         onChange={(e) => set('music_path', e.target.value)} />
                </Row>
                <Row label={t('settings.editing.presence')}
                     hint={t('settings.editing.presenceHint')}>
                  <div className="grid grid-cols-3 gap-2">
                    {MUSIC_PROFILES.map((key) => (
                      <button key={key}
                              onClick={() => set('music_profile', key)}
                              className={`btn btn-sm ${draft.music_profile === key
                                ? 'bg-brand-600/15 text-brand-600 ring-1 ring-brand-500/40'
                                : 'bg-ink-850 text-ink-400 border border-ink-700 hover:text-ink-100'}`}
                              aria-pressed={draft.music_profile === key}>
                        {t(`settings.editing.profiles.${key}`)}
                      </button>
                    ))}
                  </div>
                </Row>
                <Warning tone="info">{t('settings.editing.musicNote')}</Warning>
              </>
            )}
          </Section>

          <Section title={t('settings.editing.captionAnim')}>
            <CaptionAnimationPicker value={draft.subtitle_animation}
                                    onChange={(v: CaptionAnimation) =>
                                      set('subtitle_animation', v)}
                                    disabled={!draft.subtitles_enabled} />
            {!draft.subtitles_enabled && (
              <p className="hint">{t('settings.editing.captionsOff')}</p>
            )}
          </Section>
        </div>
      )}

      {tab === 'director' && (
        <DirectorSettings draft={draft} set={set} Section={Section}
                          Toggle={Toggle} Warning={Warning} />
      )}

      {tab === 'clips' && (
        <div className="space-y-5">
          <Section title={t('settings.editing.long')}>
            <Toggle label={t('settings.clips.longEnable')} checked={draft.long_enabled}
                    onChange={(v) => set('long_enabled', v)} />
            {draft.long_enabled && (
              <>
                <Row label={t('settings.clips.type')}>
                  <Select value={draft.long_mode} onChange={(v) => set('long_mode', v as any)}
                          options={[
                            ['continuous', t('settings.clips.longModes.continuous')],
                            ['highlights', t('settings.clips.longModes.highlights')],
                          ]} />
                </Row>
                <Row label={t('settings.clips.count')}>
                  <NumberInput value={draft.long_count} min={0} max={20} step={1}
                               onChange={(v) => set('long_count', Math.round(v))} />
                </Row>
                <Row label={t('settings.clips.minLen')}>
                  <NumberInput value={draft.long_min_seconds / 60} min={0.5} max={60} step={0.5}
                               onChange={(v) => set('long_min_seconds', Math.round(v * 60))}
                               suffix={t('settings.units.min')} />
                </Row>
                <Row label={t('settings.clips.maxLen')}>
                  <NumberInput value={draft.long_max_seconds / 60} min={1} max={90} step={0.5}
                               onChange={(v) => set('long_max_seconds', Math.round(v * 60))}
                               suffix={t('settings.units.min')} />
                </Row>
                <Row label={t('settings.clips.resolution')}>
                  <Select value={draft.long_resolution}
                          onChange={(v) => set('long_resolution', v)}
                          options={[['1920x1080', '1080p'], ['1280x720', '720p'],
                                    ['2560x1440', '1440p']]} />
                </Row>
              </>
            )}
          </Section>

          <Section title={t('settings.publishing.title')}>
            <Toggle label={t('settings.publishing.sandbox')} hint={t('settings.publishing.sandboxHint')}
                    checked={draft.publish_sandbox ?? false}
                    onChange={(v) => set('publish_sandbox', v)} />
            <Row label={t('settings.publishing.grace')} hint={t('settings.publishing.graceHint')}>
              <NumberInput value={draft.publish_missed_grace_minutes ?? 360} min={0} max={10080} step={30}
                           onChange={(v) => set('publish_missed_grace_minutes', Math.round(v))}
                           suffix={t('settings.units.min')} />
            </Row>
            <Row label={t('settings.publishing.baseUrl')} hint={t('settings.publishing.baseUrlHint')}>
              <input className="field" dir="ltr" placeholder="https://polixor.example.com"
                     value={draft.public_base_url ?? ''}
                     onChange={(e) => set('public_base_url', e.target.value)} />
            </Row>
          </Section>

          <Section title={t('settings.clips.shorts')}>
            <Toggle label={t('settings.clips.shortEnable')} checked={draft.short_enabled}
                    onChange={(v) => set('short_enabled', v)} />
            {draft.short_enabled && (
              <>
                <Row label={t('settings.clips.maxCount')} hint={t('settings.clips.maxCountHint')}>
                  <NumberInput value={draft.short_count} min={0} max={30} step={1}
                               onChange={(v) => set('short_count', Math.round(v))} />
                </Row>
                <Row label={t('settings.clips.engine')}>
                  <Select value={draft.selection_engine ?? 'intel'}
                          onChange={(v) => set('selection_engine', v as any)}
                          options={[['intel', t('settings.clips.engines.intel')],
                                    ['legacy', t('settings.clips.engines.legacy')]]} />
                </Row>
                {(draft.selection_engine ?? 'intel') === 'intel' && (
                  <Slider label={t('settings.clips.qualityBar')} value={draft.clip_min_quality ?? 0.5}
                          min={0.2} max={0.9} step={0.05}
                          display={(draft.clip_min_quality ?? 0.5).toFixed(2)}
                          hint={t('settings.clips.qualityBarHint')}
                          onChange={(v) => set('clip_min_quality', v)} />
                )}
                {(draft.selection_engine ?? 'intel') === 'intel' && (
                  <Toggle label={t('settings.clips.llmJudge')} hint={t('settings.clips.llmJudgeHint')}
                          checked={draft.clip_llm_judge ?? true}
                          onChange={(v) => set('clip_llm_judge', v)} />
                )}
                <Row label={t('settings.clips.minLen')}>
                  <NumberInput value={draft.short_min_seconds} min={3} max={180} step={1}
                               onChange={(v) => set('short_min_seconds', Math.round(v))}
                               suffix={t('settings.units.sec')} />
                </Row>
                <Row label={t('settings.clips.maxLen')}>
                  <NumberInput value={draft.short_max_seconds} min={5} max={300} step={1}
                               onChange={(v) => set('short_max_seconds', Math.round(v))}
                               suffix={t('settings.units.sec')} />
                </Row>
                <Row label={t('settings.clips.layout')}
                     hint={draft.short_layout === 'blur_pad'
                       ? t('settings.clips.layoutBlurHint')
                       : t('settings.clips.layoutFaceHint')}>
                  <Select value={draft.short_layout}
                          onChange={(v) => set('short_layout', v as any)}
                          options={(['center', 'auto_face', 'split', 'blur_pad'] as const)
                            .map((l) => [l, t(`settings.clips.layouts.${l}`)])} />
                </Row>
                <Row label={t('settings.clips.resolution')}>
                  <Select value={draft.short_resolution}
                          onChange={(v) => set('short_resolution', v)}
                          options={[['1080x1920', '1080×1920'], ['720x1280', '720×1280']]} />
                </Row>
              </>
            )}
          </Section>

          <Section title={t('settings.clips.export')}>
            <Row label={t('settings.clips.maxClips')}>
              <NumberInput value={draft.max_clips_total} min={1} max={100} step={1}
                           onChange={(v) => set('max_clips_total', Math.round(v))} />
            </Row>
            <Row label={t('settings.clips.quality')}>
              <Select value={draft.video_quality} onChange={(v) => set('video_quality', v as any)}
                      options={(['high', 'medium', 'low'] as const)
                        .map((q) => [q, t(`settings.clips.qualities.${q}`)])} />
            </Row>
            <Row label={t('settings.clips.hw')}
                 hint={system?.gpu.nvenc ? t('settings.clips.hwNvenc') : t('settings.clips.hwNone')}>
              <Select value={draft.hw_accel} onChange={(v) => set('hw_accel', v)}
                      options={[
                        ['none', t('settings.clips.hwCpu')],
                        ['nvenc', 'NVIDIA NVENC'],
                        ['qsv', 'Intel QuickSync'],
                        ['videotoolbox', 'Apple VideoToolbox'],
                      ]} />
            </Row>
            <Toggle label={t('settings.clips.normalize')}
                    hint={t('settings.clips.normalizeHint')}
                    checked={draft.audio_normalize}
                    onChange={(v) => set('audio_normalize', v)} />
            <Row label={t('settings.clips.exportDir')}
                 hint={t('settings.clips.exportDirHint')}>
              <input className="field ltr-nums" dir="ltr" value={draft.export_dir}
                     placeholder={system?.data_dir ? `${system.data_dir}\\exports` : ''}
                     onChange={(e) => set('export_dir', e.target.value)} />
            </Row>
            <Row label={t('settings.clips.concurrent')}
                 hint={t('settings.clips.concurrentHint')}>
              <NumberInput value={draft.concurrent_jobs} min={1} max={4} step={1}
                           onChange={(v) => set('concurrent_jobs', Math.round(v))} />
            </Row>
          </Section>
        </div>
      )}

      {tab === 'subtitles' && (
        <div className="space-y-5">
          <Section title={t('settings.subtitles.title')}>
            <Warning tone="info">{t('settings.subtitles.scope')}</Warning>
            <Toggle label={t('settings.subtitles.burn')} checked={draft.subtitles_enabled}
                    onChange={(v) => set('subtitles_enabled', v)} />
            {draft.subtitles_enabled && (
              <>
                <Toggle label={t('settings.subtitles.wordLevel')}
                        hint={t('settings.subtitles.wordLevelHint')}
                        checked={draft.subtitle_word_level}
                        onChange={(v) => set('subtitle_word_level', v)} />
                <Row label={t('settings.subtitles.font')}
                     hint={t('settings.subtitles.fontHint')}>
                  <input className="field" value={draft.subtitle_font}
                         onChange={(e) => set('subtitle_font', e.target.value)} />
                </Row>
                <Slider label={t('settings.subtitles.size')} value={draft.subtitle_size} min={16} max={120} step={2}
                        display={String(draft.subtitle_size)}
                        onChange={(v) => set('subtitle_size', Math.round(v))} />
                <div className="grid grid-cols-2 gap-4">
                  <Row label={t('settings.subtitles.textColor')}>
                    <input type="color" value={draft.subtitle_color}
                           className="w-full h-9 rounded-lg bg-ink-800 border border-ink-700"
                           onChange={(e) => set('subtitle_color', e.target.value)} />
                  </Row>
                  <Row label={t('settings.subtitles.outlineColor')}>
                    <input type="color" value={draft.subtitle_outline_color}
                           className="w-full h-9 rounded-lg bg-ink-800 border border-ink-700"
                           onChange={(e) => set('subtitle_outline_color', e.target.value)} />
                  </Row>
                </div>
                <Row label={t('settings.subtitles.position')}>
                  <Select value={draft.subtitle_position}
                          onChange={(v) => set('subtitle_position', v as any)}
                          options={(['bottom', 'middle', 'top'] as const).map((v) => [v, t(`settings.subtitles.positions.${v}`)])} />
                </Row>
                <Toggle label={t('settings.subtitles.titleCard')}
                        hint={t('settings.subtitles.titleCardHint')}
                        checked={draft.title_card_enabled}
                        onChange={(v) => set('title_card_enabled', v)} />
                <p className="hint">{t('settings.subtitles.fromTranscript')}</p>
              </>
            )}
          </Section>
        </div>
      )}

      {tab === 'ai' && (
        <div className="space-y-5">
          <Section title={t('settings.ai.engine')}>
            <Row label={t('settings.ai.mode')}>
              <Select value={draft.ai_mode} onChange={(v) => set('ai_mode', v as any)}
                      options={[
                        ['heuristic', t('settings.ai.modes.heuristic')],
                        ['ollama', t('settings.ai.modes.ollama')],
                        ['cloud', t('settings.ai.modes.cloud')],
                      ]} />
            </Row>

            {draft.ai_mode === 'heuristic' && (
              <Warning tone="info">{t('settings.ai.heuristicNote')}</Warning>
            )}

            {draft.ai_mode === 'ollama' && (
              <>
                <Row label={t('settings.ai.modelName')} hint={t('settings.ai.modelNameHint')}>
                  <input className="field ltr-nums" dir="ltr" value={draft.ai_model}
                         onChange={(e) => set('ai_model', e.target.value)} />
                </Row>
                <Warning tone="info">{t('settings.ai.ollamaNote')}</Warning>
              </>
            )}

            {draft.ai_mode === 'cloud' && (
              <>
                <Row label={t('settings.ai.provider')}>
                  <Select value={draft.ai_provider}
                          onChange={(v) => set('ai_provider', v as any)}
                          options={[['anthropic', 'Anthropic'], ['openai', 'OpenAI']]} />
                </Row>
                <Row label={t('settings.analysis.model')}>
                  <input className="field ltr-nums" dir="ltr" value={draft.ai_model}
                         onChange={(e) => set('ai_model', e.target.value)} />
                </Row>
                <SecretField
                  name={draft.ai_provider === 'openai' ? 'openai_api_key' : 'anthropic_api_key'}
                  label={t('settings.ai.apiKey', { provider: draft.ai_provider === 'openai' ? 'OpenAI' : 'Anthropic' })}
                  state={data.secrets[draft.ai_provider === 'openai'
                    ? 'openai_api_key' : 'anthropic_api_key']}
                  onSaved={() => void load()}
                />
                <Warning>{t('settings.ai.cloudNote')}</Warning>
              </>
            )}

            <Toggle label={t('settings.ai.discover')}
                    hint={t('settings.ai.discoverHint')}
                    checked={draft.ai_discover_moments}
                    onChange={(v) => set('ai_discover_moments', v)}
                    disabled={draft.ai_mode === 'heuristic'} />

            <AiTester />
          </Section>

          <Section title={t('settings.ai.restricted')}>
            <SecretField
              name="cookiefile_path"
              label={t('settings.ai.cookies')}
              state={data.secrets.cookiefile_path}
              placeholder="C:\Users\...\cookies.txt"
              onSaved={() => void load()}
            />
            <p className="hint">{t('settings.ai.cookiesNote')}</p>
          </Section>
        </div>
      )}

      {tab === 'images' && (
        <div className="space-y-5">
          <Section title={t('settings.images.title')}>
            <Row label={t('settings.ai.provider')}>
              <Select value={draft.image_provider}
                      onChange={(v) => set('image_provider', v as any)}
                      options={[
                        ['openai', 'OpenAI Images'],
                        ['placeholder', t('settings.images.providers.placeholder')],
                      ]} />
            </Row>

            {draft.image_provider === 'openai' && (
              <>
                <Row label={t('settings.analysis.model')}>
                  <Select value={draft.image_model}
                          onChange={(v) => set('image_model', v as any)}
                          options={[
                            ['gpt-image-1', 'gpt-image-1'],
                            ['dall-e-3', 'dall-e-3'],
                            ['dall-e-2', 'dall-e-2'],
                          ]} />
                </Row>
                <Row label={t('settings.clips.quality')}>
                  <Select value={draft.image_quality}
                          onChange={(v) => set('image_quality', v as any)}
                          options={(['low', 'medium', 'high'] as const)
                            .map((q) => [q, t(`settings.images.qualities.${q}`)])} />
                </Row>
                <SecretField
                  name="openai_api_key"
                  label={t('settings.images.apiKey')}
                  state={data.secrets.openai_api_key}
                  onSaved={() => void load()}
                />
                <Warning>{t('settings.images.openaiNote')}</Warning>
              </>
            )}

            {draft.image_provider === 'placeholder' && (
              <Warning>{t('settings.images.placeholderNote')}</Warning>
            )}

            <Row label={t('settings.images.timeout')}>
              <input type="number" min={15} max={600} className="field w-28 ltr-nums"
                     value={draft.image_timeout_seconds}
                     onChange={(e) => set('image_timeout_seconds',
                                          Number(e.target.value) as any)} />
            </Row>
            <Row label={t('settings.images.retries')}>
              <input type="number" min={0} max={5} className="field w-28 ltr-nums"
                     value={draft.image_retries}
                     onChange={(e) => set('image_retries', Number(e.target.value) as any)} />
            </Row>
            <p className="hint">{t('settings.images.retriesNote')}</p>
            <p className="hint">{t('settings.images.serverOnly')}</p>
          </Section>
        </div>
      )}

      {tab === 'live' && (
        <div className="space-y-5">
          <Section title={t('settings.live.title')}>
            <Row label={t('settings.live.segment')}>
              <input type="number" min={30} max={1800} step={30}
                     className="field w-28 ltr-nums"
                     value={draft.live_segment_seconds}
                     onChange={(e) => set('live_segment_seconds',
                                          Number(e.target.value) as any)} />
            </Row>
            <p className="hint">{t('settings.live.segmentNote')}</p>

            <Row label={t('settings.live.maxMinutes')}>
              <input type="number" min={0} max={1440}
                     className="field w-28 ltr-nums"
                     value={draft.live_max_minutes}
                     onChange={(e) => set('live_max_minutes',
                                          Number(e.target.value) as any)} />
            </Row>
            <p className="hint">{t('settings.live.maxMinutesNote')}</p>

            <Toggle label={t('settings.live.keep')}
                    hint={t('settings.live.keepHint')}
                    checked={draft.live_keep_segments}
                    onChange={(v) => set('live_keep_segments', v)} />
          </Section>
        </div>
      )}

      {tab === 'system' && (
        <div className="space-y-5">
          <Section title={t('settings.system.environment')}>
            {system ? (
              <div className="space-y-2">
                <InfoRow label={t('settings.system.version')} value={`${system.app.name} ${system.app.version}`} />
                <InfoRow label={t('settings.system.platform')} value={system.platform} />
                <InfoRow label="Python" value={system.python} />
                <InfoRow label={t('settings.system.cpus')} value={String(system.cpu_count)} />
                <InfoRow label="FFmpeg"
                         value={system.ffmpeg.available ? system.ffmpeg.path : t('settings.system.notFound')}
                         tone={system.ffmpeg.available ? 'ok' : 'bad'} />
                {Object.entries(system.modules).map(([name, m]) => (
                  <InfoRow key={name} label={name}
                           value={m.available ? (m.version || t('settings.system.installed')) : t('settings.system.notInstalled')}
                           tone={m.available ? 'ok' : 'warn'} />
                ))}
                <InfoRow label="GPU"
                         value={system.gpu.cuda ? system.gpu.name : t('settings.system.notDetected')}
                         tone={system.gpu.cuda ? 'ok' : undefined} />
                <InfoRow label="NVENC" value={system.gpu.nvenc ? t('settings.system.available') : t('settings.system.unavailable')}
                         tone={system.gpu.nvenc ? 'ok' : undefined} />
                <InfoRow label={t('settings.system.dataDir')} value={system.data_dir} />
                <InfoRow label={t('settings.system.freeSpace')} value={formatBytes(system.free_disk_bytes)} />
              </div>
            ) : <Spinner />}

            {system?.warnings.map((w, i) => <Warning key={i}>{w}</Warning>)}
          </Section>

          {storage && (
            <Section title={t('settings.system.disk')}>
              <div className="space-y-2">
                <InfoRow label={t('settings.system.sources')} value={storage.sources_human} />
                <InfoRow label={t('settings.system.work')} value={storage.work_human} />
                <InfoRow label={t('settings.system.exports')} value={storage.exports_human} />
                <InfoRow label={t('settings.system.jobs')} value={String(storage.job_count)} />
                <InfoRow label={t('settings.system.clips')} value={String(storage.clip_count)} />
              </div>
              <CleanupButton onDone={() => api.storage().then(setStorage)} />
            </Section>
          )}

          <Section title={t('settings.system.benchmarks')}>
            {benchmarks?.has_data ? (
              <>
                <p className="hint mb-3">{t('settings.system.benchmarksNote')}</p>
                <div className="space-y-2">
                  {(benchmarks.stages as any[])
                    .filter((s) => s.ratio !== null)
                    .map((s) => (
                      <InfoRow key={s.stage} label={STAGE_LABEL[s.stage] ?? s.stage}
                               value={t('settings.system.ratio', { ratio: Number(s.ratio).toFixed(3), count: s.samples })} />
                    ))}
                </div>
              </>
            ) : (
              <p className="hint">{t('settings.system.noBenchmarks')}</p>
            )}
          </Section>
        </div>
      )}

      {/* ---- שמירה: צמוד לתחתית אזור התוכן, בלי תלות בצד שבו נמצא התפריט ---- */}
      {dirty && (
        <div className="sticky bottom-3 z-20 mt-6 rounded-xl border border-ink-700 bg-ink-850/95
                        backdrop-blur shadow-pop px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm text-ink-300" role="status">{t('settings.unsaved')}</span>
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={() => setDraft(data.values)}>
                {t('settings.discard')}
              </button>
              <button className="btn-primary" onClick={() => void save()} disabled={saving}>
                {saving ? <Spinner className="w-4 h-4" /> : <IconCheck className="w-4 h-4" />}
                {t('settings.saveAll')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// רכיבי טופס
// --------------------------------------------------------------------------
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card-pad">
      <h2 className="text-sm font-semibold text-ink-100 mb-4">{title}</h2>
      <div className="space-y-4">{children}</div>
    </section>
  )
}

function Row({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <div>
      <label className="label">{label}</label>
      {children}
      {hint && <p className="hint mt-1.5">{hint}</p>}
    </div>
  )
}

function Select({ value, onChange, options }: {
  value: string
  onChange: (v: string) => void
  options: [string, string][]
}) {
  return (
    <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  )
}

function NumberInput({ value, min, max, step, onChange, suffix }: {
  value: number; min: number; max: number; step: number
  onChange: (v: number) => void; suffix?: string
}) {
  return (
    <div className="flex items-center gap-2">
      <input type="number" className="field ltr-nums" value={value}
             min={min} max={max} step={step}
             onChange={(e) => {
               const v = parseFloat(e.target.value)
               if (!Number.isNaN(v)) onChange(Math.min(max, Math.max(min, v)))
             }} />
      {suffix && <span className="text-xs text-ink-500 shrink-0">{suffix}</span>}
    </div>
  )
}

function Slider({ label, value, min, max, step, display, hint, onChange }: {
  label: string; value: number; min: number; max: number; step: number
  display: string; hint?: string; onChange: (v: number) => void
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="label !mb-0">{label}</label>
        <span className="text-xs text-ink-100 ltr-nums">{display}</span>
      </div>
      <input type="range" className="range" value={value} min={min} max={max} step={step}
             onChange={(e) => onChange(parseFloat(e.target.value))} />
      {hint && <p className="hint mt-1.5">{hint}</p>}
    </div>
  )
}

function Toggle({ label, hint, checked, onChange, disabled }: {
  label: string; hint?: string; checked: boolean
  onChange: (v: boolean) => void; disabled?: boolean
}) {
  return (
    <label className={`flex items-start gap-3 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      <input type="checkbox" className="mt-0.5 accent-brand-500 w-4 h-4"
             checked={checked} disabled={disabled}
             onChange={(e) => onChange(e.target.checked)} />
      <div>
        <div className="text-sm text-ink-200">{label}</div>
        {hint && <p className="hint mt-0.5">{hint}</p>}
      </div>
    </label>
  )
}

function InfoRow({ label, value, tone }: {
  label: string; value: string; tone?: 'ok' | 'warn' | 'bad'
}) {
  const colors = { ok: 'text-ok', warn: 'text-warn', bad: 'text-bad' }
  return (
    <div className="flex items-start justify-between gap-4 text-xs py-1
                    border-b border-ink-800/60 last:border-0">
      <span className="text-ink-500 shrink-0">{label}</span>
      <span className={`ltr-nums text-end break-all ${tone ? colors[tone] : 'text-ink-300'}`}>
        {value}
      </span>
    </div>
  )
}

function Warning({ children, tone = 'warn' }: {
  children: React.ReactNode; tone?: 'warn' | 'info'
}) {
  const cls = tone === 'info'
    ? 'bg-brand-600/10 border-brand-500/25 text-ink-200'
    : 'bg-warn/10 border-warn/25 text-warn'
  return (
    <div className={`flex items-start gap-2 rounded-lg border p-3 ${cls}`}>
      <IconAlert className="w-4 h-4 shrink-0 mt-px" />
      <p className="text-xs leading-relaxed">{children}</p>
    </div>
  )
}

// --------------------------------------------------------------------------
function SecretField({ name, label, state, placeholder, onSaved }: {
  name: string
  label: string
  state?: { configured: boolean; masked: string }
  placeholder?: string
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const { pushToast, notifyError } = useStore()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true)
    try {
      await api.setSecret(name, value)
      setValue('')
      pushToast({ tone: 'success', title: t('settings.secret.savedEncrypted') })
      onSaved()
    } catch (e) { notifyError(e, t('settings.secret.saveFailed')) } finally { setBusy(false) }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.deleteSecret(name)
      pushToast({ tone: 'info', title: t('settings.secret.deleted') })
      onSaved()
    } catch (e) { notifyError(e) } finally { setBusy(false) }
  }

  return (
    <div>
      <label className="label">{label}</label>
      {state?.configured ? (
        <div className="flex items-center gap-2">
          <div className="field ltr-nums flex items-center gap-2 !py-2">
            <Chip tone="ok"><IconCheck className="w-3 h-3" />{t('settings.secret.configured')}</Chip>
            <span className="text-ink-400" dir="ltr">{state.masked}</span>
          </div>
          <button className="btn-ghost btn-sm !px-2" onClick={() => void remove()}
                  disabled={busy} aria-label={t('settings.secret.remove')} title={t('settings.secret.remove')}>
            <IconTrash className="w-3.5 h-3.5" />
          </button>
        </div>
      ) : (
        <div className="flex gap-2">
          <input type="password" className="field ltr-nums" dir="ltr" value={value}
                 placeholder={placeholder ?? '••••••••••••'} autoComplete="off"
                 onChange={(e) => setValue(e.target.value)} />
          <button className="btn-ghost btn-sm whitespace-nowrap"
                  onClick={() => void save()} disabled={!value.trim() || busy}>
            {busy ? <Spinner className="w-3.5 h-3.5" /> : null}{t('common.save')}
          </button>
        </div>
      )}
    </div>
  )
}

function AiTester() {
  const { t } = useTranslation()
  const { notifyError } = useStore()
  const [result, setResult] = useState<Record<string, any> | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    setResult(null)
    try {
      setResult(await api.testAi())
    } catch (e) { notifyError(e, t('settings.ai.testFailed')) } finally { setBusy(false) }
  }

  return (
    <div>
      <button className="btn-ghost btn-sm" onClick={() => void run()} disabled={busy}>
        {busy ? <Spinner className="w-3.5 h-3.5" /> : <IconRefresh className="w-3.5 h-3.5" />}
        {t('settings.ai.test')}
      </button>
      {result && (
        <div className={`mt-3 rounded-lg border p-3 text-xs leading-relaxed
          ${result.success === false
            ? 'bg-bad/10 border-bad/25 text-bad'
            : 'bg-ok/10 border-ok/25 text-ok'}`}>
          {result.message || result.note}
          {Array.isArray(result.models) && result.models.length > 0 && (
            <div className="mt-1 text-ink-400 ltr-nums" dir="ltr">
              {t('settings.ai.models', { models: result.models.join(', ') })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CleanupButton({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const { pushToast, notifyError } = useStore()
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      const r = await api.cleanup()
      pushToast({
        tone: 'success', title: t('settings.system.cleaned'),
        body: t('settings.system.cleanedBody', { jobs: r.jobs_cleaned, freed: iso(r.freed_human) }),
      })
      onDone()
    } catch (e) { notifyError(e, t('settings.system.cleanupFailed')) } finally { setBusy(false) }
  }

  return (
    <>
      <button className="btn-ghost btn-sm mt-3" onClick={() => void run()} disabled={busy}>
        {busy ? <Spinner className="w-3.5 h-3.5" /> : <IconTrash className="w-3.5 h-3.5" />}
        {t('settings.system.cleanup')}
      </button>
      <p className="hint mt-1.5">{t('settings.system.cleanupNote')}</p>
    </>
  )
}
