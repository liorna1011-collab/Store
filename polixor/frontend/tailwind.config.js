/** @type {import('tailwindcss').Config} */

// כל הצבעים הניטרליים וצבעי המצב הם משתני CSS (ראו index.css), כך שאותן
// מחלקות עובדות בערכת צבעים בהירה (ברירת מחדל) וכהה. ה-ink הוא סולם
// ניטרלי שמתהפך בין הערכות: 950 = רקע העמוד, 100 = הטקסט החזק ביותר.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        ink: {
          100: v('ink-100'), 200: v('ink-200'), 300: v('ink-300'), 400: v('ink-400'),
          500: v('ink-500'), 600: v('ink-600'), 700: v('ink-700'), 750: v('ink-750'),
          800: v('ink-800'), 850: v('ink-850'), 900: v('ink-900'), 950: v('ink-950'),
        },
        brand: {
          50: v('brand-50'), 100: v('brand-100'), 200: v('brand-200'), 300: v('brand-300'),
          400: v('brand-400'), 500: v('brand-500'), 600: v('brand-600'), 700: v('brand-700'),
          800: v('brand-800'),
        },
        ok: v('ok'),
        warn: v('warn'),
        bad: v('bad'),
        on: v('on-brand'),
      },
      fontFamily: {
        sans: ['"Inter Variable"', '"Heebo Variable"', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"Cascadia Mono"', 'Consolas', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgb(var(--shadow) / 0.06), 0 1px 3px rgb(var(--shadow) / 0.08)',
        pop: '0 12px 32px rgb(var(--shadow) / 0.18)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '200% 0' },
          '100%': { backgroundPosition: '-200% 0' },
        },
        indeterminate: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(250%)' },
        },
      },
      animation: {
        'fade-up': 'fade-up .25s ease-out',
        shimmer: 'shimmer 1.8s linear infinite',
        indeterminate: 'indeterminate 1.4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
