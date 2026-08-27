import { useState } from 'react'
import { api } from '../api/client'
import type { DownloadClient, DownloadClientScope } from '../types'
import { Icon } from './Icon'
import { Select } from './ui'

export function DownloadClientField({ clients, clientId, scope, disabled, onChange, onRefreshed }: {
  clients: DownloadClient[]
  clientId: string
  scope: DownloadClientScope
  disabled?: boolean
  onChange: (id: string) => void

  onRefreshed: (clients: DownloadClient[]) => void
}) {
  const [checking, setChecking] = useState(false)
  const [result, setResult] = useState('')
  const selected = clients.find((client) => client.id === clientId)

  const recheck = async () => {
    if (!clientId) return
    setChecking(true)
    setResult('')
    try {
      const test = await api.testDownloadClient(clientId)
      const rows = await api.downloadClients()
      onRefreshed(rows.filter((client) => client.enabled && client.scope === scope))
      setResult(test.status === 'ok'
        ? `Reachable${test.latency_ms ? ` · ${test.latency_ms} ms` : ''}`
        : test.message || 'Still unreachable.')
    } catch (reason) {
      setResult(reason instanceof Error ? reason.message : 'Could not reach this client.')
    } finally {
      setChecking(false)
    }
  }

  const healthy = selected?.health === 'healthy'
  return <label>
    <span className="field-label">qBittorrent</span>
    <div className="client-field">
      <Select value={clientId} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </Select>
      <button
        type="button"
        className="release-refresh"
        onClick={() => void recheck()}
        disabled={checking || !clientId}
        aria-label="Check this client again"
      ><Icon name="refresh" size={15} /></button>
    </div>
    <small>
      {selected
        ? <>
            <span className={`client-health ${healthy ? 'ok' : 'bad'}`}>{checking ? 'checking…' : selected.health}</span>
            {' · '}{selected.url}
            {result ? <><br />{result}</> : null}
          </>
        : 'Choose the client that should receive the selected torrent.'}
    </small>
  </label>
}
