import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import medialogueBanner from '../assets/medialogue-banner.png'
import type { HealthIndicator } from '../types'
import { GlobalSearch, GlobalSearchButton } from './GlobalSearch'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'

const primary = [
  { to: '/movies', label: 'Movies', icon: 'clapper' as const },
  { to: '/shows', label: 'Shows', icon: 'tv-screen' as const },
  { to: '/review', label: 'Review', icon: 'review' as const },
  { to: '/downloads', label: 'Downloads', icon: 'download' as const },
  { to: '/problems', label: 'Problems', icon: 'alert' as const },
]
const tools = [
  { to: '/torrent-archive', label: 'Torrent Archive', icon: 'archive' as const },
  { to: '/custom-formats', label: 'Custom Formats', icon: 'custom-format' as const },
  { to: '/quality-profiles', label: 'Quality Profiles', icon: 'quality-profile' as const },
]
const system = [
  { to: '/tasks', label: 'Tasks', icon: 'activity' as const },
  { to: '/events', label: 'Events', icon: 'clock' as const },
  { to: '/settings', label: 'Settings', icon: 'settings' as const },
]

const fallbackHealth: HealthIndicator[] = [
  { name: 'Plex', state: 'unknown', detail: 'Not configured' },
  { name: 'qBittorrent', state: 'unknown', detail: 'Not configured' },
  { name: 'Storage', state: 'unknown', detail: 'No roots configured' },
  { name: 'Indexers', state: 'unknown', detail: 'Not configured' },
]

const PageTopbarHostContext = createContext<HTMLElement | null>(null)
const PlexAvailableContext = createContext(false)

export function usePlexAvailable() {
  return useContext(PlexAvailableContext)
}

export function PageTopbar({ title, subtitle, action, back, onBack }: { title: string; subtitle?: string; action?: React.ReactNode; back?: string; onBack?: () => void }) {
  const host = useContext(PageTopbarHostContext)
  if (!host) return null
  return createPortal(<div className="topbar-page-heading">
    {back && <Tooltip asChild content={back}><button className="topbar-back" type="button" onClick={onBack} aria-label={back}><Icon name="arrow" size={15} /></button></Tooltip>}
    <div className="topbar-page-copy"><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
    {action && <div className="topbar-page-actions">{action}</div>}
  </div>, host)
}

