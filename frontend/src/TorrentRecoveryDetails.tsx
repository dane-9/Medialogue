import { useEffect, useState } from 'react'
import { openApiClient, openApiData } from './api/transport'
import { Button, Panel } from './components/ui'

type Recovery = { captured_at?: string; properties?: { save_path?: string }; settings?: Record<string, unknown>; files?: { index: number; name?: string; size?: number; priority: number }[] }
type Manifest = { recovery?: Recovery; client_recovery?: Record<string, Recovery>; download_client_observations?: { download_client_id: string; download_client_name: string; category?: string; tags?: string[] }[] }

export default function TorrentRecoveryDetails({ id, onClose }: { id: string; onClose: () => void }) {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    void openApiData(openApiClient.GET('/api/v1/torrent-archive/{torrent_id}', { params: { path: { torrent_id: id } } }))
      .then(value => { if (alive) setManifest(value.manifest as Manifest) })
      .catch(e => { if (alive) setError(String(e)) })
    return () => { alive = false }
  }, [id])
  const clients = Object.entries(manifest?.client_recovery ?? {})
  const captures = clients.length ? clients : manifest?.recovery ? [['legacy', manifest.recovery] as const] : []
  return <Panel title="Torrent recovery details" action={<Button onClick={onClose}>Close</Button>}>
    {error && <p role="alert">{error}</p>}
    {!manifest && !error && <p>Reading recovery settings…</p>}
    {captures.map(([clientId, recovery]) => {
      const observation = manifest?.download_client_observations?.find(row => row.download_client_id === clientId)
      return <div className="settings-section" key={clientId}><div>
        <h3>{observation?.download_client_name ?? (clientId === 'legacy' ? 'Legacy recovery snapshot' : 'Previous client')}</h3>
        <p>{recovery.captured_at ? `Captured ${new Date(recovery.captured_at).toLocaleString()}` : 'Capture time unknown'}</p>
        <p>Save path: {recovery.properties?.save_path ?? 'Not captured'} · Category: {observation?.category ?? 'None'} · Tags: {observation?.tags?.join(', ') || 'None'}</p>
        <p>Restoration applies file selection, automatic management, sequential/first-last options, captured speed limits, complete share limits, force-start, and super-seeding settings. Queue position, transfer statistics, renamed file paths, tracker edits, and private resume data are not replayed.</p>
        <details><summary>Captured torrent settings</summary><dl>{Object.entries(recovery.settings ?? {}).map(([name, value]) => <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>)}</dl></details>
        <details><summary>File selection ({recovery.files?.filter(file => file.priority > 0).length ?? 0} selected of {recovery.files?.length ?? 0})</summary>
          <table className="data-table"><thead><tr><th>File</th><th>Selection</th></tr></thead><tbody>{recovery.files?.map(file => <tr key={file.index}><td>{file.name ?? `File ${file.index}`}</td><td>{file.priority === 0 ? 'Do not download' : file.priority === 7 ? 'Maximum priority' : file.priority === 6 ? 'High priority' : 'Normal priority'}</td></tr>)}</tbody></table>
        </details>
      </div></div>
    })}
    {manifest && !captures.length && <p>No recovery settings have been captured yet. Retry archiving while the torrent is still available in qBittorrent.</p>}
  </Panel>
}
