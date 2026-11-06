import ReplaceMediaPage from "./ReplaceMediaPage"
import { Component, FormEvent, useEffect, useState } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import medialogueBanner from './assets/medialogue-banner.png'
import medialogueLogoAnimated from './assets/medialogue-logo-animated.svg'
import medialogueLogo from './assets/medialogue-logo.svg'
import { ConfirmProvider } from './components/Confirm'
import { ApiError } from './api/transport'
import { authApi } from './api/modules/auth'
import { AppShell } from './components/Shell'
import { Button, Input } from './components/ui'
import BackupsPage from './BackupsPage'
import { BlocklistPage, CustomFormatsPage, DownloadsPage, EventHistoryPage, MovieDetailPage, MoviesPage, NewMediaPage, ProblemsPage, SettingsPage, ShowDetailPage, ShowsPage, TasksPage, UpgradeMediaPage } from './pages'
import QualityProfilesPage from './QualityProfilesPage'

function App() {
  const [authenticated, setAuthenticated] = useState(false)
  const [setupRequired, setSetupRequired] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    let alive = true
    authApi.session()
      .then(() => { if (alive) setAuthenticated(true) })
      .catch(async () => {
        if (!alive) return
        setAuthenticated(false)
        try {
          const status = await authApi.bootstrapStatus()
          if (alive) setSetupRequired(status.required)
        } catch {
          if (alive) setSetupRequired(false)
        }
      })
      .finally(() => { if (alive) setChecking(false) })
    return () => { alive = false }
  }, [])

  const login = () => { setAuthenticated(true); setChecking(false) }
  const logout = async () => { try { await authApi.logout() } catch {} setAuthenticated(false) }
  if (checking) return <BootScreen />
  return authenticated
    ? <ConfirmProvider><BrowserRouter><AuthenticatedApp onLogout={logout} /></BrowserRouter></ConfirmProvider>
    : setupRequired
      ? <FirstRunSetupPage onSetup={login} />
      : <LoginPage onLogin={login} />
}

function AuthenticatedApp({ onLogout }: { onLogout: () => void }) {
  const location = useLocation()

  return <AppShell onLogout={onLogout}><PageErrorBoundary key={location.pathname}><Routes>
    <Route path="/movies" element={<MoviesPage />} />
    <Route path="/movies/:id" element={<MovieDetailRoute />} />
    <Route path="/shows" element={<ShowsPage />} />
    <Route path="/shows/:id" element={<ShowDetailRoute />} />
    <Route path="/downloads" element={<DownloadsPage />} />
    <Route path="/blocklist" element={<BlocklistPage />} />
    <Route path="/replace-media" element={<ReplaceMediaPage />} />
    <Route path="/upgrade-media" element={<UpgradeMediaPage />} />
    <Route path="/new-media" element={<NewMediaPage />} />
    <Route path="/problems" element={<ProblemsPage />} />
    <Route path="/backups" element={<BackupsPage />} />
    <Route path="/torrent-archive" element={<Navigate to="/backups" replace />} />
    <Route path="/custom-formats" element={<CustomFormatsPage />} />
    <Route path="/quality-profiles" element={<QualityProfilesPage />} />
    <Route path="/events" element={<EventHistoryPage />} />
    <Route path="/tasks" element={<TasksPage />} />
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="*" element={<Navigate to="/movies" replace />} />
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

function BootScreen() { return <div className="boot-screen"><picture className="boot-logo"><source media="(prefers-reduced-motion: reduce)" srcSet={medialogueLogo} /><img src={medialogueLogoAnimated} alt="" /></picture><span>Loading Medialogue…</span></div> }
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
      await authApi.login(username, password)
      onLogin()
    } catch (reason) {
      if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) setError(reason.message || 'Invalid username or password.')
      else setError('Unable to reach the server. Wait for the API to become ready and try again.')
    } finally { setLoading(false) }
  }
  return <div className="login-screen">
    <div className="login-panel">
      <img className="login-brand-banner" src={medialogueBanner} alt="Medialogue" />
      <form onSubmit={submit} className="login-form">
        <label><span className="field-label">Username</span><Input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label>
        <label><span className="field-label">Password</span><Input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" autoFocus /></label>
        {error && <div className="login-error">{error}</div>}
        <Button type="submit" variant="primary" disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}</Button>
      </form>
    </div>
    <div className="login-footer">v0.1.0</div>
  </div>
}

function FirstRunSetupPage({ onSetup }: { onSetup: () => void }) {
  const [setupToken, setSetupToken] = useState('')
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    if (password.length < 12) {
      setError('Choose a password with at least 12 characters.')
      return
    }
    if (password !== confirmation) {
      setError('The password confirmation does not match.')
      return
    }
    setLoading(true)
    try {
      await authApi.bootstrap(setupToken, username, password)
      onSetup()
    } catch (reason) {
      if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) setError(reason.message || 'The setup details were rejected.')
      else setError('Unable to reach the server. Wait for the API to become ready and try again.')
    } finally {
      setLoading(false)
    }
  }

  return <div className="login-screen">
    <div className="login-panel">
      <img className="login-brand-banner" src={medialogueBanner} alt="Medialogue" />
      <div className="login-form">
        <div className="heading-copy"><details><summary>Restoring after losing a server?</summary><p>Finish setup first. Then add your media folders and qBittorrent clients, download the Medialogue Backups folder from Google Drive, and upload it on <strong>Backups → Restore</strong>. Every torrent goes back to its original folder and downloads again.</p></details><div className="eyebrow">FIRST RUN</div><h1>Set up your administrator account</h1><p>Read the one-time setup token from the container, then choose the password used for normal sign-in.</p></div>
        <form onSubmit={submit} className="login-form">
          <label><span className="field-label">Setup token</span><Input value={setupToken} onChange={(event) => setSetupToken(event.target.value)} autoComplete="off" spellCheck={false} /></label>
          <label><span className="field-label">Administrator username</span><Input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label>
          <label><span className="field-label">Password</span><Input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="new-password" autoFocus /></label>
          <label><span className="field-label">Confirm password</span><Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} type="password" autoComplete="new-password" /></label>
          {error && <div className="login-error">{error}</div>}
          <Button type="submit" variant="primary" disabled={loading || !setupToken || !username || !password || !confirmation}>{loading ? 'Setting up…' : 'Create administrator'}</Button>
        </form>
      </div>
    </div>
    <div className="login-footer">v0.1.0</div>
  </div>
}

export default App
