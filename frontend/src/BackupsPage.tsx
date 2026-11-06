import { useEffect, useRef, useState } from 'react'
import { backupsApi } from './api/modules/backups'
import type { BackupCoverage, BackupStatus, CoverageStatus, DatabaseCopy } from './api/modules/backups'
import { systemApi } from './api/modules/system'
import { Badge, Button, Panel, Progress, Segmented, Select } from './components/ui'
import { useUrlState } from './lib/urlState'
import { Note, SectionHead, failed } from './components/settings'
import type { Message } from './components/settings'
import { PageTopbar } from './components/Shell'
import { formatAge } from './lib/age'
import { formatBytes } from './components/ReleaseResults'
import { RecoveryReimportSection, RowPoster, TorrentArchiveSection } from './pages'
import type { Job } from './types'

const DATABASE_INTERVALS = [
  { value: 43200, label: 'Every 12 hours' },
  { value: 86400, label: 'Daily' },
  { value: 259200, label: 'Every 3 days' },
  { value: 604800, label: 'Weekly' },
]

const COVERAGE_LABEL: Record<CoverageStatus, string> = {
  protected: 'On Google Drive',
  local_only: 'Only on this server',
  not_archived: 'Torrent not archived',
  no_torrent: 'No torrent',
}
const COVERAGE_HELP: Record<Exclude<CoverageStatus, 'protected'>, string> = {
  local_only: 'Archived here, waiting for the next copy to Google Drive.',
  not_archived: 'Its torrent could not be saved yet. Retry it under Torrents below.',
  no_torrent: 'Medialogue never saw a torrent for it, so it cannot be downloaded again.',
}

/** Job progress while it runs, refreshed until it finishes. */
function useJob(id: string | null | undefined, onDone?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null)
  const done = useRef(onDone)
  done.current = onDone
  useEffect(() => {
    if (!id) return
    let alive = true
    let finished = false
    const tick = async () => {
      try {
        const value = await systemApi.job(id)
        if (!alive) return
        setJob(value)
        if (!finished && !['queued', 'running'].includes(value.state)) { finished = true; done.current?.(value) }
      } catch { /* the next tick retries */ }
    }
    void tick()
    const timer = window.setInterval(() => { if (!finished) void tick() }, 1500)
    return () => { alive = false; window.clearInterval(timer) }
  }, [id])
  return job
}

const TABS = [
  { value: 'torrents', label: 'Torrents' },
  { value: 'drive', label: 'Google Drive' },
  { value: 'coverage', label: 'Coverage' },
  { value: 'restore', label: 'Restore' },
] as const
type Tab = typeof TABS[number]['value']

