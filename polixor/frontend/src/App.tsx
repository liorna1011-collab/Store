import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Clapperboard, Film, Home, Image as ImageIcon, LogOut, Menu, Plus, Settings, TriangleAlert, X,
} from 'lucide-react'
import { api } from './lib/api'
import { useStore } from './lib/store'
import type { SystemInfo } from './lib/types'
import { ToastRegion, cx } from './components/ds'
import { ThemeToggle } from './components/prefs'
import DashboardPage from './pages/DashboardPage'
import NewProjectPage from './pages/NewProjectPage'
import ProjectPage from './pages/ProjectPage'
import ClipsPage from './pages/ClipsPage'
import ClipEditPage from './pages/ClipEditPage'
import ImagesPage from './pages/ImagesPage'
import SettingsPage from './pages/SettingsPage'
import JobDetailPage from './pages/JobDetailPage'

const NAV = [
  { to: '/', key: 'dashboard', Icon: Home, exact: true },
  { to: '/new', key: 'newProject', Icon: Plus },
  { to: '/clips', key: 'clips', Icon: Film },
  { to: '/images', key: 'images', Icon: ImageIcon },
  { to: '/settings', key: 'settings', Icon: Settings },
] as const

function Sidebar({ onNavigate, system, protectedMode = false }: {
  onNavigate?: () => void; system: SystemInfo | null; protectedMode?: boolean
}) {
  const { t } = useTranslation()
  const { connected } = useStore()
  return (
    <div className="flex h-full flex-col">
      <div className="px-5 py-5 border-b border-ink-750">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center shadow-card">
            <Clapperboard className="w-5 h-5 text-on" aria-hidden />
          </div>
          <div>
            <div className="font-semibold tracking-tight leading-none text-ink-100">Polixor</div>
            <div className="text-[11px] text-ink-500 mt-1">{t('common.tagline')}</div>
          </div>
        </div>
      </div>
      <nav className="flex-1 p-3 space-y-1" aria-label={t('nav.main')}>
        {NAV.map(({ to, key, Icon, ...rest }) => (
          <NavLink key={to} to={to} end={'exact' in rest} onClick={onNavigate}
                   className={({ isActive }) => cx('nav-link', isActive && 'nav-link-active')}>
            <Icon className="w-[18px] h-[18px] shrink-0" aria-hidden />
            <span className="flex-1">{t(`nav.${key}`)}</span>
          </NavLink>
        ))}
      </nav>
      <div className="p-3 border-t border-ink-750 space-y-2">
        <div className="flex items-center gap-2 px-2 text-xs text-ink-500">
          <span className={cx('w-2 h-2 rounded-full', connected ? 'bg-ok' : 'bg-bad')} aria-hidden />
          {connected ? t('nav.connected') : t('nav.disconnected')}
        </div>
        {system && !system.ffmpeg.available && (
          <div className="flex items-start gap-2 rounded-lg bg-bad/5 ring-1 ring-bad/25 p-2.5">
            <TriangleAlert className="w-4 h-4 text-bad shrink-0 mt-px" aria-hidden />
            <div className="text-xs text-bad leading-relaxed">{t('nav.ffmpegMissing')}</div>
          </div>
        )}
        {system && (
          <div className="px-2 text-[11px] text-ink-500 leading-relaxed">
            {t('nav.version', { version: system.app.version })} · <span className="ltr-nums">{system.platform}</span>
            {system.gpu.cuda && <><br />GPU: <span className="ltr-nums">{system.gpu.name}</span></>}
          </div>
        )}
        {protectedMode && (
          <form method="post" action="/logout">
            <button type="submit" className="nav-link w-full text-xs">
              <LogOut className="w-4 h-4 shrink-0" aria-hidden />{t('nav.signOut')}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}

function LegacyJobRedirect() {
  const { jobId } = useParams()
  return <Navigate to={`/projects/${jobId}`} replace />
}

export default function App() {
  const { t } = useTranslation()
  const { toasts, dismissToast } = useStore()
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  const [protectedMode, setProtectedMode] = useState(false)

  useEffect(() => { api.system().then(setSystem).catch(() => setSystem(null)) }, [])
  useEffect(() => { api.health().then((h) => setProtectedMode(Boolean(h.access_protected))).catch(() => undefined) }, [])
  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  return (
    <div className="min-h-screen flex bg-ink-950">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:start-2 focus:top-2
                                 focus:z-50 btn-primary">{t('common.skipToContent')}</a>
      <aside className="hidden lg:block w-64 shrink-0 border-e border-ink-750 bg-ink-850 sticky top-0 h-screen">
        <Sidebar system={system} protectedMode={protectedMode} />
      </aside>

      {menuOpen && (
        <div className="lg:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMenuOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 start-0 w-72 max-w-[85vw] bg-ink-850 shadow-pop animate-fade-up">
            <button type="button" onClick={() => setMenuOpen(false)} aria-label={t('common.close')}
                    className="btn-quiet !p-2 absolute top-4 end-3"><X className="w-4 h-4" /></button>
            <Sidebar system={system} protectedMode={protectedMode} onNavigate={() => setMenuOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-ink-750
                           bg-ink-950/85 backdrop-blur px-4 sm:px-6 h-14">
          <div className="flex items-center gap-2">
            <button type="button" className="lg:hidden btn-quiet !p-2" onClick={() => setMenuOpen(true)}
                    aria-label={t('nav.openMenu')}><Menu className="w-5 h-5" /></button>
            <span className="lg:hidden font-semibold text-ink-100">Polixor</span>
          </div>
          <div className="flex items-center gap-1 sm:gap-2">
            <ThemeToggle />
          </div>
        </header>
        <main id="main" className="flex-1 min-w-0 px-4 sm:px-6 lg:px-8 py-6 lg:py-8">
          <div key={location.pathname} className="mx-auto max-w-6xl animate-fade-up">
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/new" element={<NewProjectPage />} />
              <Route path="/projects/:projectId" element={<ProjectPage />} />
              <Route path="/clips" element={<ClipsPage />} />
              <Route path="/clips/:clipId/edit" element={<ClipEditPage />} />
              <Route path="/images" element={<ImagesPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/jobs" element={<Navigate to="/" replace />} />
              <Route path="/jobs/:jobId" element={<LegacyJobRedirect />} />
              <Route path="/legacy/jobs/:jobId" element={<JobDetailPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>

      <ToastRegion toasts={toasts} onDismiss={dismissToast} />
    </div>
  )
}
