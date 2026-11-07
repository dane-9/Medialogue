import { useEffect, useRef, useState } from 'react'
import { backupsApi } from './api/modules/backups'
import type { BackupRetention, BackupStatus, DatabaseCopy } from './api/modules/backups'
import { systemApi } from './api/modules/system'
import { Badge, Button, Panel, Progress, Segmented, Select } from './components/ui'
import { useUrlState } from './lib/urlState'
import { Note, SectionHead, failed } from './components/settings'
import type { Message } from './components/settings'
import { PageTopbar } from './components/Shell'
import { formatAge } from './lib/age'
import { formatBytes } from './components/ReleaseResults'
import { RecoveryReimportSection, TorrentArchiveSection } from './pages/backups'
import type { Job } from './types'
import { useLiveRefresh } from './lib/liveRefresh'

const DATABASE_INTERVALS = [
  { value: 43200, label: 'Every 12 hours' },
  { value: 86400, label: 'Daily' },
  { value: 259200, label: 'Every 3 days' },
  { value: 604800, label: 'Weekly' },
]

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
  { value: 'restore', label: 'Restore' },
] as const
type Tab = typeof TABS[number]['value']

/** The retention rules, as the Google Drive tab offers them. */
const RETENTION: { key: keyof BackupRetention; label: string; aria: string; options: number[]; option: (value: number) => string }[] = [
  { key: 'keep_daily', label: 'Daily', aria: 'Daily copies to keep', options: [0, 3, 7, 14, 30], option: (value) => `Last ${value} days` },
  { key: 'keep_weekly', label: 'Weekly', aria: 'Weekly copies to keep', options: [0, 2, 4, 8, 12], option: (value) => `Last ${value} weeks` },
  { key: 'keep_monthly', label: 'Monthly', aria: 'Monthly copies to keep', options: [0, 3, 6, 12, 24], option: (value) => `Last ${value} months` },
  { key: 'keep_recent', label: 'Also keep', aria: 'Newest copies always kept', options: [0, 3, 6, 12, 24], option: (value) => `${value} newest` },
]

/** Why a database copy is still on Drive, in words. */
export function copyKeptBy(rules?: string[]): string {
  if (!rules?.length) return 'trashed after the next copy'
  const words: Record<string, string> = { newest: 'newest', recent: 'among the newest', daily: 'daily', weekly: 'weekly', monthly: 'monthly', undated: 'date unknown' }
  return `kept: ${rules.map((rule) => words[rule] ?? rule).join(', ')}`
}

