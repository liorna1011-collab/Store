import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api } from './lib/api'
import { useStore } from './lib/store'
import type { SystemInfo } from './lib/types'
import {
  IconAlert, IconChart, IconFilm, IconHome, IconImage, IconSettings, IconTasks,
  ToastHost,
} from './components/ui'
import HomePage from './pages/HomePage'
import JobsPage from './pages/JobsPage'
import JobDetailPage from './pages/JobDetailPage'
import ClipsPage from './pages/ClipsPage'
import ClipEditPage from './pages/ClipEditPage'
import ImagesPage from './pages/ImagesPage'
import SettingsPage from './pages/SettingsPage'

const NAV = [
  { to: '/', label: 'פרויקט חדש', Icon: IconHome, exact: true },
  { to: '/jobs', label: 'משימות', Icon: IconTasks },
  { to: '/clips', label: 'גלריית קליפים', Icon: IconFilm },
  { to: '/images', label: 'AI Images', Icon: IconImage },
  { to: '/settings', label: 'הגדרות', Icon: IconSettings },
]

export default function App() {
  const { jobs, connected } = useStore()
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const location = useLocation()

  useEffect(() => {
    api.system().then(setSystem).catch(() => setSystem(null))
  }, [])

  const activeCount = jobs.filter(
    (j) => j.status === 'running' || j.status === 'queued').length

  return (
    <div className="min-h-screen flex bg-ink-950 overflow-x-hidden">
      {/* ---- סרגל צד ---- */}
      <aside className="w-14 sm:w-64 shrink-0 border-l border-ink-800 bg-ink-900
                        flex flex-col sticky top-0 h-screen">
        <div className="px-2 sm:px-5 py-5 border-b border-ink-800">
          <div className="flex items-center gap-2.5 justify-center sm:justify-start">
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700
                            flex items-center justify-center shadow-lg shadow-brand-700/20">
              <IconChart className="w-5 h-5 text-white" />
            </div>
            <div className="hidden sm:block">
              <div className="text-white font-semibold tracking-tight leading-none">Polixor</div>
              <div className="text-[11px] text-ink-500 mt-1">
                קליפים אוטומטיים משידורים
              </div>
            </div>
          </div>
        </div>

        <nav className="flex-1 p-2 sm:p-3 space-y-1">
          {NAV.map(({ to, label, Icon, exact }) => (
            <NavLink key={to} to={to} end={exact}
                     title={label}
                     className={({ isActive }) =>
                       `nav-link justify-center sm:justify-start ${
                         isActive ? 'nav-link-active' : ''}`}>
              <Icon className="w-[18px] h-[18px] shrink-0" />
              <span className="flex-1 hidden sm:block">{label}</span>
              {to === '/jobs' && activeCount > 0 && (
                <span className="chip bg-brand-600/25 text-brand-300 ltr-nums
                                 hidden sm:inline-flex">
                  {activeCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        {/* ---- מצב המערכת ---- */}
        <div className="p-2 sm:p-3 border-t border-ink-800 space-y-2">
          <div className="flex items-center gap-2 px-2 text-[11px]
                          justify-center sm:justify-start"
               title={connected ? 'מחובר לשרת' : 'מנותק מהשרת'}>
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
              connected ? 'bg-ok' : 'bg-bad'}`} />
            <span className="text-ink-500 hidden sm:block">
              {connected ? 'מחובר לשרת' : 'מנותק מהשרת'}
            </span>
          </div>
          {system && !system.ffmpeg.available && (
            <div className="hidden sm:flex items-start gap-2 rounded-lg bg-bad/10
                            border border-bad/30 p-2.5">
              <IconAlert className="w-4 h-4 text-bad shrink-0 mt-px" />
              <div className="text-[11px] text-bad leading-relaxed">
                FFmpeg לא נמצא — עיבוד וידאו לא יעבוד.
              </div>
            </div>
          )}
          {system && (
            <div className="hidden sm:block px-2 text-[11px] text-ink-600 leading-relaxed">
              גרסה {system.app.version} · {system.platform}
              {system.gpu.cuda && <><br />GPU: {system.gpu.name}</>}
            </div>
          )}
        </div>
      </aside>

      {/* ---- תוכן ---- */}
      <main className="flex-1 min-w-0">
        <div key={location.pathname} className="animate-fade-up">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/jobs/:jobId" element={<JobDetailPage />} />
            <Route path="/clips" element={<ClipsPage />} />
            <Route path="/clips/:clipId/edit" element={<ClipEditPage />} />
            <Route path="/images" element={<ImagesPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </main>

      <ToastHost />
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: {
  title: string
  subtitle?: string
  actions?: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-white tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  )
}
