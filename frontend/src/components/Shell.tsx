import { createContext, useContext, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { HealthIndicator, Job } from '../types'
import { GlobalSearch, GlobalSearchButton } from './GlobalSearch'
import { Icon } from './Icon'
import { Button, Progress } from './ui'

const primary = [
  { to: '/movies', label: 'Movies', icon: 'film' as const },
  { to: '/shows', label: 'Shows', icon: 'tv' as const },
  { to: '/downloads', label: 'Downloads', icon: 'download' as const },
  { to: '/problems', label: 'Problems', icon: 'alert' as const },
]
const tools = [
  { to: '/torrent-archive', label: 'Torrent Archive', icon: 'archive' as const },
  { to: '/custom-formats', label: 'Custom Formats', icon: 'sliders' as const },
  { to: '/quality-profiles', label: 'Quality Profiles', icon: 'spark' as const },
]
const system = [
  { to: '/events', label: 'Event History', icon: 'clock' as const },
  { to: '/settings', label: 'Settings', icon: 'settings' as const },
]

const fallbackHealth: HealthIndicator[] = [
  { name: 'Plex', state: 'unknown', detail: 'Not configured' },
  { name: 'qBittorrent', state: 'unknown', detail: 'Not configured' },
  { name: 'Storage', state: 'unknown', detail: 'No roots configured' },
  { name: 'Indexers', state: 'unknown', detail: 'Not configured' },
]

const PageTopbarHostContext = createContext<HTMLElement | null>(null)

export function PageTopbar({ title, subtitle, action, back, onBack }: { title: string; subtitle?: string; action?: React.ReactNode; back?: string; onBack?: () => void }) {
  const host = useContext(PageTopbarHostContext)
  if (!host) return null
  return createPortal(<div className="topbar-page-heading">
    {back && <button className="topbar-back" type="button" onClick={onBack} aria-label={back} title={back}><Icon name="arrow" size={15} /></button>}
    <div className="topbar-page-copy"><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
    {action && <div className="topbar-page-actions">{action}</div>}
  </div>, host)
}

export function AppShell({ children, onLogout }: { children: React.ReactNode; onLogout: () => void }) {
  const [searchOpen, setSearchOpen] = useState(false)
  const [jobsOpen, setJobsOpen] = useState(false)
  const [jobs, setJobs] = useState<Job[]>([])
  const [health, setHealth] = useState<HealthIndicator[]>(fallbackHealth)
  const [problemCount, setProblemCount] = useState(0)
  const [pageTopbarHost, setPageTopbarHost] = useState<HTMLDivElement | null>(null)
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    let alive = true
    const refreshProblemCount = () => api.problemCount('open').then((count) => { if (alive) setProblemCount(count) }).catch(() => undefined)
    const refreshHealth = () => api.health().then((payload) => { if (alive && payload.indicators?.length) setHealth(payload.indicators) }).catch(() => undefined)
    const refreshJobs = () => api.jobs().then((payload) => { if (alive) setJobs(payload) }).catch(() => undefined)
    void refreshHealth()
    void refreshJobs()
    void refreshProblemCount()

    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const jobListener = () => void refreshJobs()
    // Problem SSE events are invalidation signals only. PostgreSQL remains the
    // authoritative count, so reconnects, rollbacks and duplicate-resolution
    // events can never leave the badge drifting from the queue.
    const problemChangedListener = () => void refreshProblemCount()
    const healthListener = () => void refreshHealth()
    stream.addEventListener('job.status', jobListener)
    stream.addEventListener('problem.created', problemChangedListener)
    stream.addEventListener('problem.updated', problemChangedListener)
    stream.addEventListener('problem.resolved', problemChangedListener)
    stream.addEventListener('problem.deleted', problemChangedListener)
    stream.addEventListener('plex.health', healthListener)
    stream.addEventListener('storage_root.health', healthListener)
    stream.addEventListener('qbittorrent.health', healthListener)

    // REST remains the recovery source when SSE reconnects or a browser was
    // asleep. These slow polls are a fallback, not the primary update path.
    const problemTimer = window.setInterval(refreshProblemCount, 300000)
    const jobTimer = window.setInterval(refreshJobs, 30000)
    const healthTimer = window.setInterval(refreshHealth, 60000)
    return () => {
      alive = false
      stream.close()
      window.clearInterval(problemTimer)
      window.clearInterval(jobTimer)
      window.clearInterval(healthTimer)
    }
  }, [])

  const activeJobs = jobs.filter((job) => job.state === 'running' || job.state === 'queued').length
  const currentTitle = location.pathname.startsWith('/setup') ? 'Setup' : location.pathname.startsWith('/movies') ? 'Movies' : location.pathname.startsWith('/shows') ? 'Shows' : location.pathname.startsWith('/downloads') ? 'Downloads' : location.pathname.startsWith('/problems') ? 'Problems' : location.pathname.startsWith('/torrent-archive') ? 'Torrent Archive' : location.pathname.startsWith('/custom-formats') ? 'Custom Formats' : location.pathname.startsWith('/quality-profiles') ? 'Quality Profiles' : location.pathname.startsWith('/events') ? 'Event History' : location.pathname.startsWith('/settings') ? 'Settings' : 'Medialogue'

  return <PageTopbarHostContext.Provider value={pageTopbarHost}><div className="app-shell">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-identity" onClick={() => navigate('/movies')} role="button" tabIndex={0}>
          <div className="brand-mark"><Icon name="spark" size={18} /></div>
          <div className="brand-name">MEDIA<span>LOGUE</span></div>
        </div>
        <GlobalSearchButton onOpen={() => setSearchOpen(true)} />
      </div>
      <div className="sidebar-scroll">
        <NavSection items={primary.map((item) => item.to === '/problems' ? { ...item, count: problemCount } : item)} />
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
          <div className="health-strip">{health.map((item) => <HealthPill key={item.name} item={item} />)}</div>
          <button className="jobs-button" onClick={() => setJobsOpen(true)} aria-label="Open jobs drawer"><Icon name="activity" size={17} /><span>Jobs</span>{activeJobs > 0 && <b>{activeJobs}</b>}</button>
        </div>
      </header>
      <div className="page-content">{children}</div>
    </main>
    {searchOpen && <GlobalSearch onClose={() => setSearchOpen(false)} />}
    {jobsOpen && <JobsDrawer jobs={jobs} onClose={() => setJobsOpen(false)} onChanged={(next) => setJobs(next)} onOpenEvents={() => { setJobsOpen(false); navigate('/events') }} />}
  </div></PageTopbarHostContext.Provider>
}

