// Media tab: storage roots, scans and remote path mappings.
import { useEffect, useRef, useState } from 'react'
import { formatAge } from '../../lib/age'
import { isGeneratedRootName, nextRootName } from '../../lib/rootName'
import { moveItem } from '../../lib/reorder'
import { rootBrowseStart, rootDraftChanged, rootPathAfterBrowse } from '../../lib/rootDraft'
import type { RootDraft } from '../../lib/rootDraft'
import { describeScan } from '../../lib/scanProgress'
import { Link } from 'react-router-dom'
import { Field, Note, SectionHead, SettingsGroup, failed, ok } from '../../components/settings'
import type { Message } from '../../components/settings'
import { Icon } from '../../components/Icon'
import { Tooltip } from '../../components/Tooltip'
import { useConfirm } from '../../components/Confirm'
import { Modal } from '../../components/Modal'
import { Button, EnabledButton, Input, Segmented, Select, Slider } from '../../components/ui'
import { integrationsApi } from '../../api/modules/integrations'
import { storageApi } from '../../api/modules/storage'
import type { DownloadClient, LibrarySummary, FilesystemListing, StorageRoot, RemotePathMapping } from '../../types'
import { useLiveLibraryRefresh } from '../shared'
import { DISCARD_CHANGES } from './shared'
import { IgnoredFolders } from './IgnoredFolders'

