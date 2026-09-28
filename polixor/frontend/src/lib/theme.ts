// ערכת צבעים: בהירה כברירת מחדל, כהה כאפשרות. נשמרת בדפדפן.
// מקור אמת אחד לכל הממשק (הסרגל העליון ומסך ההגדרות מתעדכנים יחד).

import { useEffect, useReducer } from 'react'

export type Theme = 'light' | 'dark'
const KEY = 'polixor.theme'
const listeners = new Set<() => void>()

function readTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

let current: Theme = readTheme()

function apply(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', theme)
}

export function setTheme(theme: Theme): void {
  current = theme
  apply(theme)
  try { localStorage.setItem(KEY, theme) } catch { /* מצב פרטי */ }
  listeners.forEach((fn) => fn())
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [, force] = useReducer((x: number) => x + 1, 0)
  useEffect(() => {
    listeners.add(force)
    return () => { listeners.delete(force) }
  }, [])
  return [current, setTheme]
}

apply(current)
