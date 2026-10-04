// מערכת תרגום אחת לכל הממשק (i18next). אותן יכולות בשתי השפות – רק
// הטקסטים והכיוון משתנים.
//
// שפת הממשק נבחרת אוטומטית – אין בורר שפה:
//   1. `?lang=he|en` בכתובת – נסתר, לבדיקות, תמיכה ופיתוח בלבד. נשמר
//      ללשונית הנוכחית בלבד (sessionStorage); `?lang=auto` מבטל.
//   2. אות מהשרת: כותרת מדינה מ-CDN/פרוקסי מהימן (/api/locale). ישראל → עברית.
//   3. הדפדפן: אזור זמן ישראלי או שפת דפדפן עברית → עברית.
//   4. אחרת – אנגלית (LTR).
// אין GPS ואין שמירת מיקום. בחירה ידנית ישנה (localStorage "polixor.lang")
// מגרסאות קודמות נמחקת ולא משפיעה. שפת הדיבור בסרטונים מזוהה בנפרד.
// השפה נשלחת לשרת בכל בקשה (X-Polixor-Lang), כך שגם הודעות השרת בשפה הנכונה.

import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import heCommon from './locales/he/common.json'
import heNav from './locales/he/nav.json'
import heDashboard from './locales/he/dashboard.json'
import heImport from './locales/he/import.json'
import heProject from './locales/he/project.json'
import heSubtitles from './locales/he/subtitles.json'
import heClips from './locales/he/clips.json'
import heEditor from './locales/he/editor.json'
import heSettings from './locales/he/settings.json'
import heImages from './locales/he/images.json'
import heLegacy from './locales/he/legacy.json'
import heNotifications from './locales/he/notifications.json'
import hePublishing from './locales/he/publishing.json'
import heStudio from './locales/he/studio.json'
import heCreator from './locales/he/creator.json'
import enCommon from './locales/en/common.json'
import enNav from './locales/en/nav.json'
import enDashboard from './locales/en/dashboard.json'
import enImport from './locales/en/import.json'
import enProject from './locales/en/project.json'
import enSubtitles from './locales/en/subtitles.json'
import enClips from './locales/en/clips.json'
import enEditor from './locales/en/editor.json'
import enSettings from './locales/en/settings.json'
import enImages from './locales/en/images.json'
import enLegacy from './locales/en/legacy.json'
import enNotifications from './locales/en/notifications.json'
import enPublishing from './locales/en/publishing.json'
import enStudio from './locales/en/studio.json'
import enCreator from './locales/en/creator.json'

export const LANGUAGES = [
  { code: 'he', label: 'עברית', dir: 'rtl' },
  { code: 'en', label: 'English', dir: 'ltr' },
] as const

export type Lang = (typeof LANGUAGES)[number]['code']

const LEGACY_STORAGE_KEY = 'polixor.lang'     // בורר השפה הישן – מתעלמים ומוחקים
const OVERRIDE_KEY = 'polixor.langOverride'   // `?lang=` – ללשונית הנוכחית בלבד
const ISRAEL_TZ = ['Asia/Jerusalem', 'Asia/Tel_Aviv']

export const resources = {
  he: {
    translation: {
      common: heCommon, nav: heNav, dashboard: heDashboard, import: heImport,
      project: heProject, subtitles: heSubtitles, clips: heClips, editor: heEditor,
      settings: heSettings, images: heImages, legacy: heLegacy, notifications: heNotifications, publishing: hePublishing, studio: heStudio, creator: heCreator,
    },
  },
  en: {
    translation: {
      common: enCommon, nav: enNav, dashboard: enDashboard, import: enImport,
      project: enProject, subtitles: enSubtitles, clips: enClips, editor: enEditor,
      settings: enSettings, images: enImages, legacy: enLegacy, notifications: enNotifications, publishing: enPublishing, studio: enStudio, creator: enCreator,
    },
  },
} as const

