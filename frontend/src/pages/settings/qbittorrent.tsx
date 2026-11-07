// qBittorrent tab: download clients.
import { useEffect, useState } from 'react'
import { Field, Note, SectionHead, Secret, SettingsGroup, ok } from '../../components/settings'
import type { StatusTone } from '../../components/settings'
import { Icon } from '../../components/Icon'
import { useConfirm } from '../../components/Confirm'
import { Modal } from '../../components/Modal'
import { Badge, Button, EmptyState, EnabledButton, Input, Segmented, Select } from '../../components/ui'
import { integrationsApi } from '../../api/modules/integrations'
import { useUrlState } from '../../lib/urlState'
import type { DownloadClient } from '../../types'
import { formatEvidenceDate } from '../shared'
import { DISCARD_CHANGES } from './shared'

type DownloadClientDraft = {
  name: string
  url: string
  username: string
  password: string
  scope: 'movies' | 'shows'
  category: string
  tags: string
  enabled: boolean
  pollIntervalSeconds: number
  recentPriority: 'first' | 'last'
  olderPriority: 'first' | 'last'
  sequentialOrder: boolean
  firstLastFirst: boolean
  contentLayout: 'default' | 'original' | 'subfolder'
  completedDownloadHandling: boolean
}

function downloadClientPayload(draft: DownloadClientDraft) {
  return {
    name: draft.name.trim(),
    url: draft.url.trim(),
    username: draft.username.trim(),
    ...(draft.password ? { password: draft.password } : {}),
    scope: draft.scope,
    category: draft.category.trim() || null,
    tags: draft.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
    enabled: draft.enabled,
    poll_interval_seconds: draft.pollIntervalSeconds,
    recent_priority: draft.recentPriority,
    older_priority: draft.olderPriority,
    sequential_order: draft.sequentialOrder,
    first_last_first: draft.firstLastFirst,
    content_layout: draft.contentLayout,
    completed_download_handling: draft.completedDownloadHandling,
  }
}

const emptyDownloadClient: DownloadClientDraft = {
  name: '',
  url: 'http://qbittorrent:8080',
  username: '',
  password: '',
  scope: 'movies',
  category: '',
  tags: '',
  enabled: true,
  pollIntervalSeconds: 30,
  recentPriority: 'last',
  olderPriority: 'last',
  sequentialOrder: false,
  firstLastFirst: false,
  contentLayout: 'default',
  completedDownloadHandling: true,
}

function draftFromDownloadClient(client: DownloadClient): DownloadClientDraft {
  return {
    name: client.name,
    url: client.url,
    username: client.username ?? '',
    password: '',
    scope: client.scope,
    category: client.category ?? '',
    tags: client.tags.join(', '),
    enabled: client.enabled,
    pollIntervalSeconds: client.pollIntervalSeconds ?? 30,
    recentPriority: client.recentPriority,
    olderPriority: client.olderPriority,
    sequentialOrder: client.sequentialOrder,
    firstLastFirst: client.firstLastFirst,
    contentLayout: client.contentLayout,
    completedDownloadHandling: client.completedDownloadHandling,
  }
}

