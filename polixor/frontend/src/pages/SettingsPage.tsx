import { useCallback, useEffect, useState } from 'react'
import { PageHeader } from '../App'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { formatBytes } from '../lib/format'
import type {
  AppSettings, CaptionAnimation, EditStyleName, SettingsResponse, SystemInfo,
} from '../lib/types'
import { CaptionAnimationPicker, EditStylePicker } from '../components/editing'
import { DirectorSettings } from '../components/director-settings'
import {
  Chip, IconAlert, IconCheck, IconRefresh, IconTrash, Spinner,
} from '../components/ui'

type Tab = 'analysis' | 'editing' | 'director' | 'clips' | 'subtitles' | 'ai'
  | 'images' | 'live' | 'system'

type MusicProfile = AppSettings['music_profile']

const MUSIC_PROFILES: [MusicProfile, string][] = [
  ['minimal', 'מינימלי'],
  ['balanced', 'מאוזן'],
  ['energetic', 'אנרגטי'],
]

const TABS: [Tab, string][] = [
  ['analysis', 'ניתוח ותמלול'],
  ['editing', 'סגנון עריכה'],
  ['director', 'במאי AI ואודיו'],
  ['clips', 'קליפים וייצוא'],
  ['subtitles', 'כתוביות'],
  ['ai', 'מנוע AI'],
  ['images', 'AI Images'],
  ['live', 'שידור חי'],
  ['system', 'מערכת ואחסון'],
]

