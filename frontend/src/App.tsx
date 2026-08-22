import { Component, FormEvent, useEffect, useState } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ApiError, api } from './api/client'
import { AppShell } from './components/Shell'
import { Button, Input } from './components/ui'
import { CustomFormatsPage, DownloadsPage, EventHistoryPage, MovieDetailPage, MoviesPage, ProblemsPage, SettingsPage, ShowDetailPage, ShowsPage, TorrentArchivePage } from './pages'
import QualityProfilesPage from './QualityProfilesPage'
import { SetupPage } from './SetupPage'

function App() {
  const [authenticated, setAuthenticated] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    let alive = true
    api.session()
      .then(() => { if (alive) setAuthenticated(true) })
      .catch(() => { if (alive) setAuthenticated(false) })
      .finally(() => { if (alive) setChecking(false) })
    return () => { alive = false }
  }, [])

  const login = () => { setAuthenticated(true); setChecking(false) }
  const logout = async () => { try { await api.logout() } catch { /* session may already be expired */ } setAuthenticated(false) }
  if (checking) return <BootScreen />
  return authenticated ? <BrowserRouter><AuthenticatedApp onLogout={logout} /></BrowserRouter> : <LoginPage onLogin={login} />
}

function AuthenticatedApp({ onLogout }: { onLogout: () => void }) {
  const location = useLocation()
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)

  useEffect(() => {
    let alive = true
    api.setupStatus().then((status) => { if (alive) setSetupRequired(status.wizard_required) }).catch(() => { if (alive) setSetupRequired(false) })
    return () => { alive = false }
  }, [])

  if (setupRequired === null) return <BootScreen />
  const setupSettings = location.pathname === '/settings' && new URLSearchParams(location.search).get('setup') === '1'
  if (setupRequired && location.pathname !== '/setup' && !setupSettings) return <Navigate to="/setup" replace />

  return <AppShell onLogout={onLogout}><PageErrorBoundary key={location.pathname}><Routes>
    <Route path="/setup" element={<SetupPage onComplete={() => setSetupRequired(false)} />} />
    <Route path="/movies" element={<MoviesPage />} />
    <Route path="/movies/:id" element={<MovieDetailRoute />} />
    <Route path="/shows" element={<ShowsPage />} />
    <Route path="/shows/:id" element={<ShowDetailRoute />} />
    <Route path="/downloads" element={<DownloadsPage />} />
    <Route path="/problems" element={<ProblemsPage />} />
    <Route path="/torrent-archive" element={<TorrentArchivePage />} />
    <Route path="/custom-formats" element={<CustomFormatsPage />} />
    <Route path="/quality-profiles" element={<QualityProfilesPage />} />
    <Route path="/events" element={<EventHistoryPage />} />
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="*" element={<Navigate to={setupRequired ? '/setup' : '/movies'} replace />} />
  </Routes></PageErrorBoundary></AppShell>
}

class PageErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Medialogue page render failed', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return <div className="page"><div className="page-heading"><div className="heading-copy"><div className="eyebrow">PAGE ERROR</div><h1>This page crashed.</h1><p>The rest of Medialogue is still running. Refresh this page or navigate elsewhere while the error is investigated.</p></div></div><div className="settings-note error-note"><span>{this.state.error.message || 'Unexpected frontend error'}</span></div><Button variant="primary" onClick={() => window.location.reload()}>Reload page</Button></div>
  }
}

function BootScreen() { return <div className="boot-screen"><div className="brand-mark"><span>✦</span></div><span>Loading Medialogue…</span></div> }
function MovieDetailRoute() { const { id = 'inception' } = useParams(); return <MovieDetailPage id={id} /> }
function ShowDetailRoute() { const { id = '' } = useParams(); return <ShowDetailPage id={id} /> }

function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setLoading(true)
    try {
      await api.login(username, password)
      onLogin()
    } catch (reason) {
      if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) setError(reason.message || 'Invalid username or password.')
      else setError('Unable to reach the server. Wait for the API to become ready and try again.')
    } finally { setLoading(false) }
  }
  return <div className="login-screen"><div className="login-glow login-glow-one" /><div className="login-glow login-glow-two" /><div className="login-panel"><div className="login-brand"><div className="brand-mark"><span>✦</span></div><div><div className="brand-name">MEDIA<span>LOGUE</span></div><div className="brand-caption">LEAVE-IN-PLACE LIBRARY</div></div></div><div className="login-copy"><div className="eyebrow">ADMIN CONSOLE</div><h1>Welcome back.</h1><p>Manage your library while it stays exactly where you left it.</p></div><form onSubmit={submit} className="login-form"><label><span className="field-label">Username</span><Input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label><label><span className="field-label">Password</span><Input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" autoFocus /></label>{error && <div className="login-error">{error}</div>}<Button type="submit" variant="primary" disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}<span className="button-arrow">→</span></Button></form><div className="login-footer"><span><span className="health-dot green" />Local session security enabled</span><span>v0.1.0</span></div></div><div className="login-rail"><div className="rail-quote">“The filesystem is the source of truth.”</div><div className="rail-lines"><span /><span /><span /></div><div className="rail-caption"><span className="health-dot green" />All media remains in place</div></div></div>
}

export default App