export function QBittorrentSettings() {
  const confirm = useConfirm()
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [selectedId, setSelectedId] = useUrlState('client')
  const [draft, setDraft] = useState<DownloadClientDraft>(emptyDownloadClient)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [editorOpen, setEditorOpen] = useState(false)

  const loadClients = async (keepSelection = true) => {
    setLoading(true)
    try {
      const items = await integrationsApi.downloadClients()
      setClients(items)
      const nextId = keepSelection && selectedId && items.some((item) => item.id === selectedId) ? selectedId : items[0]?.id ?? ''
      setSelectedId(nextId)
      const selected = items.find((item) => item.id === nextId)
      setDraft(selected ? draftFromDownloadClient(selected) : emptyDownloadClient)
      setDirty(false)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load qBittorrent clients.')
    } finally { setLoading(false) }
  }

  useEffect(() => { void loadClients(false) }, [])

  const selectClient = (id: string) => {
    const client = clients.find((item) => item.id === id)
    setSelectedId(id)
    setDraft(client ? draftFromDownloadClient(client) : emptyDownloadClient)
    setMessage(''); setError(''); setDirty(false); setEditorOpen(true)
  }
  const updateDraft = <K extends keyof DownloadClientDraft>(key: K, value: DownloadClientDraft[K]) => { setDirty(true); setDraft((current) => ({ ...current, [key]: value })) }
  const payload = () => downloadClientPayload(draft)

  const save = async () => {
    if (!draft.name.trim() || !draft.url.trim()) { setError('Name and URL are required.'); return }
    if (!selectedId && !draft.password) { setError('Password is required for a new qBittorrent client.'); return }
    setBusy(true); setError(''); setMessage('Saving qBittorrent client…')
    try {
      const value = selectedId
        ? await integrationsApi.updateDownloadClient(selectedId, { ...payload(), expected_revision: clients.find((item) => item.id === selectedId)?.revision })
        : await integrationsApi.createDownloadClient({ ...payload(), password: draft.password })
      setClients((items) => selectedId ? items.map((item) => item.id === value.id ? value : item) : [...items, value])
      setSelectedId(value.id); setDraft(draftFromDownloadClient(value)); setDirty(false); setEditorOpen(false); setMessage(`${value.name} saved. Password is stored server-side and never returned to the browser.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save qBittorrent client.'); setMessage('') }
    finally { setBusy(false) }
  }
  const refresh = async () => {
    if (!selectedId) return
    setBusy(true); setError(''); setMessage('Refreshing qBittorrent health…')
    try {
      const result = await integrationsApi.refreshDownloadClient(selectedId)
      setClients((items) => items.map((item) => item.id === selectedId ? { ...item, health: result.status, latency_ms: result.latency_ms, last_error: result.message, last_checked_at: new Date().toISOString() } : item))
      setMessage(result.status === 'healthy' ? 'Client health is healthy.' : result.message ?? `qBittorrent status: ${result.status}.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not refresh qBittorrent health.'); setMessage('') }
    finally { setBusy(false) }
  }
  const refreshAll = async () => {
    if (!clients.length) return
    setBusy(true); setError(''); setMessage('Refreshing health for every qBittorrent client…')
    const results = await Promise.allSettled(clients.map((client) => integrationsApi.refreshDownloadClient(client.id)))
    setClients((items) => items.map((item, index) => {
      const result = results[index]
      return result?.status === 'fulfilled' ? { ...item, health: result.value.status, latency_ms: result.value.latency_ms, last_error: result.value.message, last_checked_at: new Date().toISOString() } : item
    }))
    const failedCount = results.filter((result) => result.status === 'rejected').length
    setMessage(failedCount ? `Refreshed ${clients.length} clients; ${failedCount} could not be reached.` : `Health refreshed for all ${clients.length} qBittorrent clients.`)
    setBusy(false)
  }
  const remove = async () => {
    if (!selectedId) return
    if (!await confirm({
      title: 'Remove this qBittorrent client?',
      body: 'Medialogue stops talking to it. Existing torrent history is kept.',
      confirmLabel: 'Remove client',
    })) return
    setBusy(true); setError('')
    try {
      await integrationsApi.deleteDownloadClient(selectedId)
      const remaining = clients.filter((item) => item.id !== selectedId)
      setClients(remaining); const next = remaining[0]
      setSelectedId(next?.id ?? ''); setDraft(next ? draftFromDownloadClient(next) : emptyDownloadClient); setEditorOpen(false); setMessage('Client configuration removed; torrent history was not changed.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove qBittorrent client.') }
    finally { setBusy(false) }
  }
  const selected = clients.find((item) => item.id === selectedId)
  const healthTone: StatusTone = selected?.health === 'healthy' ? 'green' : selected?.health === 'unavailable' ? 'red' : 'neutral'
  const connectionDetail = selected ? [selected.latency_ms !== undefined ? `${selected.latency_ms} ms` : '', selected.last_success_at ? `last success ${formatEvidenceDate(selected.last_success_at)}` : selected.last_checked_at ? `checked ${formatEvidenceDate(selected.last_checked_at)}` : ''].filter(Boolean).join(' · ') : undefined
  const [togglingId, setTogglingId] = useState('')

  const toggleClient = async (client: DownloadClient) => {
    setTogglingId(client.id); setError(''); setMessage('')
    try {
      const next = { ...draftFromDownloadClient(client), enabled: !client.enabled }
      await integrationsApi.updateDownloadClient(client.id, downloadClientPayload(next))
      setClients((items) => items.map((item) => item.id === client.id ? { ...item, enabled: next.enabled } : item))
      if (client.id === selectedId) setDraft((current) => ({ ...current, enabled: next.enabled }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not update this client.') }
    finally { setTogglingId('') }
  }
  const beginAdd = () => { setSelectedId(''); setDraft(emptyDownloadClient); setDirty(false); setMessage(''); setError(''); setEditorOpen(true) }
  const closeEditor = async () => {
    if (dirty && !await confirm(DISCARD_CHANGES)) return
    setEditorOpen(false); setMessage(''); setError('')
  }
  return <div className="provider-settings">
    <SectionHead title="Download Clients" description="qBittorrent instances Medialogue observes and uses for explicit grabs." action={<div className="button-row"><Button variant="secondary" icon="refresh" onClick={() => void refreshAll()} disabled={busy || !clients.length}>{busy ? 'Refreshing…' : 'Refresh health'}</Button><Button variant="primary" icon="plus" onClick={beginAdd}>Add client</Button></div>} />
    {error && !editorOpen && <Note message={{ tone: 'error', text: error }} />}
    {message && !editorOpen && <Note message={{ tone: 'ok', text: message }} />}
    {clients.length > 0 && <div className="provider-card-grid">
      {clients.map((client) => <article className={`provider-card ${client.enabled ? '' : 'provider-card-disabled'}`} role="button" tabIndex={0} key={client.id} onClick={() => selectClient(client.id)} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectClient(client.id) } }}>
        <div className="provider-card-head"><div className={`provider-card-icon ${client.scope === 'movies' ? 'kind-movie' : 'kind-show'}`}><Icon name={client.scope === 'movies' ? 'clapper' : 'tv-screen'} size={20} /></div><button type="button" className={`enabled-button cf-card-toggle ${client.enabled ? 'on' : 'off'}`} aria-pressed={client.enabled} aria-label={`Turn ${client.name} ${client.enabled ? 'off' : 'on'}`} disabled={togglingId === client.id} onClick={(event) => { event.stopPropagation(); void toggleClient(client) }}>{togglingId === client.id ? 'Saving…' : client.enabled ? 'On' : 'Off'}</button></div>
        <strong>{client.name}</strong><span>{client.url}</span>
        <div className="provider-card-meta"><span className={`provider-health ${client.health === 'healthy' ? 'healthy' : client.health === 'unavailable' ? 'error' : ''}`}><i />{client.health}</span></div>
      </article>)}
    </div>}
    {!clients.length && !loading && <EmptyState
      icon="qbittorrent"
      title="No download clients yet"
      detail="Add a qBittorrent instance so Medialogue can observe downloads and send grabs to it."
      action={<Button variant="primary" icon="plus" onClick={beginAdd}>Add client</Button>}
    />}

    {editorOpen && <Modal wide title={selected ? selected.name : 'Add qBittorrent client'} onClose={closeEditor}
      titleAction={<>
        <EnabledButton enabled={draft.enabled} onChange={(next) => updateDraft('enabled', next)} />
        {selected && <Button variant="ghost" icon="refresh" onClick={refresh} disabled={busy}>Refresh health</Button>}
      </>}
      headerAction={selected ? <div className="provider-header-status">{connectionDetail && <span className={`status-detail ${selected.health === 'healthy' ? 'ok' : selected.last_error ? 'err' : ''}`}>{connectionDetail}</span>}<Badge tone={healthTone}>{loading ? 'Loading' : selected.health}</Badge></div> : undefined}
      footer={<>
      {selected && <Button variant="danger" onClick={remove} disabled={busy}>Delete</Button>}
      <span className="modal-foot-spacer" />
      <Button variant="ghost" onClick={closeEditor} disabled={busy}>Cancel</Button>
      <Button variant="primary" onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save client'}</Button>
    </>}>
      <div className="qbit-editor provider-modal-editor">
        <div className={`settings-form scope-${draft.scope}`}>
          <SettingsGroup legend="Connection">
            <Field label="Display name" help="Shown in Medialogue only.">
              <Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} placeholder="qbit-movies-1" />
            </Field>
            <Field label="URL" help={<>Web UI address, including port. Example: <code>http://qbittorrent:8080</code></>}>
              <Input value={draft.url} onChange={(event) => updateDraft('url', event.target.value)} placeholder="http://qbittorrent:8080" />
            </Field>
            <Field label="Username" help="Web UI account. Blank if this host needs no login.">
              <Input value={draft.username} onChange={(event) => updateDraft('username', event.target.value)} autoComplete="off" />
            </Field>
            <Field label="Password"
              help={selected?.password_configured ? 'Held server-side. Leave blank to keep the current one.' : 'Held server-side and never sent back to the browser.'}>
              <Secret value={draft.password} onChange={(event) => updateDraft('password', event.target.value)} placeholder={selected?.password_configured ? 'Stored server-side · leave blank to preserve' : 'Required for authenticated clients'} />
            </Field>
            <Field label="Polling interval" help="How often progress is polled. Does not affect download speed.">
              <Select value={String(draft.pollIntervalSeconds)} onChange={(event) => updateDraft('pollIntervalSeconds', Number(event.target.value))}><option value="5">5 seconds</option><option value="10">10 seconds</option><option value="15">15 seconds</option><option value="30">30 seconds</option><option value="60">1 minute</option><option value="300">5 minutes</option></Select>
            </Field>
          </SettingsGroup>

          <SettingsGroup legend="Library">
            <Field label="Scope" help="Which library this client receives grabs for. Every client is always read.">
              <Segmented
                ariaLabel="Scope"
                value={draft.scope}
                onChange={(next) => updateDraft('scope', next)}
                options={[{ value: 'movies', label: 'Movies', tone: 'movie' }, { value: 'shows', label: 'Shows', tone: 'show' }]}
              />
            </Field>
            <Field label="Default category" help="Preselected for manual downloads. qBittorrent owns its save path.">
              <Input value={draft.category} onChange={(event) => updateDraft('category', event.target.value)} placeholder="movies" />
            </Field>
            <Field label="Tags" help="Applied to torrents Medialogue adds. Comma-separated.">
              <Input value={draft.tags} onChange={(event) => updateDraft('tags', event.target.value)} placeholder="movies, managed" />
            </Field>
          </SettingsGroup>

          <SettingsGroup legend="Download behaviour">
            <Field label="Recent priority" help="Queue position for releases posted during the last 21 days.">
              <Segmented
                ariaLabel="Recent priority"
                value={draft.recentPriority}
                onChange={(next) => updateDraft('recentPriority', next)}
                options={[{ value: 'last', label: 'Last' }, { value: 'first', label: 'First' }]}
              />
            </Field>
            <Field label="Older priority" help="Queue position for releases older than 21 days.">
              <Segmented
                ariaLabel="Older priority"
                value={draft.olderPriority}
                onChange={(next) => updateDraft('olderPriority', next)}
                options={[{ value: 'last', label: 'Last' }, { value: 'first', label: 'First' }]}
              />
            </Field>
            <Field label="Content layout" help="How qBittorrent lays the download out on disk.">
              <Segmented
                ariaLabel="Content layout"
                value={draft.contentLayout}
                onChange={(next) => updateDraft('contentLayout', next)}
                options={[{ value: 'default', label: 'Default' }, { value: 'original', label: 'Original' }, { value: 'subfolder', label: 'Subfolder' }]}
              />
            </Field>
            <Field label="Sequential order" help="Download pieces in order.">
              <Segmented ariaLabel="Sequential order" value={draft.sequentialOrder ? 'on' : 'off'} onChange={(next) => updateDraft('sequentialOrder', next === 'on')} options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'On' }]} />
            </Field>
            <Field label="First and last first" help="Prioritize the first and last pieces.">
              <Segmented ariaLabel="First and last first" value={draft.firstLastFirst ? 'on' : 'off'} onChange={(next) => updateDraft('firstLastFirst', next === 'on')} options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'On' }]} />
            </Field>
          </SettingsGroup>
        </div>

        {selected?.last_error && <Note message={{ tone: 'error', text: `Last connection error: ${selected.last_error}` }} />}
        {error && <Note message={{ tone: 'error', text: error }} />}
        {message && <Note message={{ tone: 'ok', text: message }} />}

      </div>
    </Modal>}
  </div>
}
