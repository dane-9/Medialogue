import { useEffect, useState } from 'react'
import { openApiClient, openApiData } from './api/transport'
import { Badge, Button, Input, Panel, Select } from './components/ui'

type Status = {
  oauth_configured: boolean; connected: boolean; key_configured: boolean;
  torrent_count: number; keep_daily: number; keep_weekly: number; enabled: boolean; interval_seconds: number;
  last_success_at?: string; last_error?: string; next_run_at?: string; active_job_id?: string;
}
type Snapshot = { id: string; name: string; created_at: string; verified: boolean; download_url: string }

export default function BackupDestinations() {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [key, setKey] = useState('')
  const [keySaved, setKeySaved] = useState(false)
  const [daily, setDaily] = useState(7)
  const [weekly, setWeekly] = useState(4)
  const [interval, setIntervalValue] = useState(86400)
  const [enabled, setEnabled] = useState(false)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const load = async (initialize = false) => {
    const value = await openApiData(openApiClient.GET('/api/v1/backups/status')) as unknown as Status
    setStatus(value)
    if (initialize) { setDaily(value.keep_daily); setWeekly(value.keep_weekly); setIntervalValue(value.interval_seconds); setEnabled(value.enabled) }
  }
  useEffect(() => { void load(true).catch(e => setError(String(e))) }, [])
  useEffect(() => {
    if (!status?.active_job_id) return
    const timer = window.setInterval(() => { void load().catch(e => setError(String(e))) }, 2000)
    return () => window.clearInterval(timer)
  }, [status?.active_job_id])
  const act = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setMessage('')
    try { await action(); await load() } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const save = () => act(async () => {
    await openApiData(openApiClient.PUT('/api/v1/backups/settings', { body: {
      keep_daily: daily, keep_weekly: weekly, interval_seconds: interval, enabled,
      recovery_key: key || undefined,
    } }))
    setKey(''); setKeySaved(false); setMessage('Backup settings saved.')
  })
  return <Panel title="Backup & Restore" eyebrow="GOOGLE DRIVE">
    <p>Save encrypted snapshots of Medialogue, its configuration, and the torrent archive. Downloaded movies and episodes are not included.</p>
    {error && <p role="alert" className="error-text">{error}</p>}
    {message && <p role="status">{message}</p>}
    <div className="settings-section">
      <div><h3>Google Drive</h3><p>{status?.last_success_at ? `Last verified backup: ${new Date(status.last_success_at).toLocaleString()}` : 'No verified remote backup yet.'}</p>
        {status?.last_success_at && <p>{status.torrent_count} torrent recovery records included.</p>}
        {status?.last_error && <p role="alert">Last backup failed: {status.last_error}</p>}
        {status?.next_run_at && <p>Next backup: {new Date(status.next_run_at).toLocaleString()}</p>}
      </div>
      <Badge tone={status?.connected ? 'green' : 'neutral'}>{status?.connected ? 'Connected' : 'Not connected'}</Badge>
      <Button disabled={busy || !!status?.active_job_id || !status?.oauth_configured} onClick={() => void act(async () => {
        const result = await openApiData(openApiClient.POST('/api/v1/backups/google/connect')) as { authorization_url: string }
        window.location.assign(result.authorization_url)
      })}>{status?.connected ? 'Reconnect Google Drive' : 'Connect Google Drive'}</Button>
      {status?.connected && <Button disabled={busy || !!status.active_job_id} onClick={() => void act(async () => { await openApiData(openApiClient.POST('/api/v1/backups/google/disconnect')); setEnabled(false); setSnapshots([]); setMessage('Disconnected. Scheduled backups are stopped; existing snapshots remain in Google Drive.') })}>Disconnect</Button>}
    </div>
    {status && !status.oauth_configured && <details><summary>Set up Google Drive connection</summary>
      <p>Create a Google OAuth Web application with the Drive API enabled. Configure MEDIALOGUE_GOOGLE_DRIVE_CLIENT_ID, MEDIALOGUE_GOOGLE_DRIVE_CLIENT_SECRET, and MEDIALOGUE_GOOGLE_DRIVE_REDIRECT_URI on the server, then restart.</p>
      <p>The redirect URI must match your application address followed by /api/v1/backups/google/callback. Use a Google-approved HTTPS address, or localhost for local development.</p>
    </details>}
    {!status?.key_configured && <div className="settings-section"><div><h3>Recovery key</h3><p>Save this key outside Medialogue. You need it to open backups after losing this server. It will not be shown again after saving.</p>
      <Button disabled={busy} onClick={() => void act(async () => {
        const result = await openApiData(openApiClient.POST('/api/v1/backups/recovery-key')) as { recovery_key: string }
        setKey(result.recovery_key); setKeySaved(false)
      })}>Generate recovery key</Button>
      <Input aria-label="Recovery key" value={key} onChange={e => { setKey(e.target.value); setKeySaved(false) }} autoComplete="off" spellCheck={false} />
      {key && <label><input type="checkbox" checked={keySaved} onChange={e => setKeySaved(e.target.checked)} /> I saved this recovery key outside Medialogue.</label>}
    </div></div>}
    <div className="settings-form">
      <label>Schedule<Select value={enabled ? String(interval) : 'manual'} onChange={e => { setEnabled(e.target.value !== 'manual'); if (e.target.value !== 'manual') setIntervalValue(Number(e.target.value)) }}>
        <option value="manual">Manual only</option><option value="3600">Hourly</option><option value="21600">Every 6 hours</option><option value="43200">Every 12 hours</option><option value="86400">Daily</option><option value="604800">Weekly</option>
      </Select></label>
      <label>Daily restore points<Input type="number" min={1} max={365} value={daily} onChange={e => setDaily(Number(e.target.value))} /></label>
      <label>Weekly restore points<Input type="number" min={0} max={104} value={weekly} onChange={e => setWeekly(Number(e.target.value))} /></label>
    </div>
    <p>Older snapshots are moved to Drive trash only after a new upload is verified. Daily and weekly retention can share the same snapshot.</p>
    <div className="button-row">
      <Button disabled={busy || (!!key && !keySaved)} onClick={() => void save()}>Save backup settings</Button>
      <Button variant="primary" disabled={busy || !!status?.active_job_id || !status?.connected || !status.key_configured} onClick={() => void act(async () => {
        await openApiData(openApiClient.POST('/api/v1/backups/run')); setMessage('Backup queued. Progress is available in Tasks.')
      })}>{status?.active_job_id ? 'Backup in progress…' : 'Back up now'}</Button>
      <Button disabled={busy || !status?.connected} onClick={() => void act(async () => {
        const result = await openApiData(openApiClient.GET('/api/v1/backups/google/snapshots')) as unknown as { items: Snapshot[] }
        setSnapshots(result.items); if (!result.items.length) setMessage('No remote snapshots found.')
      })}>Browse restore points</Button>
    </div>
    {!!snapshots.length && <table className="data-table"><thead><tr><th>Snapshot</th><th>Uploaded</th><th>Status</th><th /></tr></thead><tbody>{snapshots.map(item => <tr key={item.id}>
      <td>{item.name}</td><td>{new Date(item.created_at).toLocaleString()}</td><td>{item.verified ? 'Upload verified' : 'Not verified'}</td><td><a href={item.download_url} target="_blank" rel="noreferrer">Open in Google Drive</a></td>
    </tr>)}</tbody></table>}
    <details><summary>Restore on a new server</summary><ol>
      <li>Download a snapshot from Google Drive and locate your saved recovery key.</li>
      <li>Run <code>python backend/scripts/restore_backup.py backup.mlbackup restored</code> from a Medialogue checkout. Enter the key when prompted. The tool verifies and stages the files without overwriting an installation.</li>
      <li>Stop Medialogue and follow the tool’s instructions to restore the database, configuration, application secret, and archive.</li>
      <li>Start Medialogue, verify your media paths and clients, then inspect the bulk torrent re-import plan below.</li>
    </ol></details>
  </Panel>
}
