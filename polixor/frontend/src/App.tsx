import React, { Component, Suspense, lazy, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Clapperboard, Film, Home, Image as ImageIcon, LogOut, Menu, Plus, Send, Settings, TriangleAlert, X,
} from 'lucide-react'
import { api, loginUrl, proxyAuthExpired, sessionExpired } from './lib/api'
import { CLIENT_BUILD, flush as flushFailures, reloadIsSafe } from './lib/diag'
import { useConnected, useStore, useToasts } from './lib/store'
import UploadTray from './components/UploadTray'
import type { SystemInfo } from './lib/types'
import { Modal, Skeleton, ToastRegion, cx } from './components/ds'
import { ThemeToggle } from './components/prefs'
import { NotificationBell } from './components/notifications'
import DashboardPage from './pages/DashboardPage'

// כל דף נטען כשנכנסים אליו: הדף הראשון לא מחכה לקוד של עורך הקליפים, הפרסום והתמונות
const NewProjectPage = lazy(() => import('./pages/NewProjectPage'))
const ProjectPage = lazy(() => import('./pages/ProjectPage'))
const ClipsPage = lazy(() => import('./pages/ClipsPage'))
const ClipEditPage = lazy(() => import('./pages/ClipEditPage'))
const ImagesPage = lazy(() => import('./pages/ImagesPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const PublishingPage = lazy(() => import('./pages/PublishingPage'))
const JobDetailPage = lazy(() => import('./pages/JobDetailPage'))
const AdminPage = lazy(() => import('./pages/AdminPage'))

/**
 * A page's code could not be loaded (connection lost, or a new version was deployed while the
 * tab was open) or the page crashed: a clear message and a retry – never a blank screen.
 */
class PageBoundary extends Component<{ children: React.ReactNode; resetKey: string; t: (k: string) => string },
  { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch() {
    // whatever the browser's wording (a failed lazy import surfaces in several forms), the same
    // three checks decide: signed out → sign-in dialog; newer build → reload; offline → this message
    void explainChunkFailure()
  }
  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false })
  }
  render() {
    if (!this.state.failed) return this.props.children
    const { t } = this.props
    return (
      <div role="alert" className="card p-6 space-y-3" data-testid="page-failed">
        <h2 className="text-base font-semibold">{t('common.pageFailed.title')}</h2>
        <p className="text-sm text-ink-400">{t('common.pageFailed.body')}</p>
        <button type="button" className="btn-primary" onClick={() => window.location.reload()}>
          {t('common.pageFailed.reload')}</button>
      </div>
    )
  }
}

/**
 * The server serves a newer interface than this tab runs: stale JS must not keep talking to a
 * newer backend. Reloads by itself after a few seconds – unless an upload is in flight, then
 * it waits for the upload (or the user reloads now: the upload continues where it stopped).
 */
function NewVersion() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [auto, setAuto] = useState(true)
  useEffect(() => {
    const on = (e: Event) => {
      const server = String((e as CustomEvent).detail?.server || 'chunk')
      // one automatic reload per server build: if the page is still stale after it (a broken
      // deployment), it asks instead of reloading in a loop
      let already = false
      try { already = sessionStorage.getItem('polixor.reloadedFor') === server } catch { /* */ }
      setAuto(!already)
      try { sessionStorage.setItem('polixor.reloadedFor', server) } catch { /* */ }
      setOpen(true)
    }
    window.addEventListener('polixor:new-version', on)
    return () => window.removeEventListener('polixor:new-version', on)
  }, [])
  useEffect(() => {
    if (!open || !auto) return
    const id = window.setInterval(() => {
      if (reloadIsSafe()) window.location.reload()
      else setBusy(true)
    }, 4000)
    return () => window.clearInterval(id)
  }, [open, auto])
  return (
    <Modal open={open} onClose={() => setOpen(false)} title={t('common.version.title')}
           footer={<button type="button" className="btn-primary" data-testid="reload-new-version"
                           onClick={() => window.location.reload()}>{t('common.version.reload')}</button>}>
      <p className="text-ink-100" data-testid="new-version">{busy ? t('common.version.bodyBusy') : auto ? t('common.version.body') : t('common.version.title')}</p>
    </Modal>
  )
}

/**
 * A page's code could not be fetched. Why decides what the user sees: an expired sign-in (ours, or
 * the Codespaces port's – its forwarder redirects every request to GitHub) → the session dialog;
 * the server serves another build (updated while the tab was open) → the new-version reload;
 * otherwise (offline) → the message with Try again, never an automatic reload into an error page.
 */
