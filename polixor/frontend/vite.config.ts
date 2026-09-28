import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// שרת הפיתוח מעביר קריאות API ו-WebSocket לשרת ה-Python.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8756', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8756', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
})
