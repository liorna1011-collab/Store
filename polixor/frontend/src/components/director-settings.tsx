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

import type { AppSettings } from '../lib/types'

type Setter = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => void

// --------------------------------------------------------------------------
const PACING_STYLES: [string, string, string][] = [
  ['', 'לפי סגנון העריכה', 'נגזר מהסגנון שנבחר למעלה. ברירת המחדל.'],
  ['viral_short', 'ויראלי קצר', 'שינוי ויזואלי כל 1.5–3 שניות.'],
  ['clean_creator', 'יוצר נקי', 'שינוי כל 3–6 שניות. מקצועי ומאופק.'],
  ['podcast_clip', 'קטע מפודקאסט', 'שינוי כל 3.5–7 שניות, עם כתוביות.'],
  ['educational', 'הסברתי', 'שינוי כל 3–5.5 שניות, יותר המחשות.'],
  ['product', 'מוצר', 'שינוי כל 2.5–4.5 שניות, פוקוס על המוצר.'],
  ['cinematic_story', 'סיפור קולנועי', 'שינוי כל 5–9 שניות. איטי ורגשי.'],
]

const CAPTION_PRESETS: [string, string, string][] = [
  ['', 'לפי סגנון הקצב', 'נגזר מהסגנון שהבמאי בחר. ברירת המחדל.'],
  ['clean', 'נקי', 'שתי שורות שקטות בתחתית, בלי אנימציה.'],
  ['viral', 'ויראלי', 'שורה אחת, עד שלוש מילים, הדגשה רצה.'],
  ['cinematic', 'קולנועי', 'כתובית קטנה ומאופקת. התמונה היא העיקר.'],
  ['podcast', 'פודקאסט', 'שבירה בכל מעבר דובר, צבע לכל דובר.'],
  ['story', 'סיפור', 'כתובית במרכז הפריים, קצרה.'],
]

const MASTERING_TARGETS: [string, string, string][] = [
  ['social', 'רשתות חברתיות', '‎-14 LUFS. הפלטפורמות מנרמלות לשם ממילא.'],
  ['podcast', 'פודקאסט', '‎-16 LUFS. התקן המקובל להאזנה ארוכה.'],
  ['broadcast', 'שידור', '‎-23 LUFS, לפי EBU R128.'],
]

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
  return (
    <div className="space-y-5">
      <Section title="במאי ה-AI">
        <Toggle
          label="בנה תכנית עריכה לפני הביצוע"
          hint="הבמאי מנתח את המבנה — וו, הקשר, שיא רגשי, קריאה לפעולה —
                ורק אז מחליט מה לחתוך, איפה לשנות מסגור ואילו מילים
                להדגיש. כל החלטה נשמרת עם הנימוק שלה."
          checked={draft.director_enabled}
          onChange={(v) => set('director_enabled', v)} />

        {!draft.director_enabled && (
          <Warning tone="info">
            כשהבמאי כבוי, העריכה נעשית על-ידי העורך ההיוריסטי הקודם:
            הסרת אוויר מת והידוק, בלי הבנה של מבנה הסרטון. זו התנהגות
            תקינה — רק פחות חכמה.
          </Warning>
        )}

        {draft.director_enabled && (
          <>
            <Field label="סגנון קצב"
                   hint="קובע כל כמה זמן משהו משתנה. בתוך כל סגנון,
                         התפקיד מכוונן: שיא רגשי מקבל יותר אוויר,
                         פתיחה פחות.">
              <OptionList options={PACING_STYLES} value={draft.director_style}
                          onChange={(v) => set('director_style', v)} />
            </Field>

            <Field label="פריסט כתוביות"
                   hint="כל הפריסטים רצים על אותו מנוע — משתנים
                         הפרמטרים, לא המנגנון.">
              <OptionList options={CAPTION_PRESETS} value={draft.caption_preset}
                          onChange={(v) => set('caption_preset', v)} />
            </Field>

            <Warning tone="info">
              סגנון עריכה „גולמי” מבטל את הבמאי לגמרי — זו בקשה מפורשת
              לחיתוך ישיר בלי עריכה, והיא גוברת על ההגדרות כאן.
            </Warning>
          </>
        )}
      </Section>

      <Section title="מאסטרינג אודיו">
        <Toggle
          label="עבד את האודיו לפי מדידה"
          hint="מודד את הקליפ אחרי הרינדור, מחליט מה נדרש, מבצע, ומודד
                שוב. מקור שכבר עומד ביעד יוצא בלי שום עיבוד."
          checked={draft.mastering_enabled}
          onChange={(v) => set('mastering_enabled', v)} />

        {draft.mastering_enabled && (
          <>
            <Field label="יעד עוצמה"
                   hint="העוצמה המשולבת שאליה הקליפ מגיע. אם התוצאה לא
                         עומדת ביעד, הקליפ מסומן „דורש בדיקה” והאודיו
                         המקורי נשמר.">
              <OptionList options={MASTERING_TARGETS}
                          value={draft.mastering_target}
                          onChange={(v) =>
                            set('mastering_target',
                                v as AppSettings['mastering_target'])} />
            </Field>

            <Toggle
              label="הפחתת רעש"
              hint="מופעלת רק כשנמדד רעש רקע מורגש בהפסקות. בלי הפסקות
                    אין מדידה, ולכן לא מופעלת הפחתה."
              checked={draft.mastering_denoise}
              onChange={(v) => set('mastering_denoise', v)} />

            <Toggle
              label="דחיסה"
              hint="מופעלת רק כשההפרש בין הפסגות לדיבור גדול מדי."
              checked={draft.mastering_compress}
              onChange={(v) => set('mastering_compress', v)} />

            <Warning tone="info">
              עיבוד אודיו הוא הרסני: הפחתת רעש מרככת את הקול ודחיסה
              מוחקת דינמיקה. לכן כל שלב מופעל רק כשהמדידה מראה שהוא
              נחוץ, וקליפינג שנוצר בהקלטה מדווח ולא „מתוקן”.
            </Warning>
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

function OptionList({ options, value, onChange }: {
  options: [string, string, string][]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="space-y-2">
      {options.map(([key, label, hint]) => (
        <label key={key || 'auto'}
               className={`flex items-start gap-2.5 rounded-lg border p-2.5
                 cursor-pointer transition-colors ${value === key
                   ? 'border-brand-500/50 bg-brand-600/10'
                   : 'border-ink-750 bg-ink-900 hover:border-ink-600'}`}>
          <input type="radio" className="mt-1 accent-brand-500"
                 checked={value === key}
                 onChange={() => onChange(key)} />
          <span className="min-w-0">
            <span className="block text-xs font-medium text-white">{label}</span>
            <span className="block text-[11px] text-ink-500 leading-relaxed">
              {hint}
            </span>
          </span>
        </label>
      ))}
    </div>
  )
}