export default function SettingsPage() {
  const { pushToast, notifyError } = useStore()
  const [data, setData] = useState<SettingsResponse | null>(null)
  const [draft, setDraft] = useState<AppSettings | null>(null)
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [storage, setStorage] = useState<Record<string, any> | null>(null)
  const [benchmarks, setBenchmarks] = useState<Record<string, any> | null>(null)
  const [tab, setTab] = useState<Tab>('analysis')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, sys] = await Promise.all([api.getSettings(), api.system()])
      setData(s)
      setDraft(s.values)
      setSystem(sys)
      api.storage().then(setStorage).catch(() => undefined)
      api.benchmarks().then(setBenchmarks).catch(() => undefined)
    } catch (e) { notifyError(e, 'טעינת ההגדרות נכשלה') }
  }, [notifyError])

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
      pushToast({ tone: 'success', title: 'ההגדרות נשמרו' })
    } catch (e) { notifyError(e, 'שמירת ההגדרות נכשלה') } finally { setSaving(false) }
  }

  if (!draft || !data) {
    return <div className="p-4 sm:p-8 max-w-4xl mx-auto space-y-4">
      <div className="skeleton h-10 w-64" /><div className="skeleton h-96" />
    </div>
  }

  return (
    <div className="p-4 sm:p-8 max-w-4xl mx-auto pb-24">
      <PageHeader title="הגדרות"
                  subtitle="ההגדרות חלות על משימות חדשות. משימה שכבר רצה שומרת את ההגדרות שלה." />

      <div className="flex flex-wrap gap-x-1 gap-y-0 mb-5 border-b border-ink-800">
        {TABS.map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
                  className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap
                    transition-colors ${tab === key
                      ? 'border-brand-500 text-white'
                      : 'border-transparent text-ink-400 hover:text-ink-200'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'analysis' && (
        <div className="space-y-5">
          <Section title="זיהוי רגעים">
            <Slider label="רמת רגישות" value={draft.sensitivity} min={0} max={1} step={0.05}
                    display={`${Math.round(draft.sensitivity * 100)}%`}
                    hint="רגישות גבוהה מוצאת יותר רגעים, כולל בינוניים. נמוכה בוחרת רק את הבולטים."
                    onChange={(v) => set('sensitivity', v)} />
            <Slider label="דגימת פריימים לניתוח חזותי" value={draft.visual_sample_fps}
                    min={0.25} max={4} step={0.25} display={`${draft.visual_sample_fps}/שנ'`}
                    hint="יותר פריימים = ניתוח מדויק יותר וזמן עיבוד ארוך יותר."
                    onChange={(v) => set('visual_sample_fps', v)} />
            <Row label="הקשר לפני השיא" hint="שניות שנשמרות לפני הרגע עצמו">
              <NumberInput value={draft.context_pad_before} min={0} max={15} step={0.5}
                           onChange={(v) => set('context_pad_before', v)} suffix="שנ'" />
            </Row>
            <Row label="הקשר אחרי השיא">
              <NumberInput value={draft.context_pad_after} min={0} max={15} step={0.5}
                           onChange={(v) => set('context_pad_after', v)} suffix="שנ'" />
            </Row>
            <Toggle label="השתמש באות מצ'אט הלייב"
                    hint="נדרשת גישה מורשית לנתוני הצ'אט. כשאין נתונים, האות פשוט לא משפיע."
                    checked={draft.use_chat_signal}
                    onChange={(v) => set('use_chat_signal', v)} />
          </Section>

          <Section title="תמלול">
            <Row label="מנוע תמלול">
              <Select value={draft.transcript_provider}
                      onChange={(v) => set('transcript_provider', v)}
                      options={[
                        ['faster-whisper', 'faster-whisper (מקומי)'],
                        ['none', 'ללא תמלול'],
                      ]} />
            </Row>
            {draft.transcript_provider === 'faster-whisper' && (
              <>
                <Row label="מודל" hint="מודל גדול = דיוק גבוה יותר, זיכרון וזמן רב יותר">
                  <Select value={draft.whisper_model}
                          onChange={(v) => set('whisper_model', v)}
                          options={[
                            ['tiny', 'tiny — הכי מהיר (~75MB)'],
                            ['base', 'base (~145MB)'],
                            ['small', 'small — מאוזן (~480MB)'],
                            ['medium', 'medium (~1.5GB)'],
                            ['large-v3', 'large-v3 — הכי מדויק (~3GB)'],
                          ]} />
                </Row>
                <Row label="מכשיר">
                  <Select value={draft.whisper_device}
                          onChange={(v) => set('whisper_device', v)}
                          options={[['auto', 'אוטומטי'], ['cpu', 'מעבד'], ['cuda', 'GPU (CUDA)']]} />
                </Row>
                <Row label="שפת הדיבור">
                  <Select value={draft.transcribe_language}
                          onChange={(v) => set('transcribe_language', v)}
                          options={[['auto', 'זיהוי אוטומטי'], ['he', 'עברית'], ['en', 'אנגלית']]} />
                </Row>
                {system && !system.modules.faster_whisper.available && (
                  <Warning>
                    faster-whisper אינו מותקן. התמלול לא יפעל, והמשימות ימשיכו
                    עם אותות אודיו ווידאו בלבד וללא כתוביות.
                  </Warning>
                )}
                <p className="hint">
                  בפעם הראשונה המודל יורד מהאינטרנט ונשמר מקומית. לאחר מכן
                  אפשר לעבוד גם ללא חיבור.
                </p>
              </>
            )}
            {draft.transcript_provider === 'none' && (
              <Warning tone="info">
                ללא תמלול לא ייווצרו כתוביות, והרגעים ייבחרו לפי אותות אודיו
                ווידאו בלבד.
              </Warning>
            )}
          </Section>
        </div>
      )}

      {tab === 'editing' && (
        <div className="space-y-5">
          <Section title="איך הקליפים נערכים">
            <p className="hint">
              מנוע העריכה מחליט איפה לחתוך אוויר מת, איפה לשנות את גודל
              הפריים ומתי להאיץ. זה ההבדל בין קטע שנחתך מהשידור לבין קטע
              שמרגיש ערוך. הסגנון נקבע בנפרד לקליפים ארוכים ולשורטים.
            </p>
          </Section>

          <Section title="שורטים">
            <EditStylePicker value={draft.edit_style_short}
                             onChange={(v: EditStyleName) => set('edit_style_short', v)} />
          </Section>

          <Section title="קליפים ארוכים">
            <EditStylePicker value={draft.edit_style_long}
                             onChange={(v: EditStyleName) => set('edit_style_long', v)} />
          </Section>

          <Section title="כוונון עדין">
            <Toggle label="הסרת אוויר מת"
                    hint="כיבוי משאיר את כל השתיקות במקומן, בכל סגנון."
                    checked={draft.remove_silence}
                    onChange={(v) => set('remove_silence', v)} />
            <Toggle label="שינויי זווית בחיתוכים"
                    hint="שינוי קל בגודל הפריים בכל חיתוך. בלי זה החיתוכים נראים כקפיצות."
                    checked={draft.angle_changes}
                    onChange={(v) => set('angle_changes', v)} />
            <Row label="אורך שתיקה מינימלי לחיתוך"
                 hint="0 = לפי הסגנון. שקט קצר מהערך הזה לא נגזר.">
              <NumberInput value={draft.silence_min_gap} min={0} max={2} step={0.05}
                           onChange={(v) => set('silence_min_gap', v)} suffix="שנ'" />
            </Row>
            <Row label="תקרת הסרה"
                 hint="0 = לפי הסגנון. כמה מאורך הקליפ מותר להסיר לכל היותר.">
              <NumberInput value={draft.max_removed_ratio} min={0} max={0.7} step={0.05}
                           onChange={(v) => set('max_removed_ratio', v)} />
            </Row>
            <Warning tone="info">
              העורך אף פעם לא חותך שתיקה שזוהתה כדרמטית — שקט שאחריו מגיע
              דיבור משמעותי הוא חלק מהרגע, והוא מקוצר במקום להימחק.
            </Warning>
          </Section>

          <Section title="מוזיקת רקע">
            <Toggle label="הוסף מוזיקת רקע" checked={draft.music_enabled}
                    onChange={(v) => set('music_enabled', v)} />
            {draft.music_enabled && (
              <>
                <Row label="קובץ מוזיקה"
                     hint="נתיב מלא לקובץ במחשב שמריץ את Polixor. התוכנה
                           אינה מספקת מוזיקה — הבא קובץ שיש לך זכות
                           להשתמש בו.">
                  <input className="field ltr-nums" dir="ltr"
                         value={draft.music_path}
                         placeholder="C:\\Music\\track.mp3"
                         onChange={(e) => set('music_path', e.target.value)} />
                </Row>
                <Row label="נוכחות"
                     hint="העוצמה נמדדת מול קובץ המוזיקה עצמו, כך שאותה
                           בחירה נשמעת אותו דבר בכל קובץ.">
                  <div className="grid grid-cols-3 gap-2">
                    {MUSIC_PROFILES.map(([key, label]) => (
                      <button key={key}
                              onClick={() => set('music_profile', key)}
                              className={`btn btn-sm ${draft.music_profile === key
                                ? 'bg-brand-600/20 text-brand-300 ring-1 ring-brand-500/40'
                                : 'bg-ink-800 text-ink-400 border border-ink-700 hover:text-white'}`}>
                        {label}
                      </button>
                    ))}
                  </div>
                </Row>
                <Warning tone="info">
                  המוזיקה יורדת אוטומטית מתחת לדיבור, ועולה בשיא הרגשי.
                  היא נכנסת אחרי עיבוד הקול, כך שהקול עצמו מגיע ליעד
                  העוצמה שלו בלי קשר למוזיקה.
                </Warning>
              </>
            )}
          </Section>

          <Section title="אנימציית כתוביות">
            <CaptionAnimationPicker value={draft.subtitle_animation}
                                    onChange={(v: CaptionAnimation) =>
                                      set('subtitle_animation', v)}
                                    disabled={!draft.subtitles_enabled} />
            {!draft.subtitles_enabled && (
              <p className="hint">הכתוביות מושבתות בלשונית "כתוביות".</p>
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
          <Section title="קליפים ארוכים">
            <Toggle label="ייצר קליפים ארוכים" checked={draft.long_enabled}
                    onChange={(v) => set('long_enabled', v)} />
            {draft.long_enabled && (
              <>
                <Row label="סוג">
                  <Select value={draft.long_mode} onChange={(v) => set('long_mode', v as any)}
                          options={[
                            ['continuous', 'קטע רציף מהשידור'],
                            ['highlights', 'מקבץ מיטב הרגעים'],
                          ]} />
                </Row>
                <Row label="כמות">
                  <NumberInput value={draft.long_count} min={0} max={20} step={1}
                               onChange={(v) => set('long_count', Math.round(v))} />
                </Row>
                <Row label="אורך מינימלי">
                  <NumberInput value={draft.long_min_seconds / 60} min={0.5} max={60} step={0.5}
                               onChange={(v) => set('long_min_seconds', Math.round(v * 60))}
                               suffix="דק'" />
                </Row>
                <Row label="אורך מקסימלי">
                  <NumberInput value={draft.long_max_seconds / 60} min={1} max={90} step={0.5}
                               onChange={(v) => set('long_max_seconds', Math.round(v * 60))}
                               suffix="דק'" />
                </Row>
                <Row label="רזולוציה">
                  <Select value={draft.long_resolution}
                          onChange={(v) => set('long_resolution', v)}
                          options={[['1920x1080', '1080p'], ['1280x720', '720p'],
                                    ['2560x1440', '1440p']]} />
                </Row>
              </>
            )}
          </Section>

          <Section title="שורטים ואנכיים">
            <Toggle label="ייצר שורטים" checked={draft.short_enabled}
                    onChange={(v) => set('short_enabled', v)} />
            {draft.short_enabled && (
              <>
                <Row label="כמות">
                  <NumberInput value={draft.short_count} min={0} max={30} step={1}
                               onChange={(v) => set('short_count', Math.round(v))} />
                </Row>
                <Row label="אורך מינימלי">
                  <NumberInput value={draft.short_min_seconds} min={3} max={180} step={1}
                               onChange={(v) => set('short_min_seconds', Math.round(v))}
                               suffix="שנ'" />
                </Row>
                <Row label="אורך מקסימלי">
                  <NumberInput value={draft.short_max_seconds} min={5} max={300} step={1}
                               onChange={(v) => set('short_max_seconds', Math.round(v))}
                               suffix="שנ'" />
                </Row>
                <Row label="פריסה אנכית"
                     hint={draft.short_layout === 'blur_pad'
                       ? 'שום דבר לא נחתך מהפריים והתמונה נשארת חדה, כי אין הגדלה של האזור המרכזי.'
                       : 'מעקב פנים מסתמך על זיהוי פנים חזיתיות; אם לא זוהו — חיתוך מרכזי.'}>
                  <Select value={draft.short_layout}
                          onChange={(v) => set('short_layout', v as any)}
                          options={[
                            ['center', 'חיתוך מרכזי'],
                            ['auto_face', 'מעקב אחרי פנים'],
                            ['split', 'מסך מפוצל (מצלמה + גיימפליי)'],
                            ['blur_pad', 'מסגרת מלאה על רקע מטושטש'],
                          ]} />
                </Row>
                <Row label="רזולוציה">
                  <Select value={draft.short_resolution}
                          onChange={(v) => set('short_resolution', v)}
                          options={[['1080x1920', '1080×1920'], ['720x1280', '720×1280']]} />
                </Row>
              </>
            )}
          </Section>

          <Section title="ייצוא">
            <Row label="מספר קליפים מרבי למשימה">
              <NumberInput value={draft.max_clips_total} min={1} max={100} step={1}
                           onChange={(v) => set('max_clips_total', Math.round(v))} />
            </Row>
            <Row label="איכות">
              <Select value={draft.video_quality} onChange={(v) => set('video_quality', v as any)}
                      options={[['high', 'גבוהה (CRF 18)'], ['medium', 'בינונית (CRF 21)'],
                                ['low', 'נמוכה ומהירה (CRF 25)']]} />
            </Row>
            <Row label="האצת חומרה"
                 hint={system?.gpu.nvenc ? 'זוהה NVENC במערכת.'
                   : 'לא זוהה מקודד חומרה; יעשה שימוש במעבד.'}>
              <Select value={draft.hw_accel} onChange={(v) => set('hw_accel', v)}
                      options={[
                        ['none', 'ללא (מעבד)'],
                        ['nvenc', 'NVIDIA NVENC'],
                        ['qsv', 'Intel QuickSync'],
                        ['videotoolbox', 'Apple VideoToolbox'],
                      ]} />
            </Row>
            <Toggle label="איזון עוצמות אודיו"
                    hint="מאזן הפרשי עוצמה בין דיבור שקט לצעקות. כבה כדי לשמור על האודיו המקורי."
                    checked={draft.audio_normalize}
                    onChange={(v) => set('audio_normalize', v)} />
            <Row label="תיקיית ייצוא"
                 hint="השאר ריק כדי להשתמש בתיקיית ברירת המחדל של האפליקציה.">
              <input className="field ltr-nums" dir="ltr" value={draft.export_dir}
                     placeholder={system?.data_dir ? `${system.data_dir}\\exports` : ''}
                     onChange={(e) => set('export_dir', e.target.value)} />
            </Row>
            <Row label="משימות במקביל"
                 hint="יותר ממשימה אחת דורש הרבה מעבד ודיסק.">
              <NumberInput value={draft.concurrent_jobs} min={1} max={4} step={1}
                           onChange={(v) => set('concurrent_jobs', Math.round(v))} />
            </Row>
          </Section>
        </div>
      )}

      {tab === 'subtitles' && (
        <div className="space-y-5">
          <Section title="כתוביות">
            <Toggle label="צרוב כתוביות בקליפים" checked={draft.subtitles_enabled}
                    onChange={(v) => set('subtitles_enabled', v)} />
            {draft.subtitles_enabled && (
              <>
                <Toggle label="תזמון והדגשה ברמת מילה"
                        hint="זמין כשהתמלול מחזיר תזמון מילים. אחרת הכתובית מוצגת ברמת משפט."
                        checked={draft.subtitle_word_level}
                        onChange={(v) => set('subtitle_word_level', v)} />
                <Row label="גופן"
                     hint="בעברית יש לבחור גופן שתומך בעברית. אם הגופן אינו קיים במערכת,
                           Polixor יבחר חלופה מתאימה אוטומטית.">
                  <input className="field" value={draft.subtitle_font}
                         onChange={(e) => set('subtitle_font', e.target.value)} />
                </Row>
                <Slider label="גודל" value={draft.subtitle_size} min={16} max={120} step={2}
                        display={String(draft.subtitle_size)}
                        onChange={(v) => set('subtitle_size', Math.round(v))} />
                <div className="grid grid-cols-2 gap-4">
                  <Row label="צבע טקסט">
                    <input type="color" value={draft.subtitle_color}
                           className="w-full h-9 rounded-lg bg-ink-800 border border-ink-700"
                           onChange={(e) => set('subtitle_color', e.target.value)} />
                  </Row>
                  <Row label="צבע מתאר">
                    <input type="color" value={draft.subtitle_outline_color}
                           className="w-full h-9 rounded-lg bg-ink-800 border border-ink-700"
                           onChange={(e) => set('subtitle_outline_color', e.target.value)} />
                  </Row>
                </div>
                <Row label="מיקום">
                  <Select value={draft.subtitle_position}
                          onChange={(v) => set('subtitle_position', v as any)}
                          options={[['bottom', 'למטה'], ['middle', 'באמצע'], ['top', 'למעלה']]} />
                </Row>
                <Toggle label="כרטיס כותרת בתחילת הסרטון"
                        hint="מציג את כותרת הקליפ בשניות הראשונות."
                        checked={draft.title_card_enabled}
                        onChange={(v) => set('title_card_enabled', v)} />
                <p className="hint">
                  הכתוביות נגזרות מהתמלול בלבד. לפני הייצוא אפשר לתקן כל שורה
                  במסך עריכת הקליפ.
                </p>
              </>
            )}
          </Section>
        </div>
      )}

      {tab === 'ai' && (
        <div className="space-y-5">
          <Section title="מנוע ניתוח התוכן">
            <Row label="מצב">
              <Select value={draft.ai_mode} onChange={(v) => set('ai_mode', v as any)}
                      options={[
                        ['heuristic', 'מקומי היוריסטי — ללא מודל שפה'],
                        ['ollama', 'Ollama מקומי — ללא עלות'],
                        ['cloud', 'מודל בענן — דורש מפתח API'],
                      ]} />
            </Row>

            {draft.ai_mode === 'heuristic' && (
              <Warning tone="info">
                מצב זה עובד תמיד, ללא רשת וללא עלות. הכותרות והתיאורים נגזרים
                מהתמלול ומאותות האודיו/וידאו, ולכן הניסוח פחות מלוטש ממודל שפה,
                וההבנה של הקשר סיפורי מוגבלת.
              </Warning>
            )}

            {draft.ai_mode === 'ollama' && (
              <>
                <Row label="שם המודל" hint="לדוגמה: llama3.1, qwen2.5, mistral">
                  <input className="field ltr-nums" dir="ltr" value={draft.ai_model}
                         onChange={(e) => set('ai_model', e.target.value)} />
                </Row>
                <Warning tone="info">
                  דורש שרת Ollama פועל בכתובת http://localhost:11434 ומודל שהורד
                  מראש (ollama pull). לא נשלח מידע לאינטרנט. איכות התוצאה תלויה
                  בגודל המודל, ומודל גדול דורש זיכרון ו-GPU כדי לרוץ בזמן סביר.
                </Warning>
              </>
            )}

            {draft.ai_mode === 'cloud' && (
              <>
                <Row label="ספק">
                  <Select value={draft.ai_provider}
                          onChange={(v) => set('ai_provider', v as any)}
                          options={[['anthropic', 'Anthropic'], ['openai', 'OpenAI']]} />
                </Row>
                <Row label="מודל">
                  <input className="field ltr-nums" dir="ltr" value={draft.ai_model}
                         onChange={(e) => set('ai_model', e.target.value)} />
                </Row>
                <SecretField
                  name={draft.ai_provider === 'openai' ? 'openai_api_key' : 'anthropic_api_key'}
                  label={`מפתח API של ${draft.ai_provider === 'openai' ? 'OpenAI' : 'Anthropic'}`}
                  state={data.secrets[draft.ai_provider === 'openai'
                    ? 'openai_api_key' : 'anthropic_api_key']}
                  onSaved={() => void load()}
                />
                <Warning>
                  במצב ענן נשלחים קטעי תמלול לשירות חיצוני. המפתח נשמר מוצפן
                  במחשב שלך ואינו נחשף בדפדפן.
                </Warning>
              </>
            )}

            <Toggle label="תן למודל להציע רגעים שקטים"
                    hint="המודל קורא את התמלול ומחפש רגעים מעניינים שאינם קולניים, שאותות האודיו מפספסים."
                    checked={draft.ai_discover_moments}
                    onChange={(v) => set('ai_discover_moments', v)}
                    disabled={draft.ai_mode === 'heuristic'} />

            <AiTester />
          </Section>

          <Section title="גישה למקורות מוגבלים">
            <SecretField
              name="cookiefile_path"
              label="נתיב לקובץ cookies.txt"
              state={data.secrets.cookiefile_path}
              placeholder="C:\Users\...\cookies.txt"
              onSaved={() => void load()}
            />
            <p className="hint">
              קובץ עוגיות שייצאת בעצמך מהדפדפן מאפשר הורדה של תוכן שהחשבון שלך
              מורשה לצפות בו. Polixor אינו ניגש לדפדפן שלך ואינו עוקף DRM,
              הגבלות גיל, אזור או תוכן בתשלום.
            </p>
          </Section>
        </div>
      )}

      {tab === 'images' && (
        <div className="space-y-5">
          <Section title="יצירת תמונות">
            <Row label="ספק">
              <Select value={draft.image_provider}
                      onChange={(v) => set('image_provider', v as any)}
                      options={[
                        ['openai', 'OpenAI Images'],
                        ['placeholder', 'כרטיס מקומי (לא AI)'],
                      ]} />
            </Row>

            {draft.image_provider === 'openai' && (
              <>
                <Row label="מודל">
                  <Select value={draft.image_model}
                          onChange={(v) => set('image_model', v as any)}
                          options={[
                            ['gpt-image-1', 'gpt-image-1'],
                            ['dall-e-3', 'dall-e-3'],
                            ['dall-e-2', 'dall-e-2'],
                          ]} />
                </Row>
                <Row label="איכות">
                  <Select value={draft.image_quality}
                          onChange={(v) => set('image_quality', v as any)}
                          options={[['low', 'נמוכה'], ['medium', 'בינונית'],
                                    ['high', 'גבוהה']]} />
                </Row>
                <SecretField
                  name="openai_api_key"
                  label="מפתח API של OpenAI"
                  state={data.secrets.openai_api_key}
                  onSaved={() => void load()}
                />
                <Warning>
                  הפרומפט נשלח ל-OpenAI מהשרת בלבד. המפתח נשמר מוצפן במחשב שלך,
                  אינו נכלל בקוד הדפדפן ואינו מוחזר ל-API בשום צורה מלבד מסכה.
                </Warning>
              </>
            )}

            {draft.image_provider === 'placeholder' && (
              <Warning>
                ספק זה יוצר כרטיס גרפי במחשב שלך. <b>אלה אינן תמונות AI</b>, והן
                מסומנות ככאלה בכל מקום בממשק. הוא קיים כדי לאפשר בדיקה של שרשרת
                ההכנסה לווידאו ללא מפתח API.
              </Warning>
            )}

            <Row label="פסק זמן לבקשה (שניות)">
              <input type="number" min={15} max={600} className="field w-28 ltr-nums"
                     value={draft.image_timeout_seconds}
                     onChange={(e) => set('image_timeout_seconds',
                                          Number(e.target.value) as any)} />
            </Row>
            <Row label="ניסיונות חוזרים">
              <input type="number" min={0} max={5} className="field w-28 ltr-nums"
                     value={draft.image_retries}
                     onChange={(e) => set('image_retries', Number(e.target.value) as any)} />
            </Row>
            <p className="hint">
              ניסיון חוזר מתבצע רק על תקלה זמנית — חריגת מכסה, פסק זמן או שירות
              שאינו זמין. פרומפט שנדחה או מפתח חסר לא ינוסו שוב.
            </p>
            <p className="hint">
              כל קריאה לשירות יצירת התמונות יוצאת מהשרת המקומי בלבד. מפתח ה-API
              נשמר מוצפן על הדיסק שלך, אינו נחשף בדפדפן, ואינו נכלל בקוד הממשק.
            </p>
          </Section>
        </div>
      )}

      {tab === 'live' && (
        <div className="space-y-5">
          <Section title="קליטת שידור חי">
            <Row label="אורך מקטע הקלטה (שניות)">
              <input type="number" min={30} max={1800} step={30}
                     className="field w-28 ltr-nums"
                     value={draft.live_segment_seconds}
                     onChange={(e) => set('live_segment_seconds',
                                          Number(e.target.value) as any)} />
            </Row>
            <p className="hint">
              ההקלטה נשמרת במקטעים. מקטע קצר יותר מקטין את כמות החומר שבסיכון
              אם התהליך נופל באמצע, אבל מוסיף תפרים.
            </p>

            <Row label="הגבלת זמן הקלטה (דקות)">
              <input type="number" min={0} max={1440}
                     className="field w-28 ltr-nums"
                     value={draft.live_max_minutes}
                     onChange={(e) => set('live_max_minutes',
                                          Number(e.target.value) as any)} />
            </Row>
            <p className="hint">
              0 = ההקלטה נמשכת עד שתעצור אותה ידנית או עד שהשידור יסתיים.
            </p>

            <Toggle label="שמור את מקטעי ההקלטה אחרי האיחוד"
                    hint="שימושי לשחזור אם האיחוד נכשל. תופס מקום נוסף בדיסק."
                    checked={draft.live_keep_segments}
                    onChange={(v) => set('live_keep_segments', v)} />
          </Section>
        </div>
      )}

      {tab === 'system' && (
        <div className="space-y-5">
          <Section title="סביבה">
            {system ? (
              <div className="space-y-2">
                <InfoRow label="גרסה" value={`${system.app.name} ${system.app.version}`} />
                <InfoRow label="מערכת" value={system.platform} />
                <InfoRow label="Python" value={system.python} />
                <InfoRow label="מעבדים" value={String(system.cpu_count)} />
                <InfoRow label="FFmpeg"
                         value={system.ffmpeg.available ? system.ffmpeg.path : 'לא נמצא'}
                         tone={system.ffmpeg.available ? 'ok' : 'bad'} />
                {Object.entries(system.modules).map(([name, m]) => (
                  <InfoRow key={name} label={name}
                           value={m.available ? (m.version || 'מותקן') : 'לא מותקן'}
                           tone={m.available ? 'ok' : 'warn'} />
                ))}
                <InfoRow label="GPU"
                         value={system.gpu.cuda ? system.gpu.name : 'לא זוהה'}
                         tone={system.gpu.cuda ? 'ok' : undefined} />
                <InfoRow label="NVENC" value={system.gpu.nvenc ? 'זמין' : 'לא זמין'}
                         tone={system.gpu.nvenc ? 'ok' : undefined} />
                <InfoRow label="תיקיית נתונים" value={system.data_dir} />
                <InfoRow label="מקום פנוי" value={formatBytes(system.free_disk_bytes)} />
              </div>
            ) : <Spinner />}

            {system?.warnings.map((w, i) => <Warning key={i}>{w}</Warning>)}
          </Section>

          {storage && (
            <Section title="שימוש בדיסק">
              <div className="space-y-2">
                <InfoRow label="קובצי מקור" value={storage.sources_human} />
                <InfoRow label="קובצי עבודה זמניים" value={storage.work_human} />
                <InfoRow label="קליפים מיוצאים" value={storage.exports_human} />
                <InfoRow label="משימות" value={String(storage.job_count)} />
                <InfoRow label="קליפים" value={String(storage.clip_count)} />
              </div>
              <CleanupButton onDone={() => api.storage().then(setStorage)} />
            </Section>
          )}

          <Section title="מדידות זמן">
            {benchmarks?.has_data ? (
              <>
                <p className="hint mb-3">
                  היחס הוא שניות עיבוד לכל שנייה של שידור. ההערכות בממשק מבוססות
                  אך ורק על המדידות האלה.
                </p>
                <div className="space-y-2">
                  {(benchmarks.stages as any[])
                    .filter((s) => s.ratio !== null)
                    .map((s) => (
                      <InfoRow key={s.stage} label={s.stage}
                               value={`×${Number(s.ratio).toFixed(3)} (${s.samples} מדידות)`} />
                    ))}
                </div>
              </>
            ) : (
              <p className="hint">
                אין עדיין מספיק מדידות. עד שיצטברו, Polixor לא יציג הערכת זמן
                סיום — במקום לנחש.
              </p>
            )}
          </Section>
        </div>
      )}

      {/* ---- שמירה ---- */}
      {dirty && (
        <div className="fixed bottom-0 left-0 right-64 bg-ink-900/95 backdrop-blur
                        border-t border-ink-750 p-4 z-30">
          <div className="max-w-4xl mx-auto flex items-center justify-between gap-4">
            <span className="text-sm text-ink-300">יש שינויים שלא נשמרו</span>
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={() => setDraft(data.values)}>
                בטל שינויים
              </button>
              <button className="btn-primary" onClick={() => void save()} disabled={saving}>
                {saving ? <Spinner className="w-4 h-4" /> : <IconCheck className="w-4 h-4" />}
                שמור הגדרות
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
      <h2 className="text-sm font-semibold text-white mb-4">{title}</h2>
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
        <span className="text-xs text-white ltr-nums">{display}</span>
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
      <span className={`ltr-nums text-left break-all ${tone ? colors[tone] : 'text-ink-300'}`}>
        {value}
      </span>
    </div>
  )
}

function Warning({ children, tone = 'warn' }: {
  children: React.ReactNode; tone?: 'warn' | 'info'
}) {
  const cls = tone === 'info'
    ? 'bg-brand-600/10 border-brand-500/25 text-brand-200'
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
  const { pushToast, notifyError } = useStore()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true)
    try {
      await api.setSecret(name, value)
      setValue('')
      pushToast({ tone: 'success', title: 'נשמר בהצפנה במחשב' })
      onSaved()
    } catch (e) { notifyError(e, 'השמירה נכשלה') } finally { setBusy(false) }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.deleteSecret(name)
      pushToast({ tone: 'info', title: 'נמחק' })
      onSaved()
    } catch (e) { notifyError(e) } finally { setBusy(false) }
  }

  return (
    <div>
      <label className="label">{label}</label>
      {state?.configured ? (
        <div className="flex items-center gap-2">
          <div className="field ltr-nums flex items-center gap-2 !py-2">
            <Chip tone="ok"><IconCheck className="w-3 h-3" />מוגדר</Chip>
            <span className="text-ink-400" dir="ltr">{state.masked}</span>
          </div>
          <button className="btn-ghost btn-sm !px-2" onClick={() => void remove()}
                  disabled={busy} aria-label="מחק">
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
            {busy ? <Spinner className="w-3.5 h-3.5" /> : null}שמור
          </button>
        </div>
      )}
    </div>
  )
}

function AiTester() {
  const { notifyError } = useStore()
  const [result, setResult] = useState<Record<string, any> | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    setResult(null)
    try {
      setResult(await api.testAi())
    } catch (e) { notifyError(e, 'בדיקת החיבור נכשלה') } finally { setBusy(false) }
  }

  return (
    <div>
      <button className="btn-ghost btn-sm" onClick={() => void run()} disabled={busy}>
        {busy ? <Spinner className="w-3.5 h-3.5" /> : <IconRefresh className="w-3.5 h-3.5" />}
        בדוק חיבור AI
      </button>
      {result && (
        <div className={`mt-3 rounded-lg border p-3 text-xs leading-relaxed
          ${result.success === false
            ? 'bg-bad/10 border-bad/25 text-bad'
            : 'bg-ok/10 border-ok/25 text-ok'}`}>
          {result.message || result.note}
          {Array.isArray(result.models) && result.models.length > 0 && (
            <div className="mt-1 text-ink-400 ltr-nums" dir="ltr">
              מודלים זמינים: {result.models.join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CleanupButton({ onDone }: { onDone: () => void }) {
  const { pushToast, notifyError } = useStore()
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      const r = await api.cleanup()
      pushToast({
        tone: 'success', title: 'קובצי עבודה נוקו',
        body: `${r.jobs_cleaned} משימות · שוחררו ${r.freed_human}`,
      })
      onDone()
    } catch (e) { notifyError(e, 'הניקוי נכשל') } finally { setBusy(false) }
  }

  return (
    <>
      <button className="btn-ghost btn-sm mt-3" onClick={() => void run()} disabled={busy}>
        {busy ? <Spinner className="w-3.5 h-3.5" /> : <IconTrash className="w-3.5 h-3.5" />}
        נקה קובצי עבודה זמניים
      </button>
      <p className="hint mt-1.5">
        מוחק אודיו מחולץ, תמלול ונתוני ניתוח של משימות שהסתיימו.
        קובצי המקור והקליפים נשמרים.
      </p>
    </>
  )
}
