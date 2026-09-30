// בורר ערכת הצבעים. משותף לסרגל העליון ולמסך ההגדרות.
// (אין בורר שפה: שפת הממשק נבחרת אוטומטית – ראו i18n/index.ts)

import { useTranslation } from 'react-i18next'
import { Moon, Sun } from 'lucide-react'
import { useTheme } from '../lib/theme'

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