// Groups are separated by space alone. With ten destinations the headings were
// labelling what the icons and the gap already make obvious.
function NavSection({ items }: { items: Array<{ to: string; label: string; icon: Parameters<typeof Icon>[0]['name']; count?: number }> }) {
  return <div className="nav-section">{items.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><Icon name={item.icon} size={17} /><span>{item.label}</span>{item.count && <span className="nav-count">{item.count}</span>}</NavLink>)}</div>
}

function HealthPill({ item }: { item: HealthIndicator }) {
  const tone = item.state === 'healthy' ? 'green' : item.state === 'offline' ? 'red' : item.state === 'degraded' ? 'amber' : 'neutral'
  return <div className="health-pill" title={item.detail}><span className={`health-dot ${tone}`} />{item.name}</div>
}

function JobsDrawer({ jobs, onClose, onChanged, onOpenEvents }: { jobs: Job[]; onClose: () => void; onChanged: (jobs: Job[]) => void; onOpenEvents: () => void }) {
  const [cancelError, setCancelError] = useState('')
  const cancel = async (job: Job) => {
    setCancelError('')
    try {
      const cancelled = await api.cancelJob(job.id)
      onChanged(jobs.map((item) => item.id === cancelled.id ? cancelled : item))
      onChanged(await api.jobs())
    } catch (reason) {
      setCancelError(reason instanceof Error ? reason.message : 'Could not cancel the job.')
    }
  }
  return <div className="drawer-backdrop" onClick={onClose}><aside className="jobs-drawer" onClick={(event) => event.stopPropagation()}>
    <div className="drawer-header"><div><div className="eyebrow">BACKGROUND ACTIVITY</div><h2>Jobs</h2></div><button className="icon-button" onClick={onClose}><Icon name="close" size={18} /></button></div>
    <div className="drawer-note"><Icon name="shield" size={16} /><span>Jobs persist across navigation and browser refresh. Interrupted work is preserved after an application restart instead of silently resuming.</span></div>
    {cancelError && <div className="drawer-note error-note"><Icon name="alert" size={16} /><span>{cancelError}</span></div>}
    <div className="job-list">{jobs.length ? jobs.map((job) => <div className="job-item" key={job.id}><div className="job-item-top"><span className={`job-state job-${job.state}`}><span />{job.state}</span><span className="muted">{job.updated}</span></div><strong>{job.title}</strong><span className="job-detail">{job.error || job.detail || 'Persisted background operation'}</span>{job.progress !== undefined && <div className="job-progress-line"><Progress value={job.progress} tone={job.state === 'completed' ? 'green' : 'blue'} /><span>{job.progress}%</span></div>}{job.cancellable && (job.state === 'running' || job.state === 'queued') && <div className="job-actions"><Button variant="ghost" onClick={() => void cancel(job)}>Cancel</Button></div>}</div>) : <div className="drawer-note"><Icon name="check" size={16} /><span>No background jobs have been recorded yet.</span></div>}</div>
    <div className="drawer-footer"><Button variant="ghost" icon="external" onClick={onOpenEvents}>Open event history</Button></div>
  </aside></div>
}
