import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ageValue, formatAge, formatUntil } from '../lib/age'
import { isGeneratedRootName, nextRootName } from '../lib/rootName'
import { moveItem } from '../lib/reorder'
import { rootBrowseStart, rootDraftChanged, rootPathAfterBrowse } from '../lib/rootDraft'
import type { RootDraft } from '../lib/rootDraft'
import { describeScan } from '../lib/scanProgress'
import { Link } from 'react-router-dom'
import { Field, Note, SaveFooter, SectionHead, Secret, SettingsGroup, failed, ok, pending } from '../components/settings'
import type { Message, StatusTone } from '../components/settings'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { useConfirm } from '../components/Confirm'
import { Modal } from '../components/Modal'
import { Badge, Button, EmptyState, EnabledButton, Input, Segmented, Select, Slider } from '../components/ui'
import { authApi } from '../api/modules/auth'
import { integrationsApi } from '../api/modules/integrations'
import { storageApi } from '../api/modules/storage'
import { useUrlState } from '../lib/urlState'
import type { DownloadClient, Indexer, IndexerScope, LibrarySummary, FilesystemListing, StorageRoot, RemotePathMapping } from '../types'
import prowlarrIcon from '../assets/prowlarr.svg'
import jackettIcon from '../assets/jackett.svg'
import { Page, formatEvidenceDate, indexerBadgePreview, useLiveLibraryRefresh } from './shared'

const DISCARD_CHANGES = { title: 'Discard unsaved changes?', confirmLabel: 'Discard' } as const

const settingsTabItems: Array<{ name: string; icon: Parameters<typeof Icon>[0]['name'] }> = [
  { name: 'Media', icon: 'folder' },
  { name: 'qBittorrent', icon: 'qbittorrent' },
  { name: 'TMDB', icon: 'tmdb' },
  { name: 'Plex', icon: 'plex' },
  { name: 'Indexers', icon: 'indexers' },
  { name: 'Security', icon: 'security' },
]

const settingsTabs = settingsTabItems.map((item) => item.name)

export function SettingsPage() {
  const [tab, setTab] = useUrlState('tab', 'Media')
  const current = settingsTabs.includes(tab) ? tab : 'Media'
  return <Page title="Settings" subtitle="Connections, library paths, and system administration.">
    <div className="split split-narrow">
      <nav className="settings-nav">{settingsTabItems.map((item) => <button
        className={current === item.name ? 'active' : ''}
        onClick={() => setTab(item.name)}
        key={item.name}
      ><Icon name={item.icon} size={16} />{item.name}</button>)}</nav>
      <section className="panel settings-panel">
        {current === 'Media' ? <StorageSettings />
          : current === 'TMDB' ? <MetadataSettings />
          : current === 'Plex' ? <PlexSettings />
          : current === 'qBittorrent' ? <QBittorrentSettings />
          : current === 'Indexers' ? <IndexerSettings />
          : <SecuritySettings />}
      </section>
    </div>
  </Page>
}

