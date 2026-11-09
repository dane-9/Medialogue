import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { discoveryApi } from '../api/modules/discovery'
import { integrationsApi } from '../api/modules/integrations'
import { problemsApi } from '../api/modules/problems'
import { systemApi } from '../api/modules/system'
import { replacementsApi } from '../api/modules/replacements'
import { upgradesApi } from '../api/modules/upgrades'
import { jobEventChangesBadges } from '../lib/badgeRefresh'
import { DATA_CHANGED_EVENT } from '../api/transport'
import medialogueBanner from '../assets/medialogue-banner.png'
import type { HealthIndicator } from '../types'
import { GlobalSearch, GlobalSearchButton } from './GlobalSearch'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'
import { useLiveRefresh } from '../lib/liveRefresh'

const problemEvents = ['problem.created', 'problem.updated', 'problem.resolved', 'problem.deleted']
const healthEvents = ['plex.health', 'tmdb.health', 'indexer.health', 'storage_root.health', 'qbittorrent.health']

const primary = [
  { to: '/movies', label: 'Movies', icon: 'clapper' as const },
  { to: '/shows', label: 'Shows', icon: 'tv-screen' as const },
  { to: '/new-media', label: 'New Media', icon: 'new-media' as const },
  { to: '/replace-media', label: 'Replace Media', icon: 'refresh' as const },
  { to: '/upgrade-media', label: 'Upgrade Media', icon: 'upgrade-media' as const },
  { to: '/downloads', label: 'Downloads', icon: 'download' as const },
  { to: '/blocklist', label: 'Blocklist', icon: 'blocklist' as const },
  { to: '/problems', label: 'Problems', icon: 'alert' as const },
]
const tools = [
  { to: '/backups', label: 'Backups', icon: 'backup' as const },
  { to: '/custom-formats', label: 'Custom Formats', icon: 'custom-format' as const },
  { to: '/quality-profiles', label: 'Quality Profiles', icon: 'quality-profile' as const },
]
const system = [
  { to: '/tasks', label: 'Tasks', icon: 'activity' as const },
  { to: '/events', label: 'Events', icon: 'clock' as const },
  { to: '/settings', label: 'Settings', icon: 'settings' as const },
]

