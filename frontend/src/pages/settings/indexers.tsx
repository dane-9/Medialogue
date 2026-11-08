// Indexers tab: Prowlarr and Jackett Torznab indexers.
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Field, Note, SectionHead, Secret, SettingsGroup, ok } from '../../components/settings'
import type { StatusTone } from '../../components/settings'
import { Icon } from '../../components/Icon'
import { useConfirm } from '../../components/Confirm'
import { Modal } from '../../components/Modal'
import { Badge, Button, EmptyState, EnabledButton, Input, Segmented, Select } from '../../components/ui'
import { integrationsApi } from '../../api/modules/integrations'
import { useUrlState } from '../../lib/urlState'
import { useLeaveGuard } from '../../lib/leaveGuard'
import { moveItem } from '../../lib/reorder'
import type { DownloadClient, Indexer, IndexerScope } from '../../types'
import prowlarrIcon from '../../assets/prowlarr.svg'
import jackettIcon from '../../assets/jackett.svg'
import { indexerBadgePreview } from '../shared'
import { DISCARD_CHANGES } from './shared'

type IndexerDraft = {
  name: string
  torznabUrl: string
  apiKey: string
  scope: IndexerScope
  enabled: boolean
  timeoutSeconds: number
  enableRss: boolean
  enableInteractiveSearch: boolean
  categories: string
  minimumSeeders: number
  color: string
  multiLanguages: string
  downloadClientIds: string[]
}

type IndexerPreset = 'prowlarr' | 'jackett'

const indexerPresets: Record<IndexerPreset, {
  name: string
  icon: string
  description: string
  urlPlaceholder: string
  urlHelp: ReactNode
  keyHelp: string
}> = {
  prowlarr: {
    name: 'Prowlarr',
    icon: prowlarrIcon,
    description: 'Use the Torznab feed for one indexer configured in Prowlarr.',
    urlPlaceholder: 'http://prowlarr:9696/1/api',
    urlHelp: <>Copy the full Torznab feed URL for one Prowlarr indexer, including the trailing <code>/api</code>. Example: <code>http://prowlarr:9696/1/api</code>.</>,
    keyHelp: 'The API key from Prowlarr.',
  },
  jackett: {
    name: 'Jackett',
    icon: jackettIcon,
    description: 'Use the individual Torznab feed for one indexer configured in Jackett.',
    urlPlaceholder: 'http://jackett:9117/api/v2.0/indexers/indexer-name/results/torznab/api',
    urlHelp: <>Copy the full Torznab feed URL for one configured Jackett indexer. Add indexers individually; do not use Jackett&apos;s <code>/indexers/all/</code> endpoint.</>,
    keyHelp: 'The API key shown in Jackett.',
  },
}

function presetFromUrl(url: string): IndexerPreset {
  return /\/api\/v2\.0\/indexers\//i.test(url) ? 'jackett' : 'prowlarr'
}

function isJackettAllUrl(url: string) {
  return /\/api\/v2\.0\/indexers\/all\/results\/torznab(?:\/api)?\/?(?:\?|$)/i.test(url.trim())
}

const emptyIndexer: IndexerDraft = {
  name: '',
  torznabUrl: '',
  apiKey: '',
  scope: 'both',
  enabled: true,
  timeoutSeconds: 15,
  enableRss: true,
  enableInteractiveSearch: true,
  categories: '',
  minimumSeeders: 1,
  color: '',
  multiLanguages: '',
  downloadClientIds: [],
}

function draftFromIndexer(indexer: Indexer): IndexerDraft {
  return {
    name: indexer.name,
    torznabUrl: indexer.torznabUrl,
    apiKey: '',
    scope: indexer.scope,
    enabled: indexer.enabled,
    timeoutSeconds: indexer.timeoutSeconds || 15,
    enableRss: indexer.enableRss,
    enableInteractiveSearch: indexer.enableInteractiveSearch,
    categories: indexer.categories.join(', '),
    minimumSeeders: indexer.minimumSeeders,
    color: indexer.color ?? '',
    multiLanguages: indexer.multiLanguages.join(', '),
    downloadClientIds: [...indexer.downloadClientIds],
  }
}

