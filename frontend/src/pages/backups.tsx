import { useEffect, useState } from 'react'
import { formatAge } from '../lib/age'
import { formatBytes } from '../components/ReleaseResults'
import { backupsApi } from '../api/modules/backups'
import type { BackupCoverage, CoverageGap } from '../api/modules/backups'
import { Note, failed } from '../components/settings'
import type { Message } from '../components/settings'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { Modal } from '../components/Modal'
import { Badge, Button, EmptyState, Input, Panel, Progress, Select } from '../components/ui'
import { downloadsApi } from '../api/modules/downloads'
import { systemApi } from '../api/modules/system'
import type { TorrentArchiveItem, Job, RecoveryReimportPlan } from '../types'
import { EventSortHeader, tmdbPosterUrl } from './shared'

/** A small poster for list rows, or the movie/show icon when there is none. */
function RowPoster({ reference, mediaType }: { reference?: string | null; mediaType?: string | null }) {
  return <span className="new-media-poster row-poster">{reference
    ? <img src={tmdbPosterUrl(reference)} alt="" loading="lazy" referrerPolicy="no-referrer" />
    : <Icon name={mediaType === 'shows' ? 'tv-screen' : 'clapper'} size={16} />}</span>
}

/** How sure a torrent's backup is: not archived, only here, on Drive. */
/** One row of the Torrents list: an archived torrent, or a release in the
 *  library that has none and so could not be downloaded again. */
type ArchiveRow = { key: string; torrent?: TorrentArchiveItem; gap?: CoverageGap; name: string; size?: number; archivedAt?: string; rank: number }

/** How sure a row's backup is: no torrent, not archived, only here, on Drive. */
function archiveBackupRank(item: TorrentArchiveItem) {
  return !item.archiveVerified ? 0 : item.includedInRemoteBackup ? 2 : 1
}

function archiveRows(items: TorrentArchiveItem[], noTorrent: CoverageGap[]): ArchiveRow[] {
  return [
    ...items.map((torrent) => ({ key: torrent.id, torrent, name: (torrent.mediaTitle ?? torrent.releaseName ?? torrent.torrentName).toLocaleLowerCase(),
      size: torrent.totalSize, archivedAt: torrent.archivedAt, rank: archiveBackupRank(torrent) })),
    ...noTorrent.map((gap) => ({ key: `gap:${gap.id}`, gap, name: gap.title.toLocaleLowerCase(), rank: -1 })),
  ]
}

/** The rows in the order the headers ask for ("-field" is descending). */
function sortArchive(rows: ArchiveRow[], sort: string) {
  const descending = sort.startsWith('-')
  const field = sort.replace(/^-/, '')
  const value = (row: ArchiveRow): number | string =>
    field === 'size' ? row.size ?? -1
      : field === 'archived' ? (row.archivedAt ? Date.parse(row.archivedAt) : 0)
      : field === 'backup' ? row.rank
      : row.name
  return [...rows].sort((a, b) => {
    const left = value(a), right = value(b)
    const order = typeof left === 'string' ? left.localeCompare(String(right)) : left - (right as number)
    // Ties keep a stable, readable order by title.
    return (descending ? -order : order) || a.name.localeCompare(b.name)
  })
}

/** Every archived torrent (verified locally, on Google Drive, restorable one at
 *  a time) and every library release that has no torrent. Part of Backups. */