const fallbackHealth: HealthIndicator[] = [
  { name: 'Plex', state: 'unknown', detail: 'Not configured', settingsTab: 'Plex', items: [] },
  { name: 'qBittorrent', state: 'unknown', detail: 'Not configured', settingsTab: 'qBittorrent', items: [] },
  { name: 'Media Folders', state: 'unknown', detail: 'No media folders configured', settingsTab: 'Media', items: [] },
  { name: 'Indexers', state: 'unknown', detail: 'Not configured', settingsTab: 'Indexers', items: [] },
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
  const [newMediaCount, setNewMediaCount] = useState(0)
  const [upgradeCount, setUpgradeCount] = useState(0)
  const [replaceCount, setReplaceCount] = useState(0)
  const newMediaCountGeneration = useRef(0)
  const [pageTopbarHost, setPageTopbarHost] = useState<HTMLDivElement | null>(null)
  const location = useLocation()
  const navigate = useNavigate()

  // Job events, explicit New Media changes, route changes and the fallback poll
  // can overlap. Only the newest request may update the badge, otherwise a
  // slower pre-add response can restore a stale non-zero count.
  const refreshNewMediaCount = useCallback(async () => {
    const generation = ++newMediaCountGeneration.current
    try {
      const count = await discoveryApi.discoveryCount()
      if (generation === newMediaCountGeneration.current) setNewMediaCount(count)
    } catch {}
  }, [])

  useEffect(() => {
    void refreshNewMediaCount()
  }, [location.pathname, refreshNewMediaCount])

  const refreshProblemCount = () => problemsApi.problemCount('open').then((count) => setProblemCount(count)).catch(() => undefined)
  const refreshUpgradeCount = () => upgradesApi.upgradeCandidateCount().then((value) => setUpgradeCount(value.count)).catch(() => undefined)
  // Replace Media items that wait on the user: lost sources to search or
  // choose for, and finished replacements awaiting a cleanup decision.
  const refreshReplaceCount = () => replacementsApi.count().then((value) => setReplaceCount(value)).catch(() => undefined)
  const refreshHealth = () => systemApi.health().then((payload) => { if (payload.indicators?.length) setHealth(payload.indicators) }).catch(() => undefined)
  const refreshPlexAvailability = () => integrationsApi.plexConfiguration().then((configuration) => {
    setPlexAvailable(configuration.configured && configuration.enabled && configuration.token_configured)
  }).catch(() => undefined)
  const refreshHealthAndPlex = () => { void refreshHealth(); void refreshPlexAvailability() }
  // Every job reports on job.status, the health sweep included, and the
  // Upgrade and Replace Media counts are not cheap: only a finished job that
  // can change them refreshes the badges.
  const badgeJob = (event: MessageEvent<string>, name: string) => name !== 'job.status' || jobEventChangesBadges(event.data)

  // Problem events are invalidation signals only. SQLite remains the
  // authoritative count, so reconnects, rollbacks and duplicate-resolution
  // events can never leave the badge drifting from the queue.
  useLiveRefresh(refreshProblemCount, { events: problemEvents, pollMs: 300_000, immediate: true })
  // Upgrade searches report each candidate as found, and scans each folder as
  // read, so the counts follow along; coalescing keeps that to one query every
  // few seconds however busy the stream is.
  useLiveRefresh(refreshUpgradeCount, {
    events: ['job.status', 'upgrade.candidate', 'upgrade.execution', 'upgrade.submitted', 'upgrade.result_selected'],
    when: badgeJob, coalesceMs: 2_000, pollMs: 300_000, immediate: true,
  })
  useLiveRefresh(refreshReplaceCount, {
    events: ['job.status', 'upgrade.candidate', 'upgrade.execution', 'torrent.tracker_unregistered', 'torrent.tracker_recovered', 'replacement.finished'],
    when: badgeJob, coalesceMs: 2_000, pollMs: 300_000, immediate: true,
  })
  useLiveRefresh(refreshNewMediaCount, {
    events: ['job.status', 'scan.progress', 'show.discovered', 'movie.added', 'show.added'],
    when: badgeJob, coalesceMs: 2_000, pollMs: 60_000,
  })

  // Anything changed from this browser (adding New Media, grabbing an upgrade,
  // removing a media folder and its Problems, saving an integration…)
  // refreshes every badge and health dot, once the burst settles.
  useEffect(() => {
    let timer: number | undefined
    const changed = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        void refreshNewMediaCount(); void refreshUpgradeCount(); void refreshReplaceCount()
        void refreshProblemCount(); refreshHealthAndPlex()
      }, 500)
    }
    window.addEventListener(DATA_CHANGED_EVENT, changed)
    return () => { window.clearTimeout(timer); window.removeEventListener(DATA_CHANGED_EVENT, changed) }
  }, [refreshNewMediaCount])
  useLiveRefresh(refreshHealthAndPlex, { events: healthEvents, pollMs: 60_000, immediate: true })

  useEffect(() => {
    const newMediaChangedListener = () => void refreshNewMediaCount()
    const plexConfigurationChangedListener = () => { void refreshPlexAvailability(); void refreshHealth() }
    window.addEventListener('medialogue-new-media-changed', newMediaChangedListener)
    window.addEventListener('medialogue-plex-configuration-changed', plexConfigurationChangedListener)
    return () => {
      window.removeEventListener('medialogue-new-media-changed', newMediaChangedListener)
      window.removeEventListener('medialogue-plex-configuration-changed', plexConfigurationChangedListener)
      newMediaCountGeneration.current += 1
    }
  }, [refreshNewMediaCount])

  const currentTitle = location.pathname.startsWith('/movies') ? 'Movies' : location.pathname.startsWith('/shows') ? 'Shows' : location.pathname.startsWith('/new-media') ? 'New Media' : location.pathname.startsWith('/replace-media') ? 'Replace Media' : location.pathname.startsWith('/upgrade-media') ? 'Upgrade Media' : location.pathname.startsWith('/downloads') ? 'Downloads' : location.pathname.startsWith('/blocklist') ? 'Blocklist' : location.pathname.startsWith('/problems') ? 'Problems' : location.pathname.startsWith('/backups') ? 'Backups' : location.pathname.startsWith('/custom-formats') ? 'Custom Formats' : location.pathname.startsWith('/quality-profiles') ? 'Quality Profiles' : location.pathname.startsWith('/tasks') ? 'Tasks' : location.pathname.startsWith('/events') ? 'Events' : location.pathname.startsWith('/settings') ? 'Settings' : 'Medialogue'

  return <PlexAvailableContext.Provider value={plexAvailable}><PageTopbarHostContext.Provider value={pageTopbarHost}><div className="app-shell">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-identity" onClick={() => navigate('/movies')} role="button" tabIndex={0}>
          <img className="brand-banner" src={medialogueBanner} alt="Medialogue" />
        </div>
        <GlobalSearchButton onOpen={() => setSearchOpen(true)} />
      </div>
      <div className="sidebar-scroll">
        <NavSection items={primary.map((item) => item.to === '/problems' ? { ...item, count: problemCount, alert: true } : item.to === '/new-media' ? { ...item, count: newMediaCount } : item.to === '/upgrade-media' ? { ...item, count: upgradeCount, tone: 'ok' as const } : item.to === '/replace-media' ? { ...item, count: replaceCount } : item)} />
        <NavSection items={tools} />
        <NavSection items={system} />
      </div>
      <div className="sidebar-footer">
        <div className="sidebar-health" aria-label="Integration status">
          {health.filter((item) => item.name !== 'Plex' || plexAvailable).map((item) => <HealthLink key={item.name} item={item} />)}
        </div>
        <button className="user-row" onClick={onLogout}><span className="avatar">A</span><span className="user-copy"><strong>admin</strong><span>Administrator</span></span><Icon name="logout" size={15} /></button>
      </div>
    </aside>
    <main className="main-area">
      <header className="topbar">
        <div className="topbar-page-host" ref={setPageTopbarHost}>{!pageTopbarHost && <strong>{currentTitle}</strong>}</div>
      </header>
      <div className="page-scroll"><div className="page-content">{children}</div></div>
    </main>
    {searchOpen && <GlobalSearch onClose={() => setSearchOpen(false)} />}
  </div></PageTopbarHostContext.Provider></PlexAvailableContext.Provider>
}

