import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// One id per build: compiled into the JS and written to dist/version.json, which the server
// reads and sends on every response (X-Polixor-Build). A tab running an older build sees the
// difference and offers a reload instead of talking to a newer backend with stale code.
const BUILD_ID = process.env.POLIXOR_BUILD_ID || new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14)

function versionFile(): Plugin {
  return {
    name: 'polixor-version',
    apply: 'build',
    closeBundle() {
      writeFileSync(resolve(process.cwd(), 'dist/version.json'), JSON.stringify({ build: BUILD_ID }) + '\n')
    },
  }
}

// שרת הפיתוח מעביר קריאות API ו-WebSocket לשרת ה-Python.
export default defineConfig(({ command }) => ({
  plugins: [react(), versionFile()],
  // the dev server (vite serve) talks to whatever backend runs: no version check there
  define: { __BUILD_ID__: JSON.stringify(command === 'build' ? BUILD_ID : 'dev') },
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
}))