export function IndexerSettings() {
  const confirm = useConfirm()
  const [indexers, setIndexers] = useState<Indexer[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [selectedId, setSelectedId] = useUrlState('indexer')
  const [draft, setDraft] = useState<IndexerDraft>(emptyIndexer)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [dirty, setDirty] = useState(false)
  const [editorOpen, setEditorOpen] = useState(false)
  const [presetPickerOpen, setPresetPickerOpen] = useState(false)
  const [preset, setPreset] = useState<IndexerPreset>('prowlarr')
  // The order being dragged into, until saved; null shows the saved order.
  const [order, setOrder] = useState<string[] | null>(null)
  const [dragId, setDragId] = useState<string | null>(null)
  const [savingOrder, setSavingOrder] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [items, clientRows] = await Promise.all([integrationsApi.indexers(), integrationsApi.downloadClients()])
      setIndexers(items)
      setClients(clientRows)
      setError('')
      const selected = items.find((item) => item.id === selectedId) ?? items[0]
      setDraft(selected ? draftFromIndexer(selected) : emptyIndexer)
      setDirty(false)
      setSelectedId(selected?.id ?? '')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load indexers.')
    } finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  const selected = indexers.find((item) => item.id === selectedId)
  // Indexers added or removed meanwhile: new ones at the end, gone ones dropped.
  const shown = order
    ? [...order.flatMap((id) => indexers.filter((item) => item.id === id)), ...indexers.filter((item) => !order.includes(item.id))]
    : indexers
  const orderDirty = shown.some((item, index) => item.id !== indexers[index]?.id)
  useLeaveGuard(orderDirty, async () => {
    if (!await confirm({ title: 'Discard the new indexer order?', body: 'The indexers go back to their saved order.', confirmLabel: 'Discard' })) return false
    setOrder(null)
    return true
  })
  const moveIndexer = (id: string, to: number) => {
    const ids = shown.map((item) => item.id)
    const from = ids.indexOf(id)
    if (from < 0 || to < 0 || to >= ids.length || from === to) return
    setOrder(moveItem(ids, from, to))
  }
  const saveOrder = async () => {
    setSavingOrder(true); setError(''); setMessage('')
    try {
      setIndexers(await integrationsApi.reorderIndexers(shown.map((item) => item.id)))
      setOrder(null)
      setMessage('Indexer order saved. When results are otherwise equal, the higher indexer wins.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the indexer order.') }
    finally { setSavingOrder(false) }
  }
  const selectIndexer = (id: string) => {
    const item = indexers.find((candidate) => candidate.id === id)
    setSelectedId(id)
    setDraft(item ? draftFromIndexer(item) : emptyIndexer)
    if (item) setPreset(presetFromUrl(item.torznabUrl))
    setDirty(false)
    setMessage('')
    setError('')
    setEditorOpen(true)
  }
  const compatibleClients = clients.filter((client) => draft.scope === 'both' || client.scope === draft.scope)
  const updateDraft = <K extends keyof IndexerDraft>(key: K, value: IndexerDraft[K]) => {
    setDirty(true)
    setDraft((current) => ({
      ...current, [key]: value,
      ...(key === 'scope' ? { downloadClientIds: current.downloadClientIds.filter((id) => clients.some((client) => client.id === id && (value === 'both' || client.scope === value))) } : {}),
    }))
  }

  const save = async () => {
    if (!draft.name.trim() || !draft.torznabUrl.trim()) { setError('Name and Torznab URL are required.'); return }
    if (!selectedId && !draft.apiKey.trim()) { setError('API key is required for a new indexer.'); return }
    if (preset === 'jackett' && isJackettAllUrl(draft.torznabUrl)) { setError('Add Jackett indexers individually. The aggregate “all” endpoint is not supported.'); return }
    // Grabs need somewhere to go. Not enforced when nothing is configured to
    // choose from, or a fresh install could never add its first indexer.
    if (clients.length && !draft.downloadClientIds.length) { setError('Choose at least one download client for this indexer.'); return }
    if (draft.downloadClientIds.some((id) => !compatibleClients.some((client) => client.id === id))) { setError('Choose download clients matching this indexer’s scope.'); return }
    setBusy(true); setError(''); setMessage('Saving indexer…')
    try {
      const base = {
        name: draft.name.trim(),
        torznab_url: draft.torznabUrl.trim(),
        scope: draft.scope,
        enabled: draft.enabled,
        timeout_seconds: draft.timeoutSeconds,
        enable_rss: draft.enableRss,
        enable_interactive_search: draft.enableInteractiveSearch,
        categories: draft.categories.split(',').map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value >= 0),
        minimum_seeders: draft.minimumSeeders,
        color: draft.color,
        multi_languages: draft.multiLanguages.split(',').map((value) => value.trim()).filter(Boolean),
        download_client_ids: draft.downloadClientIds,
      }
      const value = selectedId
        ? await integrationsApi.updateIndexer(selectedId, { ...base, ...(draft.apiKey.trim() ? { api_key: draft.apiKey.trim() } : {}), expected_revision: selected?.revision })
        : await integrationsApi.createIndexer({ ...base, color: draft.color || undefined, api_key: draft.apiKey.trim() })
      setIndexers((items) => selectedId ? items.map((item) => item.id === value.id ? value : item) : [...items, value])
      setSelectedId(value.id)
      setDraft(draftFromIndexer(value))
      setDirty(false)
      setEditorOpen(false)
      setMessage(`${value.name} saved. The API key is stored server-side and never returned to the browser.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save indexer.')
      setMessage('')
    } finally { setBusy(false) }
  }

  const test = async () => {
    if (!selectedId && (!draft.torznabUrl.trim() || !draft.apiKey.trim())) { setError('Torznab URL and API key are required to test a new indexer.'); return }
    if (preset === 'jackett' && isJackettAllUrl(draft.torznabUrl)) { setError('Add Jackett indexers individually. The aggregate “all” endpoint is not supported.'); return }
    setBusy(true); setError(''); setMessage('Testing indexer connection…')
    try {
      const result = selectedId
        ? await integrationsApi.testIndexer(selectedId)
        : await integrationsApi.testIndexer(undefined, { torznab_url: draft.torznabUrl.trim(), api_key: draft.apiKey.trim(), timeout_seconds: draft.timeoutSeconds })
      setMessage(result.status === 'healthy' ? `Indexer is reachable${result.title ? ` · ${result.title}` : ''}${result.latencyMs ? ` · ${result.latencyMs} ms` : ''}.` : result.message ?? `Indexer status: ${result.status}.`)
      if (selectedId) await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not test indexer.')
      setMessage('')
    } finally { setBusy(false) }
  }
  const testAll = async () => {
    if (!indexers.length) return
    setBusy(true); setError(''); setMessage('Testing all indexers…')
    const results = await Promise.allSettled(indexers.map((indexer) => integrationsApi.testIndexer(indexer.id)))
    await load()
    const failedCount = results.filter((result) => result.status === 'rejected' || (result.status === 'fulfilled' && result.value.status !== 'healthy')).length
    setMessage(failedCount ? `Tested ${indexers.length} indexers; ${failedCount} reported a problem.` : `All ${indexers.length} indexers tested successfully.`)
    setBusy(false)
  }

  const remove = async () => {
    if (!selectedId) return
    if (!await confirm({
      title: 'Remove this indexer?',
      body: 'It will no longer be searched. Past search history is kept.',
      confirmLabel: 'Remove indexer',
    })) return
    setBusy(true); setError('')
    try {
      await integrationsApi.deleteIndexer(selectedId)
      const remaining = indexers.filter((item) => item.id !== selectedId)
      setIndexers(remaining)
      const next = remaining[0]
      setSelectedId(next?.id ?? '')
      setDraft(next ? draftFromIndexer(next) : emptyIndexer)
      setEditorOpen(false)
      setMessage('Indexer configuration removed.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove indexer.') }
    finally { setBusy(false) }
  }

  const healthTone: StatusTone = selected?.health === 'healthy' ? 'green' : selected?.health === 'unavailable' ? 'red' : 'neutral'
  const indexerDetail = selected ? `${selected.health}${selected.latencyMs !== undefined ? ` · ${selected.latencyMs} ms` : ''}` : undefined
  const [togglingId, setTogglingId] = useState('')

  const toggleIndexer = async (indexer: Indexer) => {
    setTogglingId(indexer.id); setError(''); setMessage('')
    try {
      await integrationsApi.updateIndexer(indexer.id, { enabled: !indexer.enabled })
      setIndexers((items) => items.map((item) => item.id === indexer.id ? { ...item, enabled: !indexer.enabled } : item))
      if (indexer.id === selectedId) setDraft((current) => ({ ...current, enabled: !indexer.enabled }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not update this indexer.') }
    finally { setTogglingId('') }
  }
  const beginAdd = () => {
    setSelectedId('')
    setDraft(emptyIndexer)
    setDirty(false)
    setMessage('')
    setError('')
    setPresetPickerOpen(true)
  }
  const choosePreset = (next: IndexerPreset) => {
    setPreset(next)
    setPresetPickerOpen(false)
    setEditorOpen(true)
  }
  const closeEditor = async () => {
    if (dirty && !await confirm(DISCARD_CHANGES)) return
    setEditorOpen(false); setMessage(''); setError('')
  }
  return <div className="provider-settings indexer-settings">
    <SectionHead title="Indexers" description="Torznab endpoints used for interactive Movie and Show searches." action={<div className="button-row"><Button variant="secondary" onClick={() => void testAll()} disabled={busy || !indexers.length}>{busy ? 'Testing…' : 'Test all'}</Button><Button variant="primary" icon="plus" onClick={beginAdd}>Add indexer</Button><Button variant={orderDirty ? 'primary' : 'secondary'} onClick={() => void saveOrder()} disabled={!orderDirty || savingOrder}>{savingOrder ? 'Saving…' : 'Save order'}</Button></div>} />
    {error && !editorOpen && <Note message={{ tone: 'error', text: error }} />}
    {message && !editorOpen && <Note message={{ tone: 'ok', text: message }} />}
    {indexers.length > 1 && <p className="indexer-order-hint">{orderDirty ? 'Order changed. Save it to keep it.' : 'Drag to reorder (or Alt + arrow keys). When results are otherwise equal, the first indexer wins.'}</p>}
    {indexers.length > 0 && <div className="provider-card-grid">
      {shown.map((indexer, index) => { const source = presetFromUrl(indexer.torznabUrl); return <article className={`provider-card provider-card-sortable ${indexer.enabled ? '' : 'provider-card-disabled'} ${dragId === indexer.id ? 'provider-card-dragging' : ''}`} role="button" tabIndex={0} key={indexer.id}
        aria-label={`${indexer.name}, position ${index + 1} of ${shown.length}`}
        draggable={!savingOrder}
        onDragStart={(event) => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', indexer.id); setDragId(indexer.id) }}
        onDragOver={(event) => { if (!dragId) return; event.preventDefault(); if (dragId !== indexer.id) moveIndexer(dragId, index) }}
        onDrop={(event) => { event.preventDefault(); setDragId(null) }}
        onDragEnd={() => setDragId(null)}
        onClick={() => selectIndexer(indexer.id)}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return
          if (event.altKey && (event.key === 'ArrowLeft' || event.key === 'ArrowUp' || event.key === 'ArrowRight' || event.key === 'ArrowDown')) {
            event.preventDefault()
            moveIndexer(indexer.id, index + (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1))
            const card = event.currentTarget
            requestAnimationFrame(() => card.focus())
          } else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectIndexer(indexer.id) }
        }}>
        <div className="provider-card-head"><div className={`provider-card-icon indexer-source-icon indexer-source-${source}`} style={indexerBadgePreview(indexer.color ?? '')}><img src={indexerPresets[source].icon} alt={`${indexerPresets[source].name} icon`} /></div><button type="button" className={`enabled-button cf-card-toggle ${indexer.enabled ? 'on' : 'off'}`} aria-pressed={indexer.enabled} aria-label={`Turn ${indexer.name} ${indexer.enabled ? 'off' : 'on'}`} disabled={togglingId === indexer.id} onClick={(event) => { event.stopPropagation(); void toggleIndexer(indexer) }}>{togglingId === indexer.id ? 'Saving…' : indexer.enabled ? 'On' : 'Off'}</button></div>
        <strong><span className="provider-card-position">{index + 1}</span>{indexer.name}</strong><span>{indexer.torznabUrl}</span>
        <div className="provider-card-meta"><span className={`provider-health ${indexer.health === 'healthy' ? 'healthy' : indexer.health === 'unavailable' ? 'error' : ''}`}><i />{indexer.health}</span></div>
      </article>})}
    </div>}
    {!indexers.length && !loading && <EmptyState
      icon="indexers"
      title="No indexers yet"
      detail="Interactive Movie and Show searches need at least one Torznab endpoint from Prowlarr or Jackett."
      action={<Button variant="primary" icon="plus" onClick={beginAdd}>Add indexer</Button>}
    />}

    {presetPickerOpen && <Modal title="Choose indexer provider" onClose={() => setPresetPickerOpen(false)} className="indexer-preset-modal">
      <div className="indexer-preset-grid">
        {(Object.entries(indexerPresets) as Array<[IndexerPreset, typeof indexerPresets[IndexerPreset]]>).map(([key, option]) => <button type="button" className={`indexer-preset-card indexer-preset-${key}`} key={key} onClick={() => choosePreset(key)}>
          <span className="indexer-preset-heading"><span className="indexer-preset-icon"><img src={option.icon} alt="" /></span><strong>{option.name}</strong></span>
          <small className="indexer-preset-description">{option.description}</small>
          <Icon name="chevron" size={17} />
        </button>)}
      </div>
    </Modal>}

    {editorOpen && <Modal wide title={selected ? selected.name : 'Add indexer'} onClose={closeEditor}
      brand={<img className={`indexer-editor-brand indexer-editor-brand-${preset}`} src={indexerPresets[preset].icon} alt="" />}
      titleAction={<>
        <EnabledButton enabled={draft.enabled} onChange={(next) => updateDraft('enabled', next)} />
        <Button variant="ghost" icon="refresh" onClick={test} disabled={busy}>{busy ? 'Working…' : 'Test'}</Button>
      </>}
      headerAction={selected ? <div className="provider-header-status">{indexerDetail && <span className={`status-detail ${selected.health === 'healthy' ? 'ok' : selected.lastError ? 'err' : ''}`}>{indexerDetail}</span>}<Badge tone={healthTone}>{loading ? 'Loading' : selected.health}</Badge></div> : undefined}
      footer={<>
      {selected && <Button variant="danger" onClick={remove} disabled={busy}>Delete</Button>}
      <span className="modal-foot-spacer" />
      <Button variant="ghost" onClick={closeEditor} disabled={busy}>Cancel</Button>
      <Button variant="primary" onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save indexer'}</Button>
    </>}>
      <div className="qbit-editor provider-modal-editor">
        <div className={`settings-form ${draft.scope === 'movies' ? 'scope-movies' : draft.scope === 'shows' ? 'scope-shows' : ''}`}>
          <SettingsGroup legend="Connection">
            <Field label="Display name" help="Shown in Medialogue and on every search result.">
              <Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} placeholder="TorrentLeech" />
            </Field>
            <Field label="Torznab URL" help={indexerPresets[preset].urlHelp}>
              <Input value={draft.torznabUrl} onChange={(event) => updateDraft('torznabUrl', event.target.value)} placeholder={indexerPresets[preset].urlPlaceholder} />
            </Field>
            <Field label="API key"
              help={`${indexerPresets[preset].keyHelp} ${selected?.apiKeyConfigured ? 'Held server-side. Leave blank to keep the current one.' : 'Held server-side and never sent back to the browser.'}`}>
              <Secret value={draft.apiKey} onChange={(event) => updateDraft('apiKey', event.target.value)} placeholder={selected?.apiKeyConfigured ? 'Stored server-side · leave blank to preserve' : 'Required'} />
            </Field>
            <Field label="Timeout" help="How long to wait before this indexer is skipped. Others still return results.">
              <Select value={String(draft.timeoutSeconds)} onChange={(event) => updateDraft('timeoutSeconds', Number(event.target.value))}><option value="10">10 seconds</option><option value="15">15 seconds</option><option value="20">20 seconds</option><option value="30">30 seconds</option></Select>
            </Field>
          </SettingsGroup>

          <SettingsGroup legend="Searching">
            <Field label="Scope" help="Which searches query this indexer.">
              <Segmented
                ariaLabel="Scope"
                value={draft.scope}
                onChange={(next) => updateDraft('scope', next as IndexerScope)}
                options={[{ value: 'both', label: 'Both' }, { value: 'movies', label: 'Movies', tone: 'movie' }, { value: 'shows', label: 'Shows', tone: 'show' }]}
              />
            </Field>
            <Field label="Enable RSS" help="Participate in periodic RSS release discovery.">
              <Segmented ariaLabel="Enable RSS" value={draft.enableRss ? 'on' : 'off'} onChange={(next) => updateDraft('enableRss', next === 'on')} options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'On' }]} />
            </Field>
            <Field label="Interactive search" help="Answer user-triggered movie, season, and episode searches.">
              <Segmented ariaLabel="Interactive search" value={draft.enableInteractiveSearch ? 'on' : 'off'} onChange={(next) => updateDraft('enableInteractiveSearch', next === 'on')} options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'On' }]} />
            </Field>
            <Field label="Categories" help="Category IDs sent with searches. Blank lets the indexer decide.">
              <Input value={draft.categories} onChange={(event) => updateDraft('categories', event.target.value)} placeholder="2000, 5000" />
            </Field>
          </SettingsGroup>

          <SettingsGroup legend="Results">
            <Field label="Minimum seeders" help="Results with fewer seeders are rejected. Unknown counts stay.">
              <Input type="number" min="0" value={String(draft.minimumSeeders)} onChange={(event) => updateDraft('minimumSeeders', Math.max(0, Number(event.target.value) || 0))} />
            </Field>
            <Field label="MULTI languages" help="What a MULTI-tagged release here actually contains. Comma-separated.">
              <Input value={draft.multiLanguages} onChange={(event) => updateDraft('multiLanguages', event.target.value)} placeholder="English, French" />
            </Field>
            <Field label="Badge colour" wide help="Tints this indexer's badge in search results.">
              <div className="color-field">
                <input
                  type="color"
                  aria-label="Badge colour"
                  value={draft.color || '#7C8CF8'}
                  onChange={(event) => updateDraft('color', event.target.value)}
                />
                <Input
                  value={draft.color}
                  onChange={(event) => updateDraft('color', event.target.value)}
                  placeholder="Default"
                  spellCheck={false}
                />
                <span className="release-pill release-pill-indexer" style={indexerBadgePreview(draft.color)}>
                  <i>Indexer</i>{draft.name.trim() || 'Preview'}
                </span>
                {draft.color ? <button type="button" className="button button-danger" onClick={() => updateDraft('color', '')}>Clear</button> : null}
              </div>
            </Field>
            <Field label="Download clients" wide help="Which download clients receive grabs from this indexer. At least one is required.">
              {compatibleClients.length ? <div className="client-choice">
                {/* Checkboxes, not badges: these were styled as the same read-only
                    status pills used elsewhere, so nothing said they were
                    selectable, and clients named after media types read as a
                    second, broken copy of the Scope control above. */}
                {compatibleClients.map((client) => {
                  const chosen = draft.downloadClientIds.includes(client.id)
                  return <button
                    key={client.id}
                    type="button"
                    role="checkbox"
                    aria-checked={chosen}
                    className={`client-choice-option ${chosen ? 'chosen' : ''}`}
                    onClick={() => updateDraft('downloadClientIds', chosen
                      ? draft.downloadClientIds.filter((id) => id !== client.id)
                      : [...draft.downloadClientIds, client.id])}
                  >
                    <span className="client-choice-box">{chosen && <Icon name="check" size={11} />}</span>
                    <span className="client-choice-name">{client.name}</span>
                    <span className="client-choice-scope">{client.scope === 'movies' ? 'Movies' : client.scope === 'shows' ? 'Shows' : 'Both'}</span>
                  </button>
                })}
              </div> : <span className="client-choice-empty">No download clients match this scope. Configure a matching client in qBittorrent settings.</span>}
            </Field>
          </SettingsGroup>
        </div>

        {selected?.lastError && <Note message={{ tone: 'error', text: `Last connection error: ${selected.lastError}` }} />}
        {error && <Note message={{ tone: 'error', text: error }} />}
        {message && <Note message={{ tone: 'ok', text: message }} />}

      </div>
    </Modal>}
  </div>
}