async function explainChunkFailure(): Promise<void> {
  if (!navigator.onLine) return
  if (await proxyAuthExpired()) { sessionExpired(); return }
  try {
    const me = await fetch('/api/me', { cache: 'no-store', redirect: 'manual' })
    if (me.status === 401 || me.type === 'opaqueredirect') { sessionExpired(); return }
    const r = await fetch('/api/health', { cache: 'no-store' })
    const b = r.headers.get('x-polixor-build')
    if (b && CLIENT_BUILD !== 'dev' && b !== CLIENT_BUILD) {
      window.dispatchEvent(new CustomEvent('polixor:new-version', { detail: { server: b } }))
    }
  } catch { /* offline: the page says so */ }
}

function PageFallback() {
  return <div className="space-y-4" aria-busy="true"><Skeleton className="h-10 w-72" /><Skeleton className="h-64" /></div>
}

/** "Your session expired. Sign in again." – the page stays as it is underneath; sign-in returns here. */
function SessionExpired() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const on = () => setOpen(true)
    window.addEventListener('polixor:session-expired', on)
    return () => window.removeEventListener('polixor:session-expired', on)
  }, [])
  return (
    <Modal open={open} onClose={() => setOpen(false)} title={t('common.session.title')}
           footer={<a href={loginUrl()} className="btn-primary">{t('common.session.signIn')}</a>}>
      <p className="text-ink-100">{t('common.session.body')}</p>
      <p className="text-sm text-ink-400 mt-2">{t('common.session.detail')}</p>
    </Modal>
  )
}

const NAV = [
  { to: '/', key: 'dashboard', Icon: Home, exact: true },
  { to: '/new', key: 'newProject', Icon: Plus },
  { to: '/clips', key: 'clips', Icon: Film },
  { to: '/publishing', key: 'publishing', Icon: Send },
  { to: '/images', key: 'images', Icon: ImageIcon },
  { to: '/settings', key: 'settings', Icon: Settings },
] as const

function Sidebar({ onNavigate, system, protectedMode = false }: {
  onNavigate?: () => void; system: SystemInfo | null; protectedMode?: boolean
}) {
  const { t } = useTranslation()
  const connected = useConnected()
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

/** Toasts subscribe on their own: a toast never re-renders the page underneath. */
function AppToasts() {
  const toasts = useToasts()
  const { dismissToast } = useStore()
  return <ToastRegion toasts={toasts} onDismiss={dismissToast} />
}

function LegacyJobRedirect() {
  const { jobId } = useParams()
  return <Navigate to={`/projects/${jobId}`} replace />
}

export default function App() {
  const { t } = useTranslation()
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  const [protectedMode, setProtectedMode] = useState(false)

  useEffect(() => { api.system().then(setSystem).catch(() => setSystem(null)) }, [])
  // failures recorded while signed out / offline are sent once the app runs again
  useEffect(() => { void flushFailures() }, [])
  // Vite reports a page's preloaded code that no longer exists (the server was updated)
  useEffect(() => {
    const on = (e: Event) => { e.preventDefault(); void explainChunkFailure() }
    window.addEventListener('vite:preloadError', on)
    return () => window.removeEventListener('vite:preloadError', on)
  }, [])
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
            <NotificationBell />
            <ThemeToggle />
          </div>
        </header>
        <main id="main" className="flex-1 min-w-0 px-4 sm:px-6 lg:px-8 py-6 lg:py-8">
          <div key={location.pathname} className="mx-auto max-w-6xl animate-fade-up">
            <PageBoundary resetKey={location.pathname} t={t}>
            <Suspense fallback={<PageFallback />}>
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/new" element={<NewProjectPage />} />
              <Route path="/projects/:projectId" element={<ProjectPage />} />
              <Route path="/clips" element={<ClipsPage />} />
              <Route path="/clips/:clipId/edit" element={<ClipEditPage />} />
              <Route path="/images" element={<ImagesPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/publishing" element={<PublishingPage />} />
              <Route path="/jobs" element={<Navigate to="/" replace />} />
              <Route path="/jobs/:jobId" element={<LegacyJobRedirect />} />
              <Route path="/legacy/jobs/:jobId" element={<JobDetailPage />} />
              <Route path="/admin" element={<AdminPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            </Suspense>
            </PageBoundary>
          </div>
        </main>
      </div>

      <AppToasts />
      <UploadTray />
      <SessionExpired />
      <NewVersion />
    </div>
  )
}
