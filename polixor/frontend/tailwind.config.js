/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          100: '#eef1f6',
          200: '#d7dde7',
          950: '#07080c',
          900: '#0c0e14',
          850: '#11131b',
          800: '#171a24',
          750: '#1e222e',
          700: '#272c3a',
          600: '#39404f',
          500: '#5a6376',
          400: '#8a93a6',
          300: '#b4bccb',
        },
        brand: {
          50: '#eef4ff',
          100: '#dbe6ff',
          200: '#b9cdff',
          300: '#8fb4ff',
          400: '#6b96ff',
          500: '#4b7bff',
          600: '#3563e8',
          700: '#274cc0',
          800: '#1d3a94',
        },
        ok: '#34d399',
        warn: '#fbbf24',
        bad: '#f87171',
      },
      fontFamily: {
        sans: ['"Segoe UI"', 'Rubik', 'Arial', '"Noto Sans Hebrew"', 'system-ui', 'sans-serif'],
        mono: ['"Cascadia Mono"', 'Consolas', 'monospace'],
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
      },
      animation: {
        'fade-up': 'fade-up .25s ease-out',
        shimmer: 'shimmer 1.8s linear infinite',
      },
    },
  },
  plugins: [],
}
