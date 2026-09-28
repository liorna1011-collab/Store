/**
 * הגדרות במאי ה-AI: סגנון קצב, פריסט כתוביות ומאסטרינג אודיו.
 *
 * עד עכשיו המנועים האלה היו נגישים רק דרך ה-API. כל מה שמופיע כאן
 * מחובר להגדרה אמיתית שמשפיעה על הייצוא — אין כאן פקד דקורטיבי.
 *
 * הטקסטים מתארים מה באמת קורה, כולל מה **לא** נעשה: כשהבמאי כבוי
 * חוזרים לעורך ההיוריסטי, וכשהמקור כבר עומד ביעד העוצמה לא מופעל
 * עליו שום עיבוד.
 */

import { useTranslation } from 'react-i18next'
import type { AppSettings } from '../lib/types'

type Setter = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => void

// --------------------------------------------------------------------------
// ערך ריק = „אוטומטי“; בקטלוג הוא נשמר תחת המפתח auto.
const PACING_STYLES = ['', 'viral_short', 'clean_creator', 'podcast_clip', 'educational',
  'product', 'cinematic_story']
const CAPTION_PRESETS = ['', 'clean', 'viral', 'cinematic', 'podcast', 'story']
const MASTERING_TARGETS = ['social', 'podcast', 'broadcast']

// --------------------------------------------------------------------------
export function DirectorSettings({ draft, set, Section, Toggle, Warning }: {
  draft: AppSettings
  set: Setter
  Section: React.ComponentType<{ title: string; children: React.ReactNode }>
  Toggle: React.ComponentType<{
    label: string; hint?: string; checked: boolean
    onChange: (v: boolean) => void; disabled?: boolean
  }>
  Warning: React.ComponentType<{
    children: React.ReactNode; tone?: 'warn' | 'info'
  }>
}) {
  const { t } = useTranslation()
  const k = (key: string) => `settings.director.${key}`
  return (
    <div className="space-y-5">
      <Section title={t(k('title'))}>
        <Toggle
          label={t(k('plan'))}
          hint={t(k('planHint'))}
          checked={draft.director_enabled}
          onChange={(v) => set('director_enabled', v)} />

        {!draft.director_enabled && (
          <Warning tone="info">{t(k('off'))}</Warning>
        )}

        {draft.director_enabled && (
          <>
            <Field label={t(k('pacing'))} hint={t(k('pacingHint'))}>
              <OptionList options={PACING_STYLES} group="pacingStyles" value={draft.director_style}
                          onChange={(v) => set('director_style', v)} />
            </Field>

            <Field label={t(k('captionPreset'))} hint={t(k('captionPresetHint'))}>
              <OptionList options={CAPTION_PRESETS} group="captionPresets" value={draft.caption_preset}
                          onChange={(v) => set('caption_preset', v)} />
            </Field>

            <Warning tone="info">{t(k('rawNote'))}</Warning>
          </>
        )}
      </Section>

      <Section title={t(k('mastering'))}>
        <Toggle
          label={t(k('master'))}
          hint={t(k('masterHint'))}
          checked={draft.mastering_enabled}
          onChange={(v) => set('mastering_enabled', v)} />

        {draft.mastering_enabled && (
          <>
            <Field label={t(k('target'))} hint={t(k('targetHint'))}>
              <OptionList options={MASTERING_TARGETS} group="targets"
                          value={draft.mastering_target}
                          onChange={(v) =>
                            set('mastering_target',
                                v as AppSettings['mastering_target'])} />
            </Field>

            <Toggle
              label={t(k('denoise'))}
              hint={t(k('denoiseHint'))}
              checked={draft.mastering_denoise}
              onChange={(v) => set('mastering_denoise', v)} />

            <Toggle
              label={t(k('compress'))}
              hint={t(k('compressHint'))}
              checked={draft.mastering_compress}
              onChange={(v) => set('mastering_compress', v)} />

            <Warning tone="info">{t(k('destructive'))}</Warning>
          </>
        )}
      </Section>
    </div>
  )
}

// --------------------------------------------------------------------------
/** תווית והסבר **מעל** הרשימה. הסבר אחרי שבע אפשרויות כבר לא נקרא. */
function Field({ label, hint, children }: {
  label: string; hint: string; children: React.ReactNode
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <p className="hint mb-2">{hint}</p>
      {children}
    </div>
  )
}

function OptionList({ options, group, value, onChange }: {
  options: string[]
  group: 'pacingStyles' | 'captionPresets' | 'targets'
  value: string
  onChange: (v: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2" role="radiogroup">
      {options.map((key) => (
        <label key={key || 'auto'}
               className={`flex items-start gap-2.5 rounded-lg border p-2.5
                 cursor-pointer transition-colors ${value === key
                   ? 'border-brand-500/50 bg-brand-600/10'
                   : 'border-ink-750 bg-ink-900 hover:border-ink-600'}`}>
          <input type="radio" className="mt-1 accent-brand-500"
                 checked={value === key}
                 onChange={() => onChange(key)} />
          <span className="min-w-0">
            <span className="block text-xs font-medium text-ink-100">
              {t(`settings.director.${group}.${key || 'auto'}.label`)}
            </span>
            <span className="block text-[11px] text-ink-500 leading-relaxed">
              {t(`settings.director.${group}.${key || 'auto'}.hint`)}
            </span>
          </span>
        </label>
      ))}
    </div>
  )
}