function NavSection({ items }: { items: Array<{ to: string; label: string; icon: Parameters<typeof Icon>[0]['name']; count?: number; alert?: boolean; tone?: 'ok' }> }) {
  return <div className="nav-section">{items.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><Icon name={item.icon} size={17} /><span>{item.label}</span>{item.count ? <span className={`nav-count ${item.alert ? 'nav-count-alert' : ''} ${item.tone === 'ok' ? 'nav-count-ok' : ''}`}>{item.count}</span> : null}</NavLink>)}</div>
}

function HealthLink({ item }: { item: HealthIndicator }) {
  const tone = item.state === 'healthy' ? 'green' : item.state === 'offline' ? 'red' : item.state === 'degraded' ? 'amber' : 'neutral'
  const content = <div className="integration-tooltip">
    {item.items.length
      ? <div className="integration-tooltip-list">{item.items.map((entry) => {
        const entryTone = entry.state === 'healthy' ? 'green' : entry.state === 'offline' ? 'red' : entry.state === 'degraded' ? 'amber' : 'neutral'
        return <div className="integration-tooltip-row" key={entry.id}>
          <span className={`health-dot ${entryTone}`} />
          <span><strong>{entry.name}</strong>{entry.facts?.length
            ? <small className="integration-tooltip-facts">{entry.facts.map((fact, index) => <Tooltip key={`${fact.value}-${index}`} content={fact.help} delay={250}><span className="integration-tooltip-fact">{fact.value}</span></Tooltip>)}</small>
            : entry.detail && <small>{entry.detail}</small>}</span>
        </div>
      })}</div>
      : <div className="integration-tooltip-empty">Nothing configured</div>}
    <Link className="integration-tooltip-hint" to={`/settings?tab=${encodeURIComponent(item.settingsTab)}`}>Open {item.settingsTab} settings</Link>
  </div>
  return <Tooltip asChild interactive placement="right" delay={250} content={content}>
    <Link className="sidebar-health-link" to={`/settings?tab=${encodeURIComponent(item.settingsTab)}`} aria-label={`${item.name}: ${item.detail}. Open settings.`}>
      <span className={`health-dot ${tone}`} />
      <span>{item.name}</span>
      <Icon name="chevron" size={13} />
    </Link>
  </Tooltip>
}