function SecuritySettings() {
  const [security, setSecurity] = useState<{ default_password_warning: boolean; session_expires_at?: string } | null>(null)
  const [sessions, setSessions] = useState<Array<{ id: string; created_at: string; expires_at: string; last_seen_at: string; current: boolean }>>([])
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState<Message>(null)
  const [busy, setBusy] = useState(false)
  const loadSessions = async () => {
    try { setSessions(await authApi.sessions()) }
    catch (reason) { setMessage(failed(reason, 'Could not load active sessions.')) }
  }
  useEffect(() => {
    authApi.security().then(setSecurity).catch((reason) => setMessage(failed(reason, 'Could not load security state.')))
    void loadSessions()
  }, [])
  const change = async () => {
    if (newPassword.length < 12) { setMessage({ tone: 'error', text: 'New password must contain at least 12 characters.' }); return }
    if (newPassword !== confirmPassword) { setMessage({ tone: 'error', text: 'New password confirmation does not match.' }); return }
    setBusy(true); setMessage(pending('Changing password…'))
    try { await authApi.changePassword(currentPassword, newPassword); setCurrentPassword(''); setNewPassword(''); setConfirmPassword(''); setSecurity((value) => value ? { ...value, default_password_warning: false } : value); await loadSessions(); setMessage(ok('Password changed. Other active sessions were revoked.')) }
    catch (reason) { setMessage(failed(reason, 'Could not change password.')) }
    finally { setBusy(false) }
  }
  const revokeSession = async (sessionId: string) => {
    setBusy(true); setMessage(pending('Revoking session…'))
    try { await authApi.revokeSession(sessionId); await loadSessions(); setMessage(ok('Session revoked.')) }
    catch (reason) { setMessage(failed(reason, 'Could not revoke session.')) }
    finally { setBusy(false) }
  }
  const revokeOthers = async () => {
    setBusy(true); setMessage(pending('Revoking other sessions…'))
    try { await authApi.revokeOtherSessions(); await loadSessions(); setMessage(ok('Other sessions revoked.')) }
    catch (reason) { setMessage(failed(reason, 'Could not revoke other sessions.')) }
    finally { setBusy(false) }
  }
  const tooShort = newPassword.length > 0 && newPassword.length < 12
  const mismatch = confirmPassword.length > 0 && newPassword !== confirmPassword
  const otherSessions = sessions.filter((session) => !session.current)
  const orderedSessions = [...sessions].sort((a, b) => Number(b.current) - Number(a.current) || ageValue(b.created_at) - ageValue(a.created_at))
  return <>
    <SectionHead title="Administrator Security"
      description="Medialogue uses a single local administrator account and secure session cookies. There is no self-service recovery, a lost password is reset from the server."
      status={security?.default_password_warning ? 'Default password active' : 'Password changed'}
      statusTone={security?.default_password_warning ? 'amber' : 'green'}
      detail={security?.default_password_warning ? 'change this before exposing the host' : undefined}
      detailTone={security?.default_password_warning ? 'err' : undefined} />
    <div className="settings-form">
      <SettingsGroup legend="Change password">
        <Field label="Current password" help="Required to prove this session belongs to you.">
          <Secret value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" />
        </Field>
        <Field label="New password" error={tooShort ? 'Must contain at least 12 characters.' : undefined}
          help={<><strong>At least 12 characters.</strong> Length matters more than symbols, a passphrase of four uncommon words beats a short complex string.</>}>
          <Secret value={newPassword} onChange={(event) => setNewPassword(event.target.value)} aria-invalid={tooShort} />
        </Field>
        <Field label="Confirm new password" error={mismatch ? 'Does not match the new password.' : undefined} help="Must match exactly.">
          <Secret value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} aria-invalid={mismatch} />
        </Field>
      </SettingsGroup>
    </div>
    <div className="settings-note"><Icon name="lock" size={15} /><span>Changing the password revokes every other active session immediately. You stay signed in on this device.</span></div>
    <Note message={message} />
    <div className="settings-footer">
      <Button variant="primary" onClick={() => void change()} disabled={busy || !currentPassword || tooShort || mismatch || !newPassword}>{busy ? 'Changing…' : 'Change password'}</Button>
    </div>
    <SectionHead title="Active Sessions" divided
      description="Every signed-in browser holds its own session cookie. Revoke any you no longer recognise. The tokens themselves are opaque and never shown here."
      status={sessions.length ? `${sessions.length} signed in` : undefined}
      statusTone={otherSessions.length ? 'amber' : 'green'}
      action={otherSessions.length > 0 && <Button variant="secondary" icon="logout" onClick={() => void revokeOthers()} disabled={busy}>
        Revoke {otherSessions.length} other{otherSessions.length === 1 ? '' : 's'}
      </Button>} />
    <div className="session-list">
      {/* Sorted so the browser you are reading this in is always the first row,
          and the rest fall in order of how recently they were used. */}
      {orderedSessions.map((session) => {
        const expired = Date.parse(session.expires_at) <= Date.now()
        return <div className={`session-row ${session.current ? 'session-row-current' : ''}`} key={session.id}>
          <span className="session-row-icon"><Icon name={session.current ? 'check' : 'security'} size={17} /></span>
          <span className="session-row-copy">
            <span className="session-row-title">
              <strong>{session.current ? 'This browser' : 'Other browser'}</strong>
              {session.current && <Badge tone="green">Current</Badge>}
              {expired && <Badge tone="red">Expired</Badge>}
            </span>
            {/* No "last used": the server freezes last_seen_at at creation so
                authenticated reads stay write-free, so it only ever repeats the
                sign-in time. */}
            <span className="session-row-facts">
              <span>Signed in {formatAge(session.created_at)}</span>
              <span>Expires {formatUntil(session.expires_at)}</span>
            </span>
          </span>
          {!session.current && <Button variant="ghost" onClick={() => void revokeSession(session.id)} disabled={busy}>Revoke</Button>}
        </div>
      })}
      {!sessions.length && <p className="provider-empty-copy">No active sessions were returned. Reload the page if you are still signed in.</p>}
    </div>
  </>
}