export default function BackupsPage() {
  const [tabParam, setTab] = useUrlState('tab', 'torrents')
  const tab: Tab = TABS.some((item) => item.value === tabParam) ? tabParam as Tab : 'torrents'
  const [status, setStatus] = useState<BackupStatus | null>(null)
  const [message, setMessage] = useState<Message>(null)
  const [busy, setBusy] = useState(false)
  const [backupJobId, setBackupJobId] = useState<string | null>(null)
  const [restored, setRestored] = useState(0)
  const [uploadMessage, setUploadMessage] = useState<Message>(null)
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const [copies, setCopies] = useState<DatabaseCopy[] | null>(null)

  const load = async () => {
    try {
      const nextStatus = await backupsApi.status()
      setStatus(nextStatus)
      if (nextStatus.active_job_id) setBackupJobId(nextStatus.active_job_id)
    } catch (reason) { setMessage(failed(reason, 'Could not load backup status.')) }
  }
  // Scheduled copies run every few minutes; keep the status line current.
  useLiveRefresh(load, { pollMs: 30_000, immediate: true })

  const backupJob = useJob(backupJobId, () => void load())
  const backingUp = backupJob ? ['queued', 'running'].includes(backupJob.state) : false

  const act = async (action: () => Promise<void>) => {
    setBusy(true); setMessage(null)
    try { await action(); await load() } catch (reason) { setMessage(failed(reason, 'That did not work.')) }
    finally { setBusy(false) }
  }
  const save = (changes: Partial<{ enabled: boolean; database_interval_seconds: number } & BackupRetention>) => act(async () => {
    if (!status) return
    await backupsApi.saveSettings({
      enabled: status.enabled, database_interval_seconds: status.database_interval_seconds,
      keep_recent: status.keep_recent, keep_daily: status.keep_daily, keep_weekly: status.keep_weekly, keep_monthly: status.keep_monthly,
      ...changes,
    })
  })

  const connected = Boolean(status?.connected)
  const tone = !status ? 'neutral' : !connected ? 'neutral' : status.last_error ? 'red' : status.enabled ? 'green' : 'amber'
  const label = !status ? 'Checking' : !connected ? 'Not connected' : status.last_error ? 'Needs attention' : status.enabled ? 'Backing up' : 'Paused'

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
        {connected && <Button variant="secondary" icon="refresh" disabled={busy || backingUp} onClick={() => void act(async () => {
          const accepted = await backupsApi.run() as { job_id: string }
          setBackupJobId(accepted.job_id)
        })}>{backingUp ? 'Backing up…' : 'Back up now'}</Button>}
        {status?.folder_url && <a className="button button-ghost" href={status.folder_url} target="_blank" rel="noreferrer">Open in Google Drive</a>}
        <span className="modal-foot-spacer" />
        <Button variant={connected ? 'ghost' : 'primary'} disabled={busy || backingUp || !status?.oauth_configured} onClick={() => void act(async () => {
          window.location.assign((await backupsApi.connect()).authorization_url)
        })}>{connected ? 'Reconnect' : 'Connect Google Drive'}</Button>
        {connected && <Button variant="ghost" disabled={busy || backingUp} onClick={() => void act(async () => { await backupsApi.disconnect(); setMessage({ tone: 'ok', text: 'Disconnected. Everything already in Drive stays there.' }) })}>Disconnect</Button>}
      </div>

      {connected && status && <div className="backups-database">
        <SectionHead title="Database" description="The library database and settings, copied separately from the torrents. Every copy from the last few days is kept, then one a week, then one a month; the rest go to Drive's trash. Torrents a kept copy lists stay on Drive." />
        <label>Copy<Select aria-label="Database copy interval" value={String(status.database_interval_seconds)} onChange={(event) => void save({ database_interval_seconds: Number(event.target.value) })}>
          {DATABASE_INTERVALS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </Select></label>
        {RETENTION.map((rule) => <label key={rule.key}>{rule.label}<Select aria-label={rule.aria} value={String(status[rule.key])} onChange={(event) => void save({ [rule.key]: Number(event.target.value) })}>
          {[...new Set([...rule.options, status[rule.key]])].sort((a, b) => a - b).map((value) => <option key={value} value={value}>{value === 0 ? 'None' : rule.option(value)}</option>)}
        </Select></label>)}
        <Button variant="ghost" disabled={busy} onClick={() => void act(async () => setCopies(await backupsApi.databaseCopies()))}>Show copies</Button>
      </div>}
      {copies && <ul className="backups-copies">{copies.length ? copies.map((copy) => <li key={copy.id}>
        <a href={copy.url} target="_blank" rel="noreferrer">{copy.name}</a>
        <span>{copy.created_at ? formatAge(copy.created_at) : ''}{copy.size ? ` · ${formatBytes(copy.size)}` : ''}{copy.torrents ? ` · ${copy.torrents.toLocaleString()} torrents` : ''} · {copyKeptBy(copy.kept_by)}</span>
      </li>) : <li>No database copies yet.</li>}</ul>}
      <p className="backups-note">Files in Drive are not encrypted. Torrent files contain your tracker passkeys, so keep the folder private.</p>
      <Note message={message} />
    </div></Panel>}

    {tab === 'restore' && <Panel title="Restore"><div className="backups-body">
      <ol className="backups-steps">
        <li>Set up Medialogue again: media folders and qBittorrent clients, as before.</li>
        <li>In Google Drive, download the "Medialogue Backups" folder. Drive gives it to you as a ZIP (several, if it is large).</li>
        <li>Upload the ZIP here, then check the plan and re-add. Each torrent goes back to its original folder and downloads again.</li>
      </ol>
      <div className="button-row">
        <input ref={fileInput} type="file" accept=".zip,application/zip" multiple hidden onChange={(event) => {
          const files = [...(event.target.files ?? [])]
          event.target.value = ''
          if (!files.length) return
          void (async () => {
            setUploading(true); setUploadMessage(null)
            let written = 0, present = 0, listed = 0
            try {
              // Drive splits a large folder into several ZIPs; each is a part of the same backup.
              for (const file of files) {
                const result = await backupsApi.uploadBackup(file)
                written += result.written; present += result.present; listed += result.listed
              }
              setUploadMessage({ tone: 'ok', text: `${written} file${written === 1 ? '' : 's'} restored${present ? `, ${present} already here` : ''}; ${listed} torrent${listed === 1 ? '' : 's'} added to the list.` })
              setRestored((value) => value + 1)
            } catch (reason) { setUploadMessage(failed(reason, 'That ZIP could not be restored.')) }
            finally { setUploading(false) }
          })()
        }} />
        <Button variant="secondary" icon="download" disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? 'Uploading…' : 'Upload backup ZIP'}</Button>
      </div>
      <Note message={uploadMessage} />
      <RecoveryReimportSection refreshKey={restored} />
    </div></Panel>}

    {tab === 'torrents' && <TorrentArchiveSection />}
  </div>
}