function asLang(v: string | null | undefined): Lang | null {
  const x = (v || '').trim().toLowerCase()
  return x === 'he' || x === 'iw' ? 'he' : x === 'en' ? 'en' : null
}

function forgetLegacyChoice() {
  try { localStorage.removeItem(LEGACY_STORAGE_KEY) } catch { /* מצב פרטי */ }
}

/** `?lang=` (נסתר). נקרא מהכתובת פעם אחת ונשמר ללשונית. */
export function readOverride(): Lang | null {
  try {
    const q = new URLSearchParams(window.location.search).get('lang')
    if (q !== null) {
      const v = asLang(q)
      if (v) sessionStorage.setItem(OVERRIDE_KEY, v)
      else sessionStorage.removeItem(OVERRIDE_KEY)       // ?lang=auto
      return v
    }
    return asLang(sessionStorage.getItem(OVERRIDE_KEY))
  } catch {
    return null
  }
}

/** לפי הדפדפן בלבד: אזור זמן ישראלי או שפה עברית → עברית, אחרת null. */
export function browserLanguage(): Lang | null {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (tz && ISRAEL_TZ.includes(tz)) return 'he'
  } catch { /* דפדפן בלי Intl מלא */ }
  const langs = typeof navigator !== 'undefined'
    ? [...(navigator.languages || []), navigator.language] : []
  if (langs.some((l) => /^(he|iw)\b/i.test(l || ''))) return 'he'
  return null
}

/**
 * השפה לפי הכללים שבראש הקובץ. `country` – השפה שהשרת גזר מכותרת המדינה
 * (או null כשאין כותרת / המדינה אינה ישראל).
 */
export function resolveLanguage(override: Lang | null, country: Lang | null,
                                browser: Lang | null): Lang {
  return override ?? country ?? browser ?? 'en'
}

/** ההחלטה הראשונית, בלי לחכות לשרת. */
export function detectLanguage(): Lang {
  return resolveLanguage(readOverride(), null, browserLanguage())
}

export function dirOf(lang: string): 'rtl' | 'ltr' {
  return lang === 'he' ? 'rtl' : 'ltr'
}

function syncDocument(lang: Lang) {
  const html = document.documentElement
  html.lang = lang
  html.dir = dirOf(lang)
  document.title = i18n.t('common.appTitle')
}

export function currentLang(): Lang {
  return (i18n.resolvedLanguage || i18n.language) === 'en' ? 'en' : 'he'
}

async function applyLanguage(lang: Lang): Promise<void> {
  if (currentLang() !== lang || i18n.language !== lang) await i18n.changeLanguage(lang)
}

/**
 * משלב את האות מהשרת (כותרת מדינה). נקרא לפני הצגת הממשק, עם זמן המתנה
 * קצר – אם השרת לא עונה בזמן נשארים עם ההחלטה לפי הדפדפן.
 */
export async function initLanguage(timeoutMs = 800): Promise<Lang> {
  forgetLegacyChoice()
  const override = readOverride()
  if (override) {
    await applyLanguage(override)
    return override
  }
  let country: Lang | null = null
  try {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), timeoutMs)
    const res = await fetch('/api/locale', { signal: ctl.signal, credentials: 'same-origin' })
    clearTimeout(timer)
    if (res.ok) country = asLang((await res.json())?.lang)
  } catch { /* אין שרת / זמן עבר – לפי הדפדפן */ }
  const lang = resolveLanguage(null, country, browserLanguage())
  await applyLanguage(lang)
  return lang
}

void i18n.use(initReactI18next).init({
  resources,
  lng: detectLanguage(),
  fallbackLng: 'en',
  supportedLngs: ['he', 'en'],
  interpolation: { escapeValue: false },
  returnNull: false,
})

i18n.on('languageChanged', (lng) => syncDocument(lng === 'en' ? 'en' : 'he'))
syncDocument(currentLang())

export default i18n
