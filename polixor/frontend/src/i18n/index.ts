// מערכת תרגום אחת לכל הממשק (i18next). אותן יכולות בשתי השפות – רק
// הטקסטים והכיוון משתנים.
//
// בחירת שפה בהפעלה הראשונה:
//   1. מה שהמשתמש בחר בעבר (localStorage "polixor.lang")
//   2. עברית – אם אזור הזמן של המחשב הוא ישראל, או שהדפדפן מבקש עברית
//   3. אחרת – אנגלית
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

export const LANGUAGES = [
  { code: 'he', label: 'עברית', dir: 'rtl' },
  { code: 'en', label: 'English', dir: 'ltr' },
] as const

export type Lang = (typeof LANGUAGES)[number]['code']

const STORAGE_KEY = 'polixor.lang'
const ISRAEL_TZ = ['Asia/Jerusalem', 'Asia/Tel_Aviv']

export const resources = {
  he: {
    translation: {
      common: heCommon, nav: heNav, dashboard: heDashboard, import: heImport,
      project: heProject, subtitles: heSubtitles, clips: heClips, editor: heEditor,
      settings: heSettings, images: heImages, legacy: heLegacy,
    },
  },
  en: {
    translation: {
      common: enCommon, nav: enNav, dashboard: enDashboard, import: enImport,
      project: enProject, subtitles: enSubtitles, clips: enClips, editor: enEditor,
      settings: enSettings, images: enImages, legacy: enLegacy,
    },
  },
} as const

function readStored(): Lang | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'he' || v === 'en' ? v : null
  } catch {
    return null
  }
}

/** השפה ההתחלתית לפי הכללים שבראש הקובץ. */
export function detectLanguage(): Lang {
  const stored = readStored()
  if (stored) return stored
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (tz && ISRAEL_TZ.includes(tz)) return 'he'
  } catch { /* דפדפן בלי Intl מלא */ }
  const langs = typeof navigator !== 'undefined'
    ? [...(navigator.languages || []), navigator.language] : []
  if (langs.some((l) => /^(he|iw)\b/i.test(l || ''))) return 'he'
  return 'en'
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

export async function setLanguage(lang: Lang): Promise<void> {
  try { localStorage.setItem(STORAGE_KEY, lang) } catch { /* מצב פרטי */ }
  await i18n.changeLanguage(lang)
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