function StorageSettings() {
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
            <Field label="Missing grace checks" help="How many scans in a row must miss a file before it counts as missing.">
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

function MetadataSettings() {
  const [configuration, setConfiguration] = useState<{ configured: boolean; api_key_configured: boolean; enabled: boolean; health: string; latency_ms?: number; last_error?: string; revision?: number } | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<Message>(null)

  const load = () => integrationsApi.tmdbConfiguration().then((value) => { setConfiguration(value); setEnabled(value.enabled); setApiKey(''); setDirty(false) })
  useEffect(() => { load().catch((reason) => setMessage(failed(reason, 'Could not load TMDB settings.'))) }, [])

  const save = async () => {
    setSaving(true); setMessage(pending('Saving TMDB settings…'))
    try {
      const value = await integrationsApi.saveTmdb({ api_key: apiKey || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setApiKey(''); setDirty(false); setMessage(ok('TMDB settings saved.'))
    } catch (reason) { setMessage(failed(reason, 'Could not save TMDB settings.')) } finally { setSaving(false) }
  }

  const healthTone: StatusTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : 'neutral'
  return <>
    <SectionHead title="TMDB"
      description="Identity source for newly discovered titles. When off, discoveries stay unmatched."
      status={!configuration?.configured ? 'Not configured' : configuration.health} statusTone={healthTone}
      action={<EnabledButton enabled={enabled} onChange={(next) => { setEnabled(next); setDirty(true) }} />} />
    <div className="settings-form">
      <SettingsGroup>
        <Field label="API key"
          help={<>Your TMDB API Read Access Token. Held server-side. {configuration?.api_key_configured ? 'Leave blank to keep the current one.' : 'Required for automatic matching.'}</>}>
          <Secret value={apiKey} onChange={(event) => { setApiKey(event.target.value); setDirty(true) }} placeholder={configuration?.api_key_configured ? 'Stored server-side · leave blank to preserve' : 'Required for automatic matching'} />
        </Field>
      </SettingsGroup>
    </div>
    {configuration?.last_error && <Note message={{ tone: 'error', text: configuration.last_error }} />}
    <Note message={message} />
    <SaveFooter dirty={dirty} saving={saving} onSave={() => void save()} />
  </>
}

function PlexSettings() {
  const [configuration, setConfiguration] = useState<import('../types').PlexConfiguration | null>(null)
  const [url, setUrl] = useState('http://plex:32400')
  const [token, setToken] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [message, setMessage] = useState<Message>(null)
  const [working, setWorking] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = () => integrationsApi.plexConfiguration().then((value) => {
    setConfiguration(value)
    if (value.url) setUrl(value.url)
    setEnabled(value.enabled)
    setToken(''); setDirty(false)
  })
  useEffect(() => { load().catch((reason) => setMessage(failed(reason, 'Could not load Plex settings.'))).finally(() => setLoading(false)) }, [])

  const save = async () => {
    if (!url.trim()) { setMessage({ tone: 'error', text: 'Enter a Plex server URL.' }); return }
    setWorking(true); setMessage(pending('Saving Plex settings…'))
    try {
      const value = await integrationsApi.savePlex({ url: url.trim(), token: token || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setToken(''); setDirty(false); setMessage(ok('Plex settings saved.'))
      window.dispatchEvent(new Event('medialogue-plex-configuration-changed'))
    } catch (reason) { setMessage(failed(reason, 'Could not save Plex settings.')) }
    finally { setWorking(false) }
  }
  const syncLibrary = async () => {
    setWorking(true); setMessage(pending('Starting Plex library verification…'))
    try {
      const result = await integrationsApi.syncPlexLibrary()
      setMessage(ok(`Plex verification queued as job ${result.job_id.slice(0, 8)}…. It reads Plex's existing library; it does not trigger a Plex scan.`))
    } catch (reason) { setMessage(failed(reason, 'Could not start Plex verification.')) }
    finally { setWorking(false) }
  }

  const healthTone: StatusTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : 'neutral'
  const live = configuration?.configured && configuration.enabled
  return <>
    <SectionHead title="Plex"
      description="Read-only verification. Nothing inside Plex is ever modified."
      status={loading ? 'Loading' : !configuration?.configured ? 'Not configured' : configuration.health} statusTone={healthTone}
      action={<EnabledButton enabled={enabled} onChange={(next) => { setEnabled(next); setDirty(true) }} />} />
    <div className="settings-form">
      <SettingsGroup>
        <Field label="URL" help={<>Plex Media Server address, including port. Example: <code>http://plex:32400</code></>}>
          <Input placeholder="http://plex:32400" value={url} onChange={(event) => { setUrl(event.target.value); setDirty(true) }} />
        </Field>
        <Field label="API token"
          help={<>Your <code>X-Plex-Token</code>. Held server-side. {configuration?.token_configured ? 'Leave blank to keep the current one.' : 'Required.'}</>}>
          <Secret placeholder={configuration?.token_configured ? 'Stored server-side · leave blank to preserve' : 'Required'} value={token} onChange={(event) => { setToken(event.target.value); setDirty(true) }} />
        </Field>
      </SettingsGroup>
    </div>
    {configuration?.last_error && <Note message={{ tone: 'error', text: configuration.last_error }} />}
    <Note message={message} />
    <SaveFooter dirty={dirty} saving={working} onSave={() => void save()}>
      {live && <span className="muted">Health is checked automatically every minute.</span>}
      {live && <Button variant="secondary" onClick={() => void syncLibrary()} disabled={working}>Sync library verification</Button>}
    </SaveFooter>
  </>
}

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

function QBittorrentSettings() {
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
  priority: number
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
  priority: 25,
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
    priority: indexer.priority,
    color: indexer.color ?? '',
    multiLanguages: indexer.multiLanguages.join(', '),
    downloadClientIds: [...indexer.downloadClientIds],
  }
}

function IndexerSettings() {
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
        priority: draft.priority,
        color: draft.color,
        multi_languages: draft.multiLanguages.split(',').map((value) => value.trim()).filter(Boolean),
        download_client_ids: draft.downloadClientIds,
      }
      const value = selectedId
        ? await integrationsApi.updateIndexer(selectedId, { ...base, ...(draft.apiKey.trim() ? { api_key: draft.apiKey.trim() } : {}), expected_revision: selected?.revision })
        : await integrationsApi.createIndexer({ ...base, color: draft.color || undefined, api_key: draft.apiKey.trim() })
      setIndexers((items) => selectedId ? items.map((item) => item.id === value.id ? value : item) : [...items, value].sort((a, b) => a.name.localeCompare(b.name)))
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
    <SectionHead title="Indexers" description="Torznab endpoints used for interactive Movie and Show searches." action={<div className="button-row"><Button variant="secondary" onClick={() => void testAll()} disabled={busy || !indexers.length}>{busy ? 'Testing…' : 'Test all'}</Button><Button variant="primary" icon="plus" onClick={beginAdd}>Add indexer</Button></div>} />
    {error && !editorOpen && <Note message={{ tone: 'error', text: error }} />}
    {message && !editorOpen && <Note message={{ tone: 'ok', text: message }} />}
    {indexers.length > 0 && <div className="provider-card-grid">
      {indexers.map((indexer) => { const source = presetFromUrl(indexer.torznabUrl); return <article className={`provider-card ${indexer.enabled ? '' : 'provider-card-disabled'}`} role="button" tabIndex={0} key={indexer.id} onClick={() => selectIndexer(indexer.id)} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectIndexer(indexer.id) } }}>
        <div className="provider-card-head"><div className={`provider-card-icon indexer-source-icon indexer-source-${source}`} style={indexerBadgePreview(indexer.color ?? '')}><img src={indexerPresets[source].icon} alt={`${indexerPresets[source].name} icon`} /></div><button type="button" className={`enabled-button cf-card-toggle ${indexer.enabled ? 'on' : 'off'}`} aria-pressed={indexer.enabled} aria-label={`Turn ${indexer.name} ${indexer.enabled ? 'off' : 'on'}`} disabled={togglingId === indexer.id} onClick={(event) => { event.stopPropagation(); void toggleIndexer(indexer) }}>{togglingId === indexer.id ? 'Saving…' : indexer.enabled ? 'On' : 'Off'}</button></div>
        <strong>{indexer.name}</strong><span>{indexer.torznabUrl}</span>
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
            <Field label="Indexer priority" help="Tie-breaker from 1 (highest) to 50 (lowest). All are still searched.">
              <Input type="number" min="1" max="50" value={String(draft.priority)} onChange={(event) => updateDraft('priority', Math.min(50, Math.max(1, Number(event.target.value) || 25)))} />
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
