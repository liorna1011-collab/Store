// בוררי שפה וערכת צבעים. משותפים לסרגל העליון ולמסך ההגדרות.

import { useTranslation } from 'react-i18next'
import { Languages, Moon, Sun } from 'lucide-react'
import { LANGUAGES, currentLang, setLanguage, type Lang } from '../i18n'
import { useTheme } from '../lib/theme'
import { cx } from './ds'

export function LanguageSelect({ compact = false }: { compact?: boolean }) {
  const { t, i18n } = useTranslation()
  const lang = (i18n.resolvedLanguage as Lang) || currentLang()
  return (
    <label className="inline-flex items-center gap-1.5 text-sm">
      <Languages className="w-4 h-4 text-ink-500" aria-hidden />
      <span className="sr-only">{t('common.language')}</span>
      <select value={lang} onChange={(e) => void setLanguage(e.target.value as Lang)}
              aria-label={t('common.language')}
              className={cx('rounded-md border border-ink-700 bg-ink-850 text-ink-200 py-1 ps-2 pe-7',
                            'text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40',
                            compact && 'py-0.5')}>
        {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
      </select>
    </label>
  )
}

export function ThemeToggle() {
  const { t } = useTranslation()
  const [theme, setTheme] = useTheme()
  const dark = theme === 'dark'
  return (
    <button type="button" onClick={() => setTheme(dark ? 'light' : 'dark')}
            aria-label={dark ? t('common.theme.toLight') : t('common.theme.toDark')}
            title={dark ? t('common.theme.toLight') : t('common.theme.toDark')}
            className="btn-quiet !p-2">
      {dark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  )
}