export function TorrentArchiveSection() {
  const [items, setItems] = useState<TorrentArchiveItem[]>([])
  const [coverage, setCoverage] = useState<BackupCoverage | null>(null)
  const [query, setQuery] = useState('')
  const [archiveFilter, setArchiveFilter] = useState('all')
  // Newest archived first; a header click sorts by that column, again to flip it.
  const [archiveSort, setArchiveSort] = useState('-archived')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [health, setHealth] = useState<{ status?: string; path?: string; writable?: boolean; archived?: number; tracked?: number; missing_or_failed?: number; message?: string }>({ status: 'unknown' })
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [archive, archiveHealth, nextCoverage] = await Promise.all([downloadsApi.torrentArchive(query), downloadsApi.torrentArchiveHealth(), backupsApi.coverage()])
      setItems(archive); setHealth(archiveHealth); setCoverage(nextCoverage); setError('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load the torrent archive.') }
    finally { setLoading(false) }
  }
  useEffect(() => { const timer = window.setTimeout(() => { void load() }, 150); return () => window.clearTimeout(timer) }, [query])

  const retryArchive = async (item: TorrentArchiveItem) => {
    setBusy(true); setMessage('')
    try {
      const accepted = await downloadsApi.retryTorrentArchive(item.id)
      const [job] = await systemApi.waitForJobs([accepted.job_id])
      if (!job || job.state !== 'completed') setMessage(`Archive retry ${job?.state ?? 'failed'}. Review Tasks for details.`)
      else setMessage(`Recovery archive completed for ${item.torrentName}.`)
      await load()
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Archive retry failed.') }
    finally { setBusy(false) }
  }
  const formatSize = formatBytes
  const needle = query.trim().toLocaleLowerCase()
  const noTorrent = (coverage?.gaps ?? []).filter((gap) => gap.status === 'no_torrent'
    && (!needle || `${gap.title} ${gap.release_name}`.toLocaleLowerCase().includes(needle)))
  const rows = sortArchive(archiveRows(items, noTorrent).filter((row) => {
    const torrent = row.torrent
    return archiveFilter === 'unrestorable' ? row.rank < 2
      : archiveFilter === 'attention' ? torrent !== undefined && !torrent.archiveVerified
      : archiveFilter === 'remote' ? row.rank === 1
      : archiveFilter === 'removed' ? torrent !== undefined && !torrent.qbitPresent
      : archiveFilter === 'missing' ? row.gap !== undefined
      : true
  }), archiveSort)

  return <section className="backups-block">
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    <div className="button-row"><Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button><Select aria-label="Archive filter" value={archiveFilter} onChange={e => setArchiveFilter(e.target.value)}><option value="all">Everything</option><option value="unrestorable">Not restorable</option><option value="missing">No torrent</option><option value="attention">Not archived</option><option value="remote">Not on Drive yet</option><option value="removed">Removed from qBittorrent</option></Select><Button disabled={busy} onClick={() => void (async () => { setBusy(true); try { const result = await downloadsApi.rebuildTorrentArchive(); setMessage(`Recovered ${result.imported} archive records; ${result.skipped} already present; ${result.errors.length} could not be verified.`); await load() } catch (e) { setMessage(String(e)) } finally { setBusy(false) } })()}>Rebuild archive catalogue</Button><Button disabled={busy} onClick={() => void (async () => { for (const item of items.filter(t => !t.archiveVerified && t.qbitPresent)) await retryArchive(item) })()}>Retry incomplete archives</Button></div>
    <Panel
      className={`table-panel ${health.status === 'healthy' ? '' : 'archive-unhealthy'}`}
      title="Torrents"
      // The archive folder's health, beside the title rather than in its own banner.
      note={[
        health.status === 'healthy' ? undefined : `Archive folder ${health.status === 'unavailable' ? 'unavailable' : 'needs attention'}`,
        coverage ? `${coverage.protected.toLocaleString()} / ${coverage.total.toLocaleString()} library releases restorable` : undefined,
        `${(health.archived ?? items.filter((item) => item.archiveState === 'archived').length).toLocaleString()} archived`,
        `${(health.missing_or_failed ?? items.filter((item) => item.archiveState !== 'archived').length).toLocaleString()} not archived`,
        health.path ?? '/torrent-archive',
      ].filter(Boolean).join(' · ')}
      action={<div className="search-field compact"><Icon name="search" size={14} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter archive…" /></div>}>
      {error ? <EmptyState title="Could not load torrent archive" detail={error} /> : !loading && !items.length && !noTorrent.length ? <EmptyState icon="archive" title="No torrent recovery records yet" detail="Relevant qBittorrent torrents will be archived automatically when they are observed." /> : <table className="data-table archive-table"><thead><tr>
        <th><EventSortHeader label="Release" field="release" sort={archiveSort} onSort={setArchiveSort} /></th>
        <th className="archive-num"><EventSortHeader label="Size" field="size" sort={archiveSort} onSort={setArchiveSort} /></th>
        <th><EventSortHeader label="Archived" field="archived" sort={archiveSort} onSort={setArchiveSort} /></th>
        <th><EventSortHeader label="Backup" field="backup" sort={archiveSort} onSort={setArchiveSort} /></th>
        <th>qBittorrent</th><th />
      </tr></thead><tbody>{rows.map((row) => {
        const gap = row.gap
        if (gap) return <tr key={row.key}>
          <td><div className="row-with-poster"><RowPoster reference={gap.poster_ref} mediaType={gap.media_type} /><div className="archive-title">
            <strong>{gap.title}{gap.year ? <span> {gap.year}</span> : null}{gap.season_number != null ? <span> · Season {gap.season_number}</span> : null}</strong>
            <span className="archive-release" title={gap.release_name}>{gap.release_name}</span>
          </div></div></td>
          <td className="archive-num">-</td>
          <td className="archive-when">-</td>
          <td><Tooltip content="Medialogue never saw a torrent for this release, so it cannot be downloaded again."><Badge tone="red">No torrent</Badge></Tooltip></td>
          <td /><td />
        </tr>
        const torrent = row.torrent!
        const release = torrent.releaseName ?? torrent.torrentName
        const backup = !torrent.archiveVerified ? { tone: 'amber' as const, label: 'Not archived', help: torrent.archiveError || 'The .torrent could not be saved yet.' }
          : torrent.includedInRemoteBackup ? { tone: 'green' as const, label: 'On Drive', help: torrent.remoteBackupAt ? `Copied ${formatAge(torrent.remoteBackupAt)}` : 'Copied to Google Drive.' }
          : { tone: 'neutral' as const, label: 'Only here', help: 'Archived on this server, waiting for the next copy to Google Drive.' }
        return <tr key={row.key}>
          <td><div className="row-with-poster"><RowPoster reference={torrent.posterRef} mediaType={torrent.mediaType} /><div className="archive-title">
            <strong>{torrent.mediaTitle ?? release}{torrent.mediaTitle && torrent.mediaYear ? <span> {torrent.mediaYear}</span> : null}</strong>
            {torrent.mediaTitle ? <span className="archive-release" title={release}>{release}</span> : <span className="archive-release">Not linked to the library</span>}
            {torrent.sourceState === 'unregistered' && <span className="archive-lost">Removed from tracker</span>}
          </div></div></td>
          <td className="archive-num">{formatSize(torrent.totalSize)}</td>
          <td className="archive-when">{torrent.archivedAt ? <Tooltip content={new Date(torrent.archivedAt).toLocaleString()}><span>{formatAge(torrent.archivedAt)}</span></Tooltip> : '-'}</td>
          <td><Tooltip content={backup.help}><Badge tone={backup.tone}>{backup.label}</Badge></Tooltip></td>
          <td><span className={`archive-qbit ${torrent.qbitPresent ? 'present' : ''}`}>{torrent.qbitPresent ? 'Present' : 'Removed'}</span></td>
          <td><div className="archive-actions">
            {!torrent.archiveVerified && <Tooltip asChild content="Retry archiving"><button type="button" className="icon-button" aria-label={`Retry archiving ${release}`} disabled={busy} onClick={() => void retryArchive(torrent)}><Icon name="refresh" size={15} /></button></Tooltip>}
          </div></td>
        </tr>
      })}</tbody></table>}
    </Panel>
  </section>
}

/** Re-add archived torrents to qBittorrent: a checked plan first, then only
 *  what was confirmed. Part of the Backups page's restore. */
export function RecoveryReimportSection({ refreshKey = 0 }: { refreshKey?: number }) {
  const [reimportPlan, setReimportPlan] = useState<RecoveryReimportPlan | null>(null)
  const [reimportJob, setReimportJob] = useState<Job | null>(null)
  const [selectedRecoveryIds, setSelectedRecoveryIds] = useState<Set<string>>(new Set())
  const [confirmReimport, setConfirmReimport] = useState(false)
  const [message, setMessage] = useState<Message>(null)
  const [reimportBusy, setReimportBusy] = useState(false)

  useEffect(() => {
    if (!reimportJob || !['queued', 'running'].includes(reimportJob.state)) return
    let alive = true
    const refresh = async () => {
      try { const value = await systemApi.job(reimportJob.id); if (alive) setReimportJob(value) } catch {}
    }
    const timer = window.setInterval(() => void refresh(), 1500)
    return () => { alive = false; window.clearInterval(timer) }
  }, [reimportJob?.id, reimportJob?.state])

  const inspectReimport = async () => {
    setReimportBusy(true); setMessage(null)
    try {
      const plan = await systemApi.recoveryReimportPlan()
      setReimportPlan(plan)
      setSelectedRecoveryIds(new Set(plan.items.filter((item) => item.status === 'ready').map((item) => item.torrentId)))
    } catch (reason) { setMessage(failed(reason, 'Could not check what can be re-added.')) }
    finally { setReimportBusy(false) }
  }
  // A finished fetch from Drive opens the plan straight away.
  useEffect(() => { if (refreshKey) void inspectReimport() }, [refreshKey])
  const submitReimport = async () => {
    if (!reimportPlan || !selectedRecoveryIds.size) return
    setReimportBusy(true); setMessage(null)
    try {
      const accepted = await systemApi.startRecoveryReimport(reimportPlan.confirmationToken, [...selectedRecoveryIds])
      const value = await systemApi.job(accepted.job_id)
      setReimportJob(value)
      setConfirmReimport(false)
      setReimportPlan(null)
      setSelectedRecoveryIds(new Set())
    } catch (reason) {
      setConfirmReimport(false)
      setMessage(failed(reason, 'Could not start re-adding torrents. Check the plan again before retrying.'))
    } finally { setReimportBusy(false) }
  }
  const reimportSummary = reimportJob?.summary ?? {}
  const reimportTone = reimportJob?.state === 'completed' ? 'green' : reimportJob?.state === 'failed' ? 'red' : reimportJob ? 'amber' : 'neutral'
  const readyIds = reimportPlan?.items.filter((item) => item.status === 'ready').map((item) => item.torrentId) ?? []

  return <div className="backups-reimport">
    <div className="button-row">
      <Button variant="ghost" icon="refresh" disabled={reimportBusy || reimportJob?.state === 'running' || reimportJob?.state === 'queued'} onClick={() => void inspectReimport()}>{reimportBusy ? 'Checking…' : 'Check what can be re-added'}</Button>
      {reimportPlan && <><Badge tone="green">{reimportPlan.ready} ready</Badge><Badge tone="neutral">{reimportPlan.alreadyPresent} already in qBittorrent</Badge><Badge tone={reimportPlan.blocked ? 'red' : 'neutral'}>{reimportPlan.blocked} blocked</Badge></>}
    </div>

    {reimportPlan && <>
      <p className="backups-note">Each torrent goes back to the client, save path, category and tags it had, with the same files selected. Blocked rows say why. The plan expires at {new Date(reimportPlan.expiresAt).toLocaleTimeString()}; check again if anything changes.</p>
      <div className="button-row">
        <Button variant="ghost" onClick={() => setSelectedRecoveryIds(new Set(readyIds))}>Select all ready</Button>
        <Button variant="ghost" onClick={() => setSelectedRecoveryIds(new Set())}>Clear selection</Button>
        <Badge tone="blue">{selectedRecoveryIds.size} selected</Badge>
      </div>
      <div className="table-scroll"><table className="data-table"><thead><tr><th></th><th>Release</th><th>Status</th><th>qBittorrent</th><th>Destination</th><th>Files</th></tr></thead><tbody>{reimportPlan.items.map((item) => {
        const selected = selectedRecoveryIds.has(item.torrentId)
        const selectable = item.status === 'ready'
        return <tr key={item.torrentId}>
          <td><input type="checkbox" aria-label={`Re-add ${item.releaseName ?? item.torrentName}`} checked={selected} disabled={!selectable} onChange={() => setSelectedRecoveryIds((current) => { const next = new Set(current); if (next.has(item.torrentId)) next.delete(item.torrentId); else next.add(item.torrentId); return next })} /></td>
          <td><strong>{item.releaseName ?? item.torrentName}</strong><span className="table-sub">{item.mediaTitle ?? item.mediaType ?? 'Unknown media'} · {item.infoHash.slice(0, 12)}</span>{item.warnings.map((warning) => <span key={warning} className="table-sub">{warning}</span>)}</td>
          <td><Badge tone={item.status === 'ready' ? 'green' : item.status === 'already_present' ? 'neutral' : 'red'}>{item.status === 'already_present' ? 'already in qBittorrent' : item.status}</Badge>{item.reasons.map((reason) => <span key={reason} className="table-sub">{reason}</span>)}</td>
          <td>{item.downloadClientName ?? 'Unavailable'}{item.category !== undefined && <span className="table-sub">Category: {item.category || 'none'}</span>}{item.tags.length ? <span className="table-sub">Tags: {item.tags.join(', ')}</span> : null}</td>
          <td><code>{item.savePath ?? 'Missing'}</code>{item.resolvedSavePath && item.resolvedSavePath !== item.savePath && <span className="table-sub">Resolves to {item.resolvedSavePath}</span>}</td>
          <td><span>{item.selectedFileCount}/{item.totalFileCount} selected</span>{item.skippedFileCount ? <span className="table-sub">{item.skippedFileCount} stay skipped</span> : null}</td>
        </tr>
      })}</tbody></table></div>
      <div className="settings-footer"><Button variant="primary" icon="download" disabled={!selectedRecoveryIds.size || reimportBusy} onClick={() => setConfirmReimport(true)}>Re-add {selectedRecoveryIds.size || ''} to qBittorrent</Button></div>
    </>}

    {reimportJob && <div className="recovery-job">
      <div className="button-row"><Badge tone={reimportTone}>{reimportJob.state}</Badge><span className="muted">{reimportJob.error || (typeof reimportSummary.message === 'string' ? reimportSummary.message : reimportJob.detail) || 'Re-adding torrents to qBittorrent.'}</span></div>
      {reimportJob.progress !== undefined && <div className="job-progress-line"><Progress value={reimportJob.progress} tone={reimportJob.state === 'completed' ? 'green' : 'blue'} /><span>{reimportJob.progress}%</span></div>}
      {typeof reimportSummary.submitted === 'number' && <span className="muted">Re-added: {reimportSummary.submitted} · Already in qBittorrent: {Number(reimportSummary.skipped ?? 0)} · Failed: {Number(reimportSummary.failed ?? 0)}</span>}
    </div>}

    <Note message={message} />

    {confirmReimport && reimportPlan && <Modal
      title="Re-add torrents"
      eyebrow="RESTORE"
      icon="download"
      onClose={() => setConfirmReimport(false)}
      footer={<><Button variant="ghost" onClick={() => setConfirmReimport(false)}>Cancel</Button><Button variant="primary" icon="download" disabled={reimportBusy || !selectedRecoveryIds.size} onClick={() => void submitReimport()}>{reimportBusy ? 'Submitting…' : `Re-add ${selectedRecoveryIds.size} torrent${selectedRecoveryIds.size === 1 ? '' : 's'}`}</Button></>}
    >
      <div className="settings-section"><div><h3>qBittorrent will start downloading these</h3><p>The {selectedRecoveryIds.size} selected torrent{selectedRecoveryIds.size === 1 ? '' : 's'} go back to their original save paths with the same category, tags and file selection. Anything that appears in qBittorrent in the meantime is skipped.</p></div></div>
      <Note message={{ tone: 'error', text: 'This can start a lot of network and disk activity. Nothing already on disk is deleted or moved.' }} />
    </Modal>}
  </div>
}