export default function BackupsPage() {
  const [tabParam, setTab] = useUrlState('tab', 'torrents')
  const tab: Tab = TABS.some((item) => item.value === tabParam) ? tabParam as Tab : 'torrents'
  const [status, setStatus] = useState<BackupStatus | null>(null)
  const [coverage, setCoverage] = useState<BackupCoverage | null>(null)
  const [message, setMessage] = useState<Message>(null)
  const [busy, setBusy] = useState(false)
  const [backupJobId, setBackupJobId] = useState<string | null>(null)
  const [restoreJobId, setRestoreJobId] = useState<string | null>(null)
  const [restored, setRestored] = useState(0)
  const [showAllGaps, setShowAllGaps] = useState(false)
  const [copies, setCopies] = useState<DatabaseCopy[] | null>(null)

  const load = async () => {
    try {
      const [nextStatus, nextCoverage] = await Promise.all([backupsApi.status(), backupsApi.coverage()])
      setStatus(nextStatus)
      setCoverage(nextCoverage)
      if (nextStatus.active_job_id) setBackupJobId(nextStatus.active_job_id)
      if (nextStatus.active_restore_job_id) setRestoreJobId(nextStatus.active_restore_job_id)
    } catch (reason) { setMessage(failed(reason, 'Could not load backup status.')) }
  }
  useEffect(() => { void load() }, [])
  // Scheduled copies run every few minutes; keep the status line current.
  useEffect(() => { const timer = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(timer) }, [])

  const backupJob = useJob(backupJobId, () => void load())
  const restoreJob = useJob(restoreJobId, (job) => { void load(); if (job.state === 'completed') setRestored((value) => value + 1) })
  const backingUp = backupJob ? ['queued', 'running'].includes(backupJob.state) : false
  const restoring = restoreJob ? ['queued', 'running'].includes(restoreJob.state) : false

  const act = async (action: () => Promise<void>) => {
    setBusy(true); setMessage(null)
    try { await action(); await load() } catch (reason) { setMessage(failed(reason, 'That did not work.')) }
    finally { setBusy(false) }
  }
  const save = (changes: Partial<{ enabled: boolean; keep_database: number; database_interval_seconds: number }>) => act(async () => {
    if (!status) return
    await backupsApi.saveSettings({ enabled: status.enabled, keep_database: status.keep_database, database_interval_seconds: status.database_interval_seconds, ...changes })
  })

  const connected = Boolean(status?.connected)
  const tone = !status ? 'neutral' : !connected ? 'neutral' : status.last_error ? 'red' : status.enabled ? 'green' : 'amber'
  const label = !status ? 'Checking' : !connected ? 'Not connected' : status.last_error ? 'Needs attention' : status.enabled ? 'Backing up' : 'Paused'
  const gaps = coverage?.gaps ?? []
  const shownGaps = showAllGaps ? gaps : gaps.slice(0, 8)

  return <div className="page backups-page">
    <PageTopbar title="Backups" subtitle="Keeps every torrent in Google Drive, so the whole library can download again onto a new server." />
    <div className="backups-tabs"><Segmented ariaLabel="Backups section" value={tab} options={[...TABS]} onChange={(value) => setTab(value)} /></div>

    {tab === 'drive' && 
<Panel title="Google Drive" action={<Badge tone={tone}>{label}</Badge>}><div className="backups-body">
      {status && !status.oauth_configured && <Note message={{ tone: 'error', text: 'Google Drive is not set up on this server. Create a Google OAuth web application with the Drive API enabled, set MEDIALOGUE_GOOGLE_DRIVE_CLIENT_ID, MEDIALOGUE_GOOGLE_DRIVE_CLIENT_SECRET and MEDIALOGUE_GOOGLE_DRIVE_REDIRECT_URI (this address followed by /api/v1/backups/google/callback), then restart.' }} />}
      {connected && status && <div className="backups-status">
        <div><strong>{status.torrents_on_drive.toLocaleString()}</strong><span>torrents on Drive</span></div>
        <div><strong>{status.last_sync_at ? formatAge(status.last_sync_at) : 'Never'}</strong><span>last torrent copy</span></div>
        <div><strong>{status.last_database_at ? formatAge(status.last_database_at) : 'Never'}</strong><span>last database copy</span></div>
        <div><strong>{status.enabled ? `Every ${Math.round(status.sync_interval_seconds / 60)} min` : 'Off'}</strong><span>torrent copies</span></div>
      </div>}
      {status?.last_error && <Note message={{ tone: 'error', text: `Last copy failed: ${status.last_error}` }} />}
      {backupJob && <div className="job-progress-line"><Progress value={backupJob.progress ?? 0} tone={backingUp ? 'blue' : backupJob.state === 'completed' ? 'green' : 'amber'} /><span>{backingUp ? `${backupJob.progress ?? 0}%` : backupJob.error || (typeof backupJob.summary?.message === 'string' ? backupJob.summary.message : backupJob.state)}</span></div>}

      <div className="button-row">
        {connected && <Button variant={status?.enabled ? 'secondary' : 'primary'} disabled={busy} onClick={() => void save({ enabled: !status?.enabled })}>{status?.enabled ? 'Pause backups' : 'Turn on backups'}</Button>}
        {connected && <Button variant="secondary" icon="refresh" disabled={busy || backingUp || restoring} onClick={() => void act(async () => {
          const accepted = await backupsApi.run() as { job_id: string }
          setBackupJobId(accepted.job_id)
        })}>{backingUp ? 'Backing up…' : 'Back up now'}</Button>}
        {status?.folder_url && <a className="button button-ghost" href={status.folder_url} target="_blank" rel="noreferrer">Open in Google Drive</a>}
        <span className="modal-foot-spacer" />
        <Button variant={connected ? 'ghost' : 'primary'} disabled={busy || backingUp || restoring || !status?.oauth_configured} onClick={() => void act(async () => {
          window.location.assign((await backupsApi.connect()).authorization_url)
        })}>{connected ? 'Reconnect' : 'Connect Google Drive'}</Button>
        {connected && <Button variant="ghost" disabled={busy || backingUp || restoring} onClick={() => void act(async () => { await backupsApi.disconnect(); setMessage({ tone: 'ok', text: 'Disconnected. Everything already in Drive stays there.' }) })}>Disconnect</Button>}
      </div>

      {connected && status && <div className="backups-database">
        <SectionHead title="Database" description="The library database and settings, copied separately from the torrents. Older copies are moved to Drive's trash." />
        <label>Copy<Select aria-label="Database copy interval" value={String(status.database_interval_seconds)} onChange={(event) => void save({ database_interval_seconds: Number(event.target.value) })}>
          {DATABASE_INTERVALS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </Select></label>
        <label>Keep<Select aria-label="Database copies to keep" value={String(status.keep_database)} onChange={(event) => void save({ keep_database: Number(event.target.value) })}>
          {[3, 7, 14, 30].map((value) => <option key={value} value={value}>{value} newest</option>)}
        </Select></label>
        <Button variant="ghost" disabled={busy} onClick={() => void act(async () => setCopies(await backupsApi.databaseCopies()))}>Show copies</Button>
      </div>}
      {copies && <ul className="backups-copies">{copies.length ? copies.map((copy) => <li key={copy.id}>
        <a href={copy.url} target="_blank" rel="noreferrer">{copy.name}</a>
        <span>{copy.created_at ? formatAge(copy.created_at) : ''}{copy.size ? ` · ${formatBytes(copy.size)}` : ''}</span>
      </li>) : <li>No database copies yet.</li>}</ul>}
      <p className="backups-note">Files in Drive are not encrypted. Torrent files contain your tracker passkeys, so keep the folder private.</p>
      <Note message={message} />
    </div></Panel>}

    {tab === 'coverage' && <Panel title="Coverage" action={coverage && <Badge tone={coverage.protected === coverage.total ? 'green' : 'amber'}>{coverage.protected.toLocaleString()} / {coverage.total.toLocaleString()} on Drive</Badge>}><div className="backups-body">
      {!coverage ? <p className="backups-note">Checking…</p> : <>
        <p className="backups-note">{coverage.protected === coverage.total
          ? 'Every release in the library can be downloaded again from Google Drive.'
          : `${(coverage.total - coverage.protected).toLocaleString()} of ${coverage.total.toLocaleString()} releases could not be downloaded again yet.`}</p>
        {gaps.length > 0 && <>
          <div className="button-row">
            {(['local_only', 'not_archived', 'no_torrent'] as const).filter((key) => coverage[key]).map((key) => <Badge key={key} tone={key === 'local_only' ? 'amber' : 'red'}>{coverage[key]} {COVERAGE_LABEL[key].toLowerCase()}</Badge>)}
          </div>
          <ul className="backups-legend">{(['local_only', 'not_archived', 'no_torrent'] as const).filter((key) => coverage[key]).map((key) => <li key={key}><strong>{COVERAGE_LABEL[key]}:</strong> {COVERAGE_HELP[key]}</li>)}</ul>
          <table className="data-table backups-gaps"><thead><tr><th>Title</th><th>Release</th><th>Status</th></tr></thead><tbody>{shownGaps.map((gap) => <tr key={gap.id}>
            <td><div className="row-with-poster"><RowPoster reference={gap.poster_ref} mediaType={gap.media_type} /><div><strong>{gap.title}</strong><span className="table-sub">{[gap.year, gap.season_number != null ? `Season ${gap.season_number}` : null].filter(Boolean).join(' · ')}</span></div></div></td>
            <td className="mono">{gap.release_name}</td>
            <td><Badge tone={gap.status === 'local_only' ? 'amber' : 'red'}>{COVERAGE_LABEL[gap.status]}</Badge></td>
          </tr>)}</tbody></table>
          {gaps.length > shownGaps.length && <Button variant="ghost" onClick={() => setShowAllGaps(true)}>Show all {gaps.length}{coverage.gaps_truncated ? '+' : ''}</Button>}
        </>}
      </>}
    </div></Panel>}

    {tab === 'restore' && <Panel title="Restore"><div className="backups-body">
      <ol className="backups-steps">
        <li>Set up Medialogue again: media folders and qBittorrent clients, as before.</li>
        <li>Connect the same Google account and fetch the torrents.</li>
        <li>Check the plan and re-add them. Each goes back to its original folder and downloads again.</li>
      </ol>
      <div className="button-row">
        <Button variant="secondary" icon="download" disabled={!connected || busy || restoring || backingUp} onClick={() => void act(async () => {
          const accepted = await backupsApi.restore() as { job_id: string }
          setRestoreJobId(accepted.job_id)
        })}>{restoring ? 'Fetching…' : 'Fetch torrents from Google Drive'}</Button>
      </div>
      {restoreJob && <div className="job-progress-line"><Progress value={restoreJob.progress ?? 0} tone={restoring ? 'blue' : restoreJob.state === 'completed' ? 'green' : 'amber'} /><span>{restoring ? (restoreJob.detail || `${restoreJob.progress ?? 0}%`) : restoreJob.error || (typeof restoreJob.summary?.message === 'string' ? restoreJob.summary.message : restoreJob.state)}</span></div>}
      <RecoveryReimportSection refreshKey={restored} />
    </div></Panel>}

    {tab === 'torrents' && <TorrentArchiveSection />}
  </div>
}
