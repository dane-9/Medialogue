import { useEffect, useRef, useState } from 'react'
import { backupsApi } from './api/modules/backups'
import type { BackupRetention, BackupStatus, DatabaseCopy, DeviceSignIn } from './api/modules/backups'
import { systemApi } from './api/modules/system'
import { Badge, Button, Input, Panel, Progress, Segmented, Select } from './components/ui'
import { useUrlState } from './lib/urlState'
import { Field, Note, Secret, SectionHead, failed } from './components/settings'
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

const CONSOLE = 'https://console.cloud.google.com'

/** The one-time Google Cloud setup, and the client ID and secret it gives. */
function GoogleClientSetup({ status, onSaved, onCancel }: { status: BackupStatus; onSaved: () => void; onCancel?: () => void }) {
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<Message>(null)
  const submit = async () => {
    setSaving(true); setMessage(null)
    try { await backupsApi.saveClient({ client_id: clientId, client_secret: secret }); onSaved() }
    catch (reason) { setMessage(failed(reason, 'Could not save the Google client.')) }
    finally { setSaving(false) }
  }
  return <div className="backups-client">
    <SectionHead title="Google client" description="Once only: Medialogue signs in to your Drive through an OAuth client in your own Google Cloud project." />
    <ol className="backups-steps">
      <li>Create a project in the <a href={`${CONSOLE}/projectcreate`} target="_blank" rel="noreferrer">Google Cloud console</a>, and enable the <a href={`${CONSOLE}/apis/library/drive.googleapis.com`} target="_blank" rel="noreferrer">Google Drive API</a> in it.</li>
      <li>In <a href={`${CONSOLE}/auth/overview`} target="_blank" rel="noreferrer">Google Auth Platform</a>, get started with any app name and your email, audience <strong>External</strong>. Then under <strong>Audience</strong>, click <strong>Publish app</strong>: while it is in Testing, Google signs Medialogue out every 7 days.</li>
      <li>Under <a href={`${CONSOLE}/auth/clients/create`} target="_blank" rel="noreferrer">Clients → Create client</a>, choose type <strong>TVs and Limited Input devices</strong>.</li>
      <li>Paste its client ID and secret here.</li>
    </ol>
    <div className="backups-client-fields">
      <Field label="Client ID"><Input value={clientId} placeholder="….apps.googleusercontent.com" autoComplete="off" spellCheck={false} onChange={(event) => setClientId(event.target.value)} /></Field>
      <Field label="Client secret"><Secret value={secret} onChange={(event) => setSecret(event.target.value)} /></Field>
    </div>
    {status.client_source === 'environment' && <p className="backups-note">This server also has a client set by environment variables. One saved here is used instead.</p>}
    <div className="button-row">
      <Button variant="primary" disabled={saving || !clientId.trim() || !secret.trim()} onClick={() => void submit()}>{saving ? 'Saving…' : 'Save client'}</Button>
      {onCancel && <Button variant="ghost" disabled={saving} onClick={onCancel}>Cancel</Button>}
    </div>
    <Note message={message} />
  </div>
}

/** The code to enter at google.com/device, polled until it is approved. */
function DeviceCode({ signIn, onConnected, onFailed, onCancel }: { signIn: DeviceSignIn; onConnected: () => void; onFailed: (reason: unknown) => void; onCancel: () => void }) {
  const done = useRef({ onConnected, onFailed })
  done.current = { onConnected, onFailed }
  useEffect(() => {
    let alive = true
    let timer = 0
    const tick = async () => {
      try {
        if (await backupsApi.checkConnect() === 'connected') { if (alive) done.current.onConnected(); return }
      } catch (reason) { if (alive) done.current.onFailed(reason); return }
      if (alive) timer = window.setTimeout(() => void tick(), signIn.interval * 1000)
    }
    timer = window.setTimeout(() => void tick(), signIn.interval * 1000)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [signIn])
  return <div className="backups-device">
    <p>On any phone or computer, open <a href={signIn.verification_url} target="_blank" rel="noreferrer">{signIn.verification_url.replace(/^https:\/\//, '')}</a>, sign in with the Google account to back up to, and enter:</p>
    <div className="backups-device-code"><code>{signIn.user_code}</code>
      <Button variant="ghost" onClick={() => void navigator.clipboard?.writeText(signIn.user_code)}>Copy</Button></div>
    <p className="backups-note">Waiting for approval… The code works until {new Date(signIn.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.</p>
    <div className="button-row"><Button variant="ghost" onClick={onCancel}>Cancel</Button></div>
  </div>
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
  const [signIn, setSignIn] = useState<DeviceSignIn | null>(null)
  const [editingClient, setEditingClient] = useState(false)

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
      {status && (!status.oauth_configured || editingClient) && <GoogleClientSetup status={status}
        onSaved={() => { setEditingClient(false); setMessage({ tone: 'ok', text: 'Google client saved. Now connect your Google Drive.' }); void load() }}
        onCancel={status.oauth_configured ? () => setEditingClient(false) : undefined} />}
      {signIn && <DeviceCode signIn={signIn}
        onConnected={() => { setSignIn(null); setMessage({ tone: 'ok', text: 'Google Drive connected. Turn on backups to start copying.' }); void load() }}
        onFailed={(reason) => { setSignIn(null); setMessage(failed(reason, 'Google sign-in failed.')) }}
        onCancel={() => setSignIn(null)} />}
      {connected && status && <div className="backups-status">
        <div><strong>{status.torrents_on_drive.toLocaleString()}</strong><span>torrents on Drive</span></div>
        <div><strong>{formatBytes(status.bytes_on_drive)}</strong><span>total size on Drive</span></div>
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
        {status?.oauth_configured && !editingClient && !signIn && <>
          <Button variant={connected ? 'ghost' : 'primary'} disabled={busy || backingUp} onClick={() => void act(async () => {
            setSignIn(await backupsApi.connect())
          })}>{connected ? 'Reconnect' : 'Connect Google Drive'}</Button>
          <Button variant="ghost" disabled={busy || backingUp} onClick={() => setEditingClient(true)}>Change client</Button>
        </>}
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