export function AppShell({ children, onLogout }: { children: React.ReactNode; onLogout: () => void }) {
  const [searchOpen, setSearchOpen] = useState(false)
  const [health, setHealth] = useState<HealthIndicator[]>(fallbackHealth)
  const [plexAvailable, setPlexAvailable] = useState(false)
  const [problemCount, setProblemCount] = useState(0)
  const [reviewCount, setReviewCount] = useState(0)
  const reviewCountGeneration = useRef(0)
  const [pageTopbarHost, setPageTopbarHost] = useState<HTMLDivElement | null>(null)
  const location = useLocation()
  const navigate = useNavigate()

  // Job events, explicit Review changes, route changes and the fallback poll
  // can overlap. Only the newest request may update the badge, otherwise a
  // slower pre-import response can restore a stale non-zero count.
  const refreshReviewCount = useCallback(async () => {
    const generation = ++reviewCountGeneration.current
    try {
      const count = await api.discoveryCount()
      if (generation === reviewCountGeneration.current) setReviewCount(count)
    } catch {}
  }, [])

  useEffect(() => {
    void refreshReviewCount()
  }, [location.pathname, refreshReviewCount])

  useEffect(() => {
    let alive = true
    const refreshProblemCount = () => api.problemCount('open').then((count) => { if (alive) setProblemCount(count) }).catch(() => undefined)
    const refreshHealth = () => api.health().then((payload) => { if (alive && payload.indicators?.length) setHealth(payload.indicators) }).catch(() => undefined)
    const refreshPlexAvailability = () => api.plexConfiguration().then((configuration) => {
      if (alive) setPlexAvailable(configuration.configured && configuration.enabled && configuration.token_configured)
    }).catch(() => undefined)
    void refreshHealth()
    void refreshPlexAvailability()
    void refreshProblemCount()
    void refreshReviewCount()

    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const jobListener = () => void refreshReviewCount()
    // Problem SSE events are invalidation signals only. SQLite remains the
    // authoritative count, so reconnects, rollbacks and duplicate-resolution
    // events can never leave the badge drifting from the queue.
    const problemChangedListener = () => void refreshProblemCount()
    const healthListener = () => { void refreshHealth(); void refreshPlexAvailability() }
    stream.addEventListener('job.status', jobListener)
    stream.addEventListener('problem.created', problemChangedListener)
    stream.addEventListener('problem.updated', problemChangedListener)
    stream.addEventListener('problem.resolved', problemChangedListener)
    stream.addEventListener('problem.deleted', problemChangedListener)
    stream.addEventListener('plex.health', healthListener)
    stream.addEventListener('tmdb.health', healthListener)
    stream.addEventListener('indexer.health', healthListener)
    stream.addEventListener('storage_root.health', healthListener)
    stream.addEventListener('qbittorrent.health', healthListener)

    const problemTimer = window.setInterval(refreshProblemCount, 300000)
    const reviewChangedListener = () => void refreshReviewCount()
    const plexConfigurationChangedListener = () => { void refreshPlexAvailability(); void refreshHealth() }
    window.addEventListener('medialogue-review-changed', reviewChangedListener)
    window.addEventListener('medialogue-plex-configuration-changed', plexConfigurationChangedListener)
    const reviewTimer = window.setInterval(() => void refreshReviewCount(), 60000)
    const healthTimer = window.setInterval(() => { void refreshHealth(); void refreshPlexAvailability() }, 60000)
    return () => {
      alive = false
      stream.close()
      window.clearInterval(problemTimer)
      window.clearInterval(reviewTimer)
      window.removeEventListener('medialogue-review-changed', reviewChangedListener)
      window.removeEventListener('medialogue-plex-configuration-changed', plexConfigurationChangedListener)
      window.clearInterval(healthTimer)
      reviewCountGeneration.current += 1
    }
  }, [refreshReviewCount])

  const currentTitle = location.pathname.startsWith('/movies') ? 'Movies' : location.pathname.startsWith('/shows') ? 'Shows' : location.pathname.startsWith('/review') ? 'Review' : location.pathname.startsWith('/downloads') ? 'Downloads' : location.pathname.startsWith('/problems') ? 'Problems' : location.pathname.startsWith('/torrent-archive') ? 'Torrent Archive' : location.pathname.startsWith('/custom-formats') ? 'Custom Formats' : location.pathname.startsWith('/quality-profiles') ? 'Quality Profiles' : location.pathname.startsWith('/tasks') ? 'Tasks' : location.pathname.startsWith('/events') ? 'Events' : location.pathname.startsWith('/settings') ? 'Settings' : 'Medialogue'

  return <PlexAvailableContext.Provider value={plexAvailable}><PageTopbarHostContext.Provider value={pageTopbarHost}><div className="app-shell">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-identity" onClick={() => navigate('/movies')} role="button" tabIndex={0}>
          <img className="brand-banner" src={medialogueBanner} alt="Medialogue" />
        </div>
        <GlobalSearchButton onOpen={() => setSearchOpen(true)} />
      </div>
      <div className="sidebar-scroll">
        <NavSection items={primary.map((item) => item.to === '/problems' ? { ...item, count: problemCount } : item.to === '/review' ? { ...item, count: reviewCount } : item)} />
        <NavSection items={tools} />
        <NavSection items={system} />
      </div>
      <div className="sidebar-footer">
        <button className="user-row" onClick={onLogout}><span className="avatar">A</span><span className="user-copy"><strong>admin</strong><span>Administrator</span></span><Icon name="logout" size={15} /></button>
      </div>
    </aside>
    <main className="main-area">
      <header className="topbar">
        <div className="topbar-page-host" ref={setPageTopbarHost}>{!pageTopbarHost && <strong>{currentTitle}</strong>}</div>
        <div className="topbar-actions">
          <div className="health-strip">{health.filter((item) => item.name !== 'Plex' || plexAvailable).map((item) => <HealthPill key={item.name} item={item} />)}</div>
        </div>
      </header>
      <div className="page-content">{children}</div>
    </main>
    {searchOpen && <GlobalSearch onClose={() => setSearchOpen(false)} />}
  </div></PageTopbarHostContext.Provider></PlexAvailableContext.Provider>
}

function NavSection({ items }: { items: Array<{ to: string; label: string; icon: Parameters<typeof Icon>[0]['name']; count?: number }> }) {
  return <div className="nav-section">{items.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><Icon name={item.icon} size={17} /><span>{item.label}</span>{item.count ? <span className="nav-count">{item.count}</span> : null}</NavLink>)}</div>
}

function HealthPill({ item }: { item: HealthIndicator }) {
  const tone = item.state === 'healthy' ? 'green' : item.state === 'offline' ? 'red' : item.state === 'degraded' ? 'amber' : 'neutral'
  return <Tooltip asChild content={item.detail}><div className="health-pill"><span className={`health-dot ${tone}`} />{item.name}</div></Tooltip>
}