export function StorageSettings() {
  const confirm = useConfirm()
  const [roots, setRoots] = useState<StorageRoot[]>([])
  // Multiple progress events can overlap REST reads. Only the newest root
  // snapshot may win, otherwise a slow stale response can resurrect Scanning.
  const rootsLoadGeneration = useRef(0)
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [mappings, setMappings] = useState<RemotePathMapping[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [message, setMessage] = useState<Message>(null)
  const [selectedRoot, setSelectedRoot] = useState<StorageRoot | null>(null)
  const [rootModalOpen, setRootModalOpen] = useState(false)
  const [rootBaseline, setRootBaseline] = useState<RootDraft | null>(null)
  const [rootBusy, setRootBusy] = useState(false)
  const [listing, setListing] = useState<FilesystemListing | null>(null)
  const [tmdbReady, setTmdbReady] = useState(true)
  const [summary, setSummary] = useState<LibrarySummary | null>(null)
  const [removing, setRemoving] = useState<StorageRoot | null>(null)
  const [removeEntries, setRemoveEntries] = useState(false)
  const [browseBusy, setBrowseBusy] = useState(false)
  const [addingMapping, setAddingMapping] = useState(false)
  const [selectedMapping, setSelectedMapping] = useState<RemotePathMapping | null>(null)
  const [mappingDirty, setMappingDirty] = useState(false)
  const [mappingBusy, setMappingBusy] = useState(false)
  const [name, setName] = useState('Movies')
  const [path, setPath] = useState('/media/movies')
  const [mediaType, setMediaType] = useState<'movies' | 'shows'>('movies')
  const [accessMode, setAccessMode] = useState<'read_only' | 'read_write'>('read_only')
  const [approveNewMedia, setApproveNewMedia] = useState(false)
  const [missingGraceChecks, setMissingGraceChecks] = useState(2)
  const [mappingName, setMappingName] = useState('qBittorrent path')
  const [mappingClientId, setMappingClientId] = useState('')
  const [mappingEnabled, setMappingEnabled] = useState(true)
  const [remotePrefix, setRemotePrefix] = useState('/downloads')
  const [localPrefix, setLocalPrefix] = useState('/media')
  const [mappingRootId, setMappingRootId] = useState('')

  const loadRoots = async () => {
    const generation = ++rootsLoadGeneration.current
    try {
      const nextRoots = await storageApi.storageRoots()
      if (generation === rootsLoadGeneration.current) setRoots(nextRoots)
    } catch (reason) {
      if (generation === rootsLoadGeneration.current) setMessage(failed(reason, 'Could not load media folder state.'))
    }
  }
  const load = async () => {
    const generation = ++rootsLoadGeneration.current
    try {
      const [nextRoots, nextMappings, nextClients] = await Promise.all([storageApi.storageRoots(), integrationsApi.remotePathMappings(), integrationsApi.downloadClients()])
      if (generation === rootsLoadGeneration.current) setRoots(nextRoots)
      setMappings(nextMappings); setClients(nextClients)
      setMappingRootId((value) => value || nextRoots[0]?.id || '')
    } catch (reason) { setMessage(failed(reason, 'Could not load storage configuration.')) }
  }
  useEffect(() => { void load() }, [])
  useLiveLibraryRefresh(loadRoots, 15000, true)
  useEffect(() => {
    const generation = ++rootsLoadGeneration.current
    storageApi.checkStorageRootHealth().then((nextRoots) => {
      if (generation === rootsLoadGeneration.current) setRoots(nextRoots)
    }).catch(() => undefined)
  }, [])
  useEffect(() => { storageApi.librarySummary().then(setSummary).catch(() => undefined) }, [])
  useEffect(() => {
    integrationsApi.tmdbConfiguration()
      .then((value) => setTmdbReady(Boolean(value.enabled && value.api_key_configured)))
      .catch(() => undefined)
  }, [])

  const takenNames = (except?: StorageRoot | null) =>
    roots.filter((item) => item.id !== except?.id).map((item) => item.name)

  const changeMediaType = (next: 'movies' | 'shows') => {
    setMediaType(next)
    setName((current) => isGeneratedRootName(current)
      ? nextRootName(next === 'movies' ? 'Movies' : 'Shows', takenNames(selectedRoot))
      : current)
  }

  const beginAddRoot = () => {
    setMessage(null); setSelectedRoot(null); setName(nextRootName('Movies', takenNames())); setPath(''); openBrowser(''); setMediaType('movies'); setAccessMode('read_only'); setApproveNewMedia(true); setMissingGraceChecks(2); setRootBaseline({ name: '', path: '', mediaType: 'movies', accessMode: 'read_only', approveNewMedia: true, missingGraceChecks: 2 }); setRootModalOpen(true); setMessage(null)
  }
  const editRoot = (root: StorageRoot) => {
    setMessage(null); setSelectedRoot(root); setName(root.name); setPath(root.resolved_root_path); openBrowser(root.resolved_root_path, { updateDraftPath: false, preservePathOnProblem: true }); setMediaType(root.media_type); setAccessMode(root.access_mode); setApproveNewMedia(root.discovery_mode === 'review'); setMissingGraceChecks(root.missing_grace_checks ?? 2); setRootBaseline({ name: root.name, path: root.resolved_root_path, mediaType: root.media_type, accessMode: root.access_mode, approveNewMedia: root.discovery_mode === 'review', missingGraceChecks: root.missing_grace_checks ?? 2 }); setRootModalOpen(true); setMessage(null)
  }
  const rootDirty = rootDraftChanged(
    { name, path, mediaType, accessMode, approveNewMedia, missingGraceChecks },
    rootBaseline,
  )
  const closeRootModal = async () => {
    if (rootDirty && !await confirm(DISCARD_CHANGES)) return
    setRootModalOpen(false); setMessage(null)
  }

  const browseTo = async (target: string, options: { updateDraftPath?: boolean; preservePathOnProblem?: boolean } = {}) => {
    const updateDraftPath = options.updateDraftPath ?? true
    const preservePathOnProblem = options.preservePathOnProblem ?? Boolean(selectedRoot)
    setBrowseBusy(true)
    try {
      const result = await storageApi.browseFilesystem(target)
      setListing(result)
      setPath((current) => rootPathAfterBrowse(current, result.current_path, Boolean(result.problem), updateDraftPath, preservePathOnProblem))
    } catch (reason) {
      setMessage(failed(reason, 'Could not read that folder.'))
    } finally { setBrowseBusy(false) }
  }

  const openBrowser = (from: string, options: { updateDraftPath?: boolean; preservePathOnProblem?: boolean } = {}) => {
    void browseTo(rootBrowseStart(from), options)
  }

  const saveRoot = async () => {
    if (!name.trim() || !path.trim()) { setMessage({ tone: 'error', text: 'A folder path and a name are required.' }); return }
    setRootBusy(true)
    try {
      const requestedDiscoveryMode = approveNewMedia ? 'review' : 'automatic'
      const saved = selectedRoot
        ? await storageApi.updateStorageRoot(selectedRoot.id, { name: name.trim(), path: path.trim(), media_type: mediaType, access_mode: accessMode, discovery_mode: requestedDiscoveryMode, missing_grace_checks: missingGraceChecks })
        : await storageApi.createStorageRoot({ name: name.trim(), path: path.trim(), media_type: mediaType, access_mode: accessMode, discovery_mode: requestedDiscoveryMode, enabled: true, missing_grace_checks: missingGraceChecks })
      // A Vite page can outlive its backend process during development. Older
      // servers ignore unknown request fields, which previously closed the
      // modal as if New Media approval had been persisted when it had not.
      if (saved.discovery_mode !== requestedDiscoveryMode) {
        throw new Error('The backend did not save the New Media setting. Restart the backend and try again.')
      }
      setRoots((items) => selectedRoot ? items.map((item) => item.id === saved.id ? saved : item) : [...items, saved])
      setMappingRootId((value) => value || saved.id); setRootBaseline(null); setRootModalOpen(false)
      if (selectedRoot) {
        const scanIdentityWasReset = Boolean(selectedRoot.last_scan_at && !saved.last_scan_at)
        setMessage(ok(scanIdentityWasReset
          ? `${saved.name} updated. Scan it now to initialize the new ${mediaType === selectedRoot.media_type ? 'folder' : 'media type'}.`
          : `${saved.name} updated.`))
      } else {
        // Scan straight away: a root that has never been scanned does nothing,
        // and leaving that as a second step people had to discover was the
        // main reason adding one felt like it had not worked.
        if (!tmdbReady) {
          setMessage(ok(`${saved.name} added. Configure TMDB, then scan it.`))
        } else {
          try {
            await storageApi.startScan(saved.id)
            await loadRoots()
            setMessage(ok(`${saved.name} added. Scanning it now.`))
          } catch {
            setMessage(ok(`${saved.name} added, but the scan could not be started.`))
          }
        }
      }
    } catch (reason) {
      const action = selectedRoot ? 'update' : 'add'
      const detail = reason instanceof Error ? reason.message : 'The request failed.'
      setMessage({ tone: 'error', text: `Could not ${action} media folder: ${detail}` })
    }
    finally { setRootBusy(false) }
  }
  const scanRoot = async (root: StorageRoot) => {
    setRootBusy(true)
    try {
      await storageApi.startScan(root.id)
      await loadRoots()
      setMessage(ok(`${root.name} is scanning now.`))
    } catch (reason) {
      setMessage(failed(reason, `Could not scan ${root.name}.`))
    } finally { setRootBusy(false) }
  }

  const mediaNoun = (root: StorageRoot) =>
    root.media_type === 'shows'
      ? (root.media_count === 1 ? 'show' : 'shows')
      : (root.media_count === 1 ? 'movie' : 'movies')

  const beginRemoveRoot = (root: StorageRoot) => { setRemoving(root); setRemoveEntries(false) }

  const removeRoot = async () => {
    const root = removing
    if (!root) return
    setRootBusy(true)
      try {
        await storageApi.deleteStorageRoot(root.id, removeEntries)
        if (removeEntries) window.dispatchEvent(new Event('medialogue-new-media-changed'))
        setRoots((items) => items.filter((item) => item.id !== root.id))
      setRemoving(null)
      setRootModalOpen(false)
      setSelectedRoot(null)
      setSummary(await storageApi.librarySummary())
      setMessage(ok(removeEntries
        ? `${root.name} removed, along with ${root.media_count} ${mediaNoun(root)}.`
        : `${root.name} removed. Its ${root.media_count} ${mediaNoun(root)} ${root.media_count === 1 ? 'is' : 'are'} still in your library.`))
    } catch (reason) {
      setMessage(failed(reason, 'Could not remove this media folder.'))
    } finally { setRootBusy(false) }
  }
  const beginAddMapping = () => {
    setMessage(null); setSelectedMapping(null); setMappingName('qBittorrent path'); setMappingClientId(''); setMappingEnabled(true); setRemotePrefix('/downloads'); setLocalPrefix('/media'); setMappingRootId(roots[0]?.id ?? ''); setMappingDirty(false); setAddingMapping(true); setMessage(null)
  }
  const editMapping = (mapping: RemotePathMapping) => {
    setMessage(null); setSelectedMapping(mapping); setMappingName(mapping.name); setMappingClientId(mapping.integration_id ?? ''); setMappingEnabled(mapping.enabled); setRemotePrefix(mapping.remote_prefix); setLocalPrefix(mapping.local_prefix); setMappingRootId(mapping.storage_root_id ?? ''); setMappingDirty(false); setAddingMapping(true); setMessage(null)
  }
  const closeMapping = async () => {
    if (mappingDirty && !await confirm(DISCARD_CHANGES)) return
    setAddingMapping(false); setMessage(null)
  }
  const updateMappingDraft = (update: () => void) => { update(); setMappingDirty(true) }
  const saveMapping = async () => {
    if (!mappingName.trim() || !remotePrefix.trim() || !localPrefix.trim()) { setMessage({ tone: 'error', text: 'Name, remote prefix, and Medialogue prefix are required.' }); return }
    setMappingBusy(true)
    try {
      const payload = {
        name: mappingName.trim() || 'qBittorrent path',
        integration_type: 'qbittorrent' as const,
        integration_id: mappingClientId || null,
        remote_prefix: remotePrefix.trim(),
        local_prefix: localPrefix.trim(),
        storage_root_id: mappingRootId || null,
        enabled: mappingEnabled,
      }
      const saved = selectedMapping
        ? await integrationsApi.updateRemotePathMapping(selectedMapping.id, payload)
        : await integrationsApi.createRemotePathMapping({ ...payload, integration_id: mappingClientId || undefined, storage_root_id: mappingRootId || undefined })
      setMappings((items) => selectedMapping ? items.map((item) => item.id === saved.id ? saved : item) : [...items, saved])
      setMappingDirty(false); setAddingMapping(false); setMessage(ok(selectedMapping ? `${saved.name} updated.` : 'Remote path mapping added. Recheck the affected Problem after the next qBittorrent observation.'))
    } catch (reason) { setMessage(failed(reason, `Could not ${selectedMapping ? 'update' : 'add'} remote path mapping.`)) }
    finally { setMappingBusy(false) }
  }
  const removeMapping = async (mapping: RemotePathMapping) => {
    if (!await confirm({
      title: `Remove "${mapping.name}"?`,
      body: 'Paths reported by qBittorrent will no longer be rewritten by this mapping.',
      confirmLabel: 'Remove mapping',
    })) return
    setMappingBusy(true)
    try { await integrationsApi.deleteRemotePathMapping(mapping.id); setMappings((items) => items.filter((item) => item.id !== mapping.id)); setAddingMapping(false); setSelectedMapping(null); setMessage(ok(`${mapping.name} removed.`)) }
    catch (reason) { setMessage(failed(reason, 'Could not remove remote path mapping.')) }
    finally { setMappingBusy(false) }
  }

  function dragOverRoot(index: number) {
    if (dragIndex === null || dragIndex === index) return
    setRoots((items) => moveItem(items, dragIndex, index))
    setDragIndex(index)
  }

  async function commitRootOrder() {
    const wasDragging = dragIndex !== null
    setDragIndex(null)
    if (!wasDragging) return
    const ids = roots.map((root) => root.id)
    try { setRoots(await storageApi.reorderStorageRoots(ids)) }
    // The list already shows the new order, so a failed save must put the
    // stored order back rather than leave the two disagreeing.
    catch { setRoots(await storageApi.storageRoots().catch(() => roots)) }
  }

  const liveSelectedRoot = selectedRoot ? roots.find((root) => root.id === selectedRoot.id) ?? selectedRoot : null

  return <>
    <SectionHead title="Media Folders"
      description="Where your media already lives. Medialogue only reads these folders, nothing is ever moved or renamed. Scan one to find what is inside it."
      action={<Button variant="primary" icon="plus" onClick={beginAddRoot}>Add media folder</Button>} />
    {!tmdbReady && <div className="settings-note error-note">
      <Icon name="alert" size={15} />
      <span>Scanning needs TMDB. Medialogue cannot identify what it finds without it, so scans will not start. <Link to="/settings?tab=TMDB">Configure TMDB</Link></span>
    </div>}
    {summary && summary.unlinked_directories > 0 && <div className="settings-note">
      <Icon name="alert" size={15} />
      <span>
        {summary.unlinked_directories} folder{summary.unlinked_directories === 1 ? '' : 's'} {summary.unlinked_directories === 1 ? 'is' : 'are'} no longer inside a configured media folder, usually because one was removed.
        {' '}Their movies and shows stay in your library but are not scanned or checked. Add the folder back to reconnect them.
      </span>
    </div>}
    <div className="root-list">
      {roots.map((root, index) => {
        const scanned = Boolean(root.last_scan_at)
        const health = root.last_health ?? 'unknown'
        const offline = scanned && health === 'unavailable'
        // A reachable folder can still have media that disappeared from disk.
        // Without this the row reads as fully healthy while files are gone.
        const missing = root.missing_media_count ?? 0
        const state = root.scanning ? { tone: 'blue' as const, text: 'Scanning…' }
          : !root.enabled ? { tone: 'neutral' as const, text: 'Off' }
          : offline ? { tone: 'red' as const, text: `Path not reachable${root.affected_media_count ? ` · ${root.affected_media_count} affected` : ''}` }
          : !scanned ? { tone: 'amber' as const, text: 'Never scanned' }
          : missing ? { tone: 'amber' as const, text: `${missing} ${missing === 1 ? 'item' : 'items'} missing` }
          : { tone: 'green' as const, text: `Scanned ${formatAge(root.last_scan_at)}` }
        return <button type="button" className={`root-row ${root.enabled ? '' : 'root-row-disabled'} ${dragIndex === index ? 'root-row-dragging' : ''}`} key={root.id} onClick={() => editRoot(root)}
          draggable
          onDragStart={(event) => { event.dataTransfer.effectAllowed = 'move'; setDragIndex(index) }}
          onDragOver={(event) => { event.preventDefault(); dragOverRoot(index) }}
          onDragEnd={() => void commitRootOrder()}
          onDrop={(event) => { event.preventDefault(); void commitRootOrder() }}>
          <span className="root-row-grip" aria-hidden="true"><Icon name="menu" size={14} /></span>

          <Tooltip asChild content={root.media_type === 'movies' ? 'Movies folder' : 'Shows folder'}>
            <span className={`root-row-icon kind-${root.media_type === 'movies' ? 'movie' : 'show'} ${offline ? 'bad' : ''}`}>
              <Icon name={root.media_type === 'movies' ? 'clapper' : 'tv-screen'} size={18} />
            </span>
          </Tooltip>
          <span className="root-row-copy">
            <span className="root-row-title">
              <strong>{root.name}</strong>
            </span>
            <code>{root.resolved_root_path}</code>
          </span>
          <span className="root-row-end">
            {root.watch_state === 'watching' && <Tooltip asChild content="Watched live: folders added, changed or deleted here show up within a minute."><span className="root-row-watch"><Icon name="eye" size={14} /></span></Tooltip>}
            {root.watch_state === 'unavailable' && <Tooltip asChild content="Live watching is not available for this folder, so changes show up at the next scheduled scan."><span className="root-row-watch unwatched"><Icon name="eye-off" size={14} /></span></Tooltip>}
            {root.access_mode === 'read_write' && <Tooltip asChild content="Read/write. Medialogue is allowed to delete inside this folder."><span className="root-row-write"><Icon name="unlock" size={14} /></span></Tooltip>}
            {root.scanning
              ? <Tooltip asChild content={describeScan(root.scan)}><span className={`root-row-state tone-${state.tone}`}><i /><span>{state.text}</span></span></Tooltip>
              : <span className={`root-row-state tone-${state.tone}`}><i /><span>{state.text}</span></span>}
            <span className="root-row-go"><Icon name="chevron" size={16} /></span>
          </span>
        </button>
      })}
    </div>
    {!roots.length && <p className="provider-empty-copy">No media folders yet. Add the folder where your media already lives, as Medialogue sees it, which inside a container is the container’s path.</p>}

    {rootModalOpen && <Modal wide className="modal-tall" icon="folder" title={selectedRoot ? selectedRoot.name : 'Add media folder'} onClose={closeRootModal}
      footer={<>
      {selectedRoot && <Button variant="danger" onClick={() => beginRemoveRoot(selectedRoot)} disabled={rootBusy}>Remove</Button>}
      {liveSelectedRoot && <Button variant="ghost" icon="refresh" onClick={() => void scanRoot(liveSelectedRoot)} disabled={rootBusy || rootDirty || !liveSelectedRoot.enabled || liveSelectedRoot.scanning || !tmdbReady}>{liveSelectedRoot.scanning ? 'Scanning…' : 'Scan now'}</Button>}
      <span className="modal-foot-spacer" />
      <Button variant="ghost" onClick={closeRootModal} disabled={rootBusy}>Cancel</Button>
      <Button variant="primary" onClick={() => void saveRoot()} disabled={rootBusy || !path.trim() || Boolean(selectedRoot && !rootDirty)}>{rootBusy ? 'Saving…' : selectedRoot ? 'Save media folder' : 'Add media folder'}</Button>
    </>}>
      <div className="provider-modal-editor">
        <div className="root-browser">
          <div className="root-browser-bar">
            <button type="button" onClick={() => void browseTo(listing?.parent ?? '')} disabled={browseBusy || listing?.parent == null}>
              <Icon name="arrow" size={13} />Back
            </button>
            <code>{listing?.current_path || 'This computer'}</code>
          </div>
          <div className="root-browser-list">
            {browseBusy && <p className="root-browser-note">Reading…</p>}
            {!browseBusy && listing?.problem && <p className="root-browser-note">{listing.problem}</p>}
            {!browseBusy && !listing?.problem && !listing?.directories.length && <p className="root-browser-note">No folders here.</p>}
            {!browseBusy && listing?.directories.map((entry) => <button type="button" key={entry.path} onClick={() => void browseTo(entry.path)}>
              <Icon name="folder" size={14} />{entry.name}
            </button>)}
          </div>
        </div>

        <div className={`settings-form scope-${mediaType}`}>
          <SettingsGroup legend="Folder">
            <Field label="Name" help="Shown in Medialogue only.">
              <Input value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
            <Field label="Media type" help="Which library the files found here belong to.">
              <Segmented
                ariaLabel="Media type"
                value={mediaType}
                onChange={changeMediaType}
                options={[{ value: 'movies', label: 'Movies', tone: 'movie' }, { value: 'shows', label: 'Shows', tone: 'show' }]}
              />
            </Field>
            <Field label="Access" help="Read-only is recommended. Read/write allows deletion.">
              <Segmented
                ariaLabel="Access"
                value={accessMode}
                onChange={setAccessMode}
                options={[{ value: 'read_only', label: 'Read-only' }, { value: 'read_write', label: 'Read/write' }]}
              />
            </Field>
          </SettingsGroup>
          <SettingsGroup legend="Scanning">
            <Field label="New media" help={approveNewMedia
              ? 'Newly detected titles wait in New Media until you approve them.'
              : 'Newly detected titles are added to your library as soon as a scan finds them.'}>
              <Segmented
                ariaLabel="New media"
                value={approveNewMedia ? 'review' : 'auto'}
                onChange={(mode) => setApproveNewMedia(mode === 'review')}
                options={[{ value: 'review', label: 'Approve before adding' }, { value: 'auto', label: 'Add automatically' }]}
              />
            </Field>
            <Field label="Missing grace checks" help="How many scans in a row must miss a folder before its media counts as missing.">
              <Slider
                ariaLabel="Missing grace checks"
                min={1}
                max={5}
                value={missingGraceChecks}
                onChange={setMissingGraceChecks}
                format={(value) => `${value} ${value === 1 ? 'scan' : 'scans'}`}
              />
            </Field>
          </SettingsGroup>
        </div>
        <Note message={message} />
      </div>
    </Modal>}

    {removing && <Modal
      icon="folder"
      title={`Remove ${removing.name}?`}
      onClose={() => setRemoving(null)}
      footer={<>
        <span className="footer-state clean">Files on disk are never touched.</span>
        <Button variant="ghost" onClick={() => setRemoving(null)} disabled={rootBusy}>Cancel</Button>
        <Button variant="danger" onClick={() => void removeRoot()} disabled={rootBusy}>{rootBusy ? 'Removing…' : 'Remove'}</Button>
      </>}
    >
      <div className="root-remove">
        <code>{removing.resolved_root_path}</code>
        <p>
          Medialogue will stop scanning this folder, and will not download anything into it.
          {removing.media_count > 0 && <> The {removing.media_count} {mediaNoun(removing)} it found here stay in your library, but nothing will keep them up to date until you add this folder back.</>}
        </p>
        {removing.media_count > 0 && <label className="root-remove-option">
          <input type="checkbox" checked={removeEntries} onChange={(event) => setRemoveEntries(event.target.checked)} />
          <span>
            <strong>Also remove {removing.media_count === 1 ? 'the' : `all ${removing.media_count}`} {mediaNoun(removing)} in this folder from the library</strong>
            <small>This deletes what Medialogue knows about {removing.media_count === 1 ? 'it' : 'them'}, whether {removing.media_count === 1 ? 'it is' : 'they are'} monitored, which quality profile {removing.media_count === 1 ? 'it uses' : 'they use'}, and any tags. The files stay on disk, so scanning this folder later would add {removing.media_count === 1 ? 'it' : 'them'} back as {removing.media_count === 1 ? 'a new entry' : 'new entries'}, starting from scratch.</small>
          </span>
        </label>}
      </div>
    </Modal>}

    <SectionHead title="Remote Path Mappings" divided
      description="Only needed when qBittorrent reports a path that does not exist from Medialogue's point of view, typically a different container, or a different host entirely."
      action={<Button variant="primary" icon="plus" onClick={beginAddMapping}>Add mapping</Button>} />
    {/* Same row shape as Media Folders: these are two lists of paths in one
        section, and rendering one as cards and the other as rows made them look
        like unrelated features. */}
    <div className="root-list">
      {mappings.map((mapping) => {
        const client = clients.find((item) => item.id === mapping.integration_id)
        const root = roots.find((item) => item.id === mapping.storage_root_id)
        return <button type="button" className={`root-row root-row-static ${mapping.enabled ? '' : 'root-row-disabled'}`} key={mapping.id} onClick={() => editMapping(mapping)}>
          <span className="root-row-icon"><Icon name="arrow" size={18} /></span>
          <span className="root-row-copy">
            <span className="root-row-title"><strong>{mapping.name}</strong></span>
            <code>{mapping.remote_prefix} → {mapping.local_prefix}</code>
          </span>
          <span className="root-row-end">
            <span className="root-row-scope">{client?.name ?? 'All qBittorrent clients'} · {root?.name ?? 'Any media folder'}</span>
            <span className={`root-row-state tone-${mapping.enabled ? 'green' : 'neutral'}`}><i /><span>{mapping.enabled ? 'On' : 'Off'}</span></span>
            <span className="root-row-go"><Icon name="chevron" size={16} /></span>
          </span>
        </button>
      })}
    </div>
    {!mappings.length && <p className="provider-empty-copy">No mappings configured. Only add one when qBittorrent reports a path that differs from the path visible inside Medialogue.</p>}

    <IgnoredFolders />

    {addingMapping && <Modal wide title={selectedMapping ? selectedMapping.name : 'Add remote path mapping'} onClose={closeMapping}
      titleAction={<EnabledButton enabled={mappingEnabled} onChange={(next) => updateMappingDraft(() => setMappingEnabled(next))} />}
      footer={<>
      {selectedMapping && <Button variant="danger" onClick={() => void removeMapping(selectedMapping)} disabled={mappingBusy}>Delete</Button>}
      <span className="modal-foot-spacer" />
      <Button variant="ghost" onClick={closeMapping} disabled={mappingBusy}>Cancel</Button>
      <Button variant="primary" onClick={() => void saveMapping()} disabled={mappingBusy || Boolean(selectedMapping && !mappingDirty)}>{mappingBusy ? 'Saving…' : selectedMapping ? 'Save mapping' : 'Add mapping'}</Button>
    </>}>
      <div className="provider-modal-editor">
        <div className="settings-form">
          <SettingsGroup legend="Mapping">
            <Field label="Name" help="Shown in Medialogue only.">
              <Input value={mappingName} onChange={(event) => updateMappingDraft(() => setMappingName(event.target.value))} />
            </Field>
            <Field label="qBittorrent client" help="Which client's reported paths this mapping rewrites. Leave as all clients when every instance uses the same layout.">
              <Select value={mappingClientId} onChange={(event) => updateMappingDraft(() => setMappingClientId(event.target.value))}><option value="">All qBittorrent clients</option>{clients.map((client) => <option value={client.id} key={client.id}>{client.name}</option>)}</Select>
            </Field>
          </SettingsGroup>
          <SettingsGroup legend="Paths">
            <Field label="Remote prefix" help="The path prefix exactly as qBittorrent reports it. Copy it from a download's save path.">
              <Input value={remotePrefix} onChange={(event) => updateMappingDraft(() => setRemotePrefix(event.target.value))} placeholder="/downloads/movies" />
            </Field>
            <Field label="Medialogue prefix" help="The equivalent path from Medialogue's point of view.">
              <Input value={localPrefix} onChange={(event) => updateMappingDraft(() => setLocalPrefix(event.target.value))} placeholder="/media/movies" />
            </Field>
            <Field label="Media folder" badge="optional" help="Limit the rewritten path to one media folder, or leave unset to try them all.">
              <Select value={mappingRootId} onChange={(event) => updateMappingDraft(() => setMappingRootId(event.target.value))}><option value="">No specific media folder</option>{roots.map((root) => <option value={root.id} key={root.id}>{root.name} · {root.resolved_root_path}</option>)}</Select>
            </Field>
          </SettingsGroup>
        </div>
        <Note message={message} />
      </div>
    </Modal>}

    {!rootModalOpen && !addingMapping && !removing && <Note message={message} />}

  </>
}
