import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { cachedList, rememberList } from '../lib/listCache'
import { needsAttention, statusTone } from '../lib/status'
import { Link } from 'react-router-dom'
import { Field, SettingsGroup } from '../components/settings'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { useConfirm } from '../components/Confirm'
import { usePlexAvailable } from '../components/Shell'
import { useCurrentLibrary, useLibraries } from '../lib/libraries'
import { Badge, Button, EmptyState, Input, Panel, Select } from '../components/ui'
import { Modal } from '../components/Modal'
import { profileFormChanged } from '../lib/profileForm'
import { TorrentHistoryPanel } from '../components/TorrentHistoryPanel'
import { IncomingReleaseRow, useLiveDownloads, withLiveUpdate } from '../components/IncomingDownload'
import { CustomFiltersModal, LibraryFilterMenu, useCustomFilters } from '../components/CustomFilters'
import { showFilterFields } from '../lib/libraryFilterFields'
import { InteractiveSearchDialog } from '../components/InteractiveSearchDialog'
import { LibraryHistoryModal } from '../components/LibraryHistoryModal'
import { LibraryContextMenu } from '../components/LibraryContextMenu'
import { RemoveTitlesModal } from '../components/RemoveTitlesModal'
import { settingsApi } from '../api/modules/settings'
import { showsApi } from '../api/modules/shows'
import { systemApi } from '../api/modules/system'
import { upgradesApi } from '../api/modules/upgrades'
import { normalizeMediaView } from '../lib/uiState'
import { useLibrarySelection, type SelectionModifiers } from '../lib/useLibrarySelection'
import { SelectionBar, SelectCheck } from '../components/SelectionBar'
import { reuseUnchanged } from '../lib/reuseUnchanged'
import { VirtualGrid, useVirtualRows } from '../components/VirtualList'
import type { EpisodeOrdering, Library, MediaProfileSettings, QualityDefinition, QualityProfile, Show, Season, Episode, EpisodeMedia } from '../types'
import { MonitorMark, Page, PosterImage, QbitStatusBadge, defaultProfileLabel, formatEvidenceDate, libraryListEvents, useLiveLibraryRefresh } from './shared'

const seasonName = (season: Season) => season.title || (season.seasonNumber === 0 ? 'Specials' : `Season ${season.seasonNumber}`)
const fileName = (path: string) => path.split(/[\\/]/).pop() || path

export function ShowsPage({ library }: { library?: Library } = {}) {
  const libraryId = library?.id ?? ''
  const confirm = useConfirm()
  const plexAvailable = usePlexAvailable()
  // The library as last loaded in this tab: shown at once on return.
  const [items, setItems] = useState<Show[]>(() => cachedList<Show>(`shows:${libraryId}:title`) ?? [])
  const [view, setView] = useState<'cards' | 'table'>(() => normalizeMediaView(window.localStorage.getItem('medialogue.shows.view')))
  const [query, setQuery] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [filter, setFilter] = useState('All')
  const [sort, setSort] = useState('title')
  const [loading, setLoading] = useState(() => cachedList(`shows:${libraryId}:title`) === undefined)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [cardBusy, setCardBusy] = useState<string | null>(null)
  const [context, setContext] = useState<{ x: number; y: number } | null>(null)
  const [removing, setRemoving] = useState<string[] | null>(null)
  // Matches the server default, so the chip does not flash on before the fetch.
  const [countSpecials, setCountSpecials] = useState(false)
  const [specialsBusy, setSpecialsBusy] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const customFilters = useCustomFilters('shows', showFilterFields, filter)
  // A saved filter deleted elsewhere leaves nothing to apply.
  useEffect(() => { if (customFilters.isCustom && customFilters.filters.length && !customFilters.active) setFilter('All') }, [customFilters.isCustom, customFilters.active, customFilters.filters.length])
  const load = async (foreground = true) => {
    if (foreground) setLoading(true)
    try { const next = await showsApi.shows('', sort, libraryId); rememberList(`shows:${libraryId}:${sort}`, next); setItems((current) => reuseUnchanged(current, next)); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load shows.') }
    finally { if (foreground) setLoading(false) }
  }
  // Live events already trigger a reload, so the poll is only a fallback.
  useLiveLibraryRefresh(() => load(false), 60000, false, [...libraryListEvents, 'results.shows'])
  useEffect(() => {
    const cached = cachedList<Show>(`shows:${libraryId}:${sort}`)
    if (cached) { setItems((current) => reuseUnchanged(current, cached)); setLoading(false) }
    void load(!cached && !items.length)
  }, [sort])
  useEffect(() => { showsApi.specialsCounting().then((value) => setCountSpecials(value.count_specials)).catch(() => undefined) }, [])

  // A hard switch, not a view filter: it rewrites the Counted flag on Season 0
  // for every show. Monitoring is left alone, so nothing stops being searched.
  const toggleSpecials = async () => {
    const next = !countSpecials
    if (!next && !await confirm({
      title: 'Stop counting Specials?',
      body: 'Specials are excluded from episode totals on every show, and any per-show Counted choice you made is cleared.',
      confirmLabel: 'Stop counting',
    })) return
    setSpecialsBusy(true)
    try {
      const result = await showsApi.setSpecialsCounting(next)
      setCountSpecials(result.count_specials)
      setError('')
      await load(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not change Specials counting.') }
    finally { setSpecialsBusy(false) }
  }
  const checkShowForUpgradeNow = async (show: Show) => {
    setCardBusy(show.id); setMessage('')
    try {
      const accepted = await upgradesApi.searchUpgrades({ showId: show.id })
      setMessage(`Checking ${show.title} for upgrades…`)
      const [job] = await systemApi.waitForJobs([accepted.jobId])
      const found = Number(job?.summary?.candidates_found ?? 0)
      if (job?.state === 'completed') setMessage(found ? `${found} upgrade ${found === 1 ? 'candidate' : 'candidates'} found for ${show.title}. Review ${found === 1 ? 'it' : 'them'} in Upgrade Media.` : `No upgrade found for ${show.title}.`)
      else setMessage(`Upgrade check for ${show.title} ${job?.state ?? 'failed'}. Check Tasks for details.`)
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : `Could not check ${show.title} for upgrades.`)
    } finally { setCardBusy(null) }
  }
  // Cards are memoised, so they get one stable handler that always calls the
  // latest version of the action.
  const upgradeRef = useRef(checkShowForUpgradeNow)
  upgradeRef.current = checkShowForUpgradeNow
  const checkShowForUpgrade = useCallback((show: Show) => upgradeRef.current(show), [])
  const totalEpisodes = useMemo(() => items.reduce((sum, show) => sum + show.episodesTotal, 0), [items])
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return items.filter((show) => (customFilters.isCustom ? customFilters.matches(show) : filter === 'All' || (filter === 'Missing' ? show.status === 'Missing' || (show.episodesMissing ?? 0) > 0 : filter === 'Problems' ? needsShowReview(show) : filter === 'Unmonitored' ? show.monitored === false : show.status === filter))
      && (!needle || show.title.toLowerCase().includes(needle)))
  }, [items, filter, query, customFilters.isCustom, customFilters.matches])
  const visibleIds = useMemo(() => filtered.map((show) => show.id), [filtered])
  const { selected, click: toggleSelected, focus, selectAll, clear } = useLibrarySelection(visibleIds, { enabled: !context && !historyOpen && !removing && !filtersOpen })
  const openContext = useCallback((event: React.MouseEvent, showId: string) => {
    event.preventDefault()
    event.stopPropagation()
    focus(showId)
    setContext({ x: event.clientX, y: event.clientY })
  }, [focus])
  return <Page title={library?.name ?? 'Shows'} subtitle="Track seasons and episodes without reorganizing your files." action={<Button variant="secondary" icon="clock" onClick={() => setHistoryOpen(true)}>History</Button>}>
    {historyOpen && <LibraryHistoryModal mediaType="shows" onClose={() => setHistoryOpen(false)} />}
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${items.length} ${items.length === 1 ? 'show' : 'shows'}, ${totalEpisodes} ${totalEpisodes === 1 ? 'episode' : 'episodes'}…`} /></div><Tooltip asChild content={countSpecials ? 'Specials count toward episode totals. Click to exclude them.' : 'Specials are excluded from episode totals. Click to include them.'}><button type="button" className={`filter-chip library-specials-toggle ${countSpecials ? 'active' : ''}`} aria-pressed={countSpecials} disabled={specialsBusy} onClick={() => void toggleSpecials()}><Icon name={countSpecials ? 'check' : 'close'} size={12} />Specials</button></Tooltip><SelectionBar count={selected.size} total={filtered.length} noun="show" onSelectAll={selectAll} onClear={clear} /><div className="toolbar-spacer" /><Select className="library-control" selectedPrefix="SORT" aria-label="Sort shows" value={sort} onChange={(event) => setSort(event.target.value)}><option value="title">Title A–Z</option><option value="-title">Title Z–A</option><option value="year">Year oldest</option><option value="-year">Year newest</option><option value="missing">Missing fewest</option><option value="-missing">Missing most</option><option value="problems">Problems fewest</option><option value="-problems">Problems most</option><option value="-last_observed">Last observed newest</option><option value="last_observed">Last observed oldest</option></Select><LibraryFilterMenu ariaLabel="Filter shows" value={filter} builtIn={['All', 'Missing', 'Problems', 'Unmonitored', 'Present']} custom={customFilters.filters} onChange={setFilter} onManage={() => setFiltersOpen(true)} /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => { setView('cards'); window.localStorage.setItem('medialogue.shows.view', 'cards') }} aria-label="Card view"><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => { setView('table'); window.localStorage.setItem('medialogue.shows.view', 'table') }} aria-label="Table view"><Icon name="list" size={16} /></button></div></div>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {error && <EmptyState title="Could not load Shows" detail={error} />}
    {!error && !loading && (view === 'cards' ? <VirtualGrid className="media-grid show-grid" items={filtered} itemKey={(show) => show.id} renderItem={(show) => <ShowCard show={show} selected={selected.has(show.id)} onToggle={toggleSelected} onContext={openContext} busy={cardBusy === show.id} onCheckUpgrade={checkShowForUpgrade} />} /> : <ShowTable items={filtered} selected={selected} onToggle={toggleSelected} onSelectAll={selectAll} onClear={clear} onContext={openContext} />)}
    {context && <><div className="context-dismiss-layer" onClick={() => setContext(null)} onContextMenu={(event) => { event.preventDefault(); setContext(null) }} /><LibraryContextMenu position={context} count={selected.size} noun="show" onRemove={() => { setRemoving([...selected]); setContext(null) }} onClear={() => { clear(); setContext(null) }} /></>}
    {filtersOpen && <CustomFiltersModal mediaType="shows" noun="shows" filters={customFilters.filters} fields={showFilterFields} items={items} onChanged={customFilters.setFilters} onApply={setFilter} onClose={() => setFiltersOpen(false)} />}
    {removing && <RemoveTitlesModal kind="show" ids={removing} onClose={() => setRemoving(null)} onDone={(text) => { setRemoving(null); clear(); setMessage(text); void load(false) }} />}
    {!error && !loading && !filtered.length && <EmptyState icon="tv-screen" title={items.length ? 'No shows match this filter' : 'No Shows yet'} detail={items.length ? 'Try another library filter.' : 'Add a Show from TMDB, or scan a Show media folder to find existing files.'} />}
  </Page>
}

const needsShowReview = (show: Show) => (show.problemCount ?? 0) > 0

function ShowTable({ items, selected, onToggle, onSelectAll, onClear, onContext }: { items: Show[]; selected: Set<string>; onToggle: (id: string, modifiers?: SelectionModifiers) => void; onSelectAll: () => void; onClear: () => void; onContext: (event: React.MouseEvent, id: string) => void }) {
  const plexAvailable = usePlexAvailable()
  const rows = useVirtualRows(items, plexAvailable ? 7 : 6)
  return <Panel className="table-panel"><table className="data-table media-table"><thead><tr>
    <th className="select-cell"><SelectCheck inline checked={items.length > 0 && selected.size === items.length} mixed={selected.size > 0 && selected.size < items.length} label="all shows" onToggle={() => selected.size ? onClear() : onSelectAll()} /></th>
    <th>Title</th><th>Seasons</th><th>Episodes</th><th>Status</th><th>qBittorrent</th>{plexAvailable && <th>Plex</th>}
  </tr></thead><tbody ref={rows.bodyRef}>{rows.before}{rows.visible.map((show) => {
    const problems = show.problemCount ?? 0
    const missing = show.episodesMissing ?? Math.max(0, show.episodesTotal - show.episodesPresent)
    return <tr key={show.id} className={selected.has(show.id) ? 'row-selected' : ''} onContextMenu={(event) => onContext(event, show.id)} onMouseDown={(event) => { if (event.shiftKey) event.preventDefault() }} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(show.id, event) } }}>
      <td className="select-cell"><SelectCheck inline checked={selected.has(show.id)} label={show.title} onToggle={(modifiers) => onToggle(show.id, modifiers)} /></td>
      <td><div className="media-table-title">
        <span className={`table-poster poster-${show.id}`}><PosterImage reference={show.poster} title={show.title} size="w92" /></span>
        <MonitorMark monitored={show.monitored !== false} />
        <Link className="media-table-name" to={`/shows/${show.id}`}>{show.title}</Link>
        <span className="media-table-year">{show.year || ''}</span>
      </div></td>
      <td className="media-table-count">{show.seasons}</td>
      <td className="media-table-count">{show.episodesPresent} / {show.episodesTotal}{missing > 0 && <span className="media-table-missing">{missing} missing</span>}</td>
      <td><span className="media-table-badges"><Badge tone={statusTone(show.status)}>{show.status}</Badge>{problems > 0 && <Badge tone="red">{problems} problem{problems === 1 ? '' : 's'}</Badge>}</span></td>
      <td><QbitStatusBadge status={show.torrentStatus} showState /></td>
      {plexAvailable && <td><Badge tone={statusTone(show.plex)}>{show.plex}</Badge></td>}
    </tr>
  })}{rows.after}</tbody></table></Panel>
}

const ShowCard = memo(function ShowCard({ show, selected, onToggle, onContext, busy, onCheckUpgrade }: { show: Show; selected: boolean; onToggle: (id: string, modifiers?: SelectionModifiers) => void; onContext: (event: React.MouseEvent, id: string) => void; busy: boolean; onCheckUpgrade: (show: Show) => Promise<void> }) {
  const plexAvailable = usePlexAvailable()
  const percent = show.episodesTotal ? Math.round((show.episodesPresent / show.episodesTotal) * 100) : 0
  const checkUpgrade = (event: React.MouseEvent) => { event.preventDefault(); event.stopPropagation(); void onCheckUpgrade(show) }
  return <article className={`media-card ${selected ? 'media-selected' : ''} ${needsAttention(show.status) ? 'media-attention' : ''}`} onContextMenu={(event) => onContext(event, show.id)}><SelectCheck checked={selected} label={show.title} onToggle={(modifiers) => onToggle(show.id, modifiers)} /><Link className="media-card-link" to={`/shows/${show.id}`} onMouseDown={(event) => { if (event.shiftKey) event.preventDefault() }} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(show.id, event) } }}><div className={`poster poster-${show.id} ${show.poster ? 'poster-has-image' : 'poster-empty'}`}><PosterImage reference={show.poster} title={show.title} size="w342" />{!show.poster && <span className="poster-fallback-title">{show.title}</span>}<span className="poster-marks"><span className="media-card-actions" aria-label={`Actions for ${show.title}`}><Tooltip asChild content="Check for Upgrade"><button className="icon-button" aria-label={`Check ${show.title} for upgrades`} disabled={busy} onClick={checkUpgrade}><Icon name="upgrade-media" size={13} /></button></Tooltip></span><MonitorMark monitored={show.monitored !== false} /></span></div><div className="media-card-body"><div className="media-card-meta"><QbitStatusBadge status={show.torrentStatus} compact />{plexAvailable && <Tooltip content={`Plex ${show.plex}`}><Badge tone={statusTone(show.plex)} className="media-card-secondary-badge">Plex</Badge></Tooltip>}</div><div className="media-card-title"><strong>{show.title}</strong><span>{show.year || 'Year unknown'}</span></div>{/* The count and its completeness are one fact, so they are one control: the
                  pill fills to the proportion present and is coloured by whether any
                  are missing. A separate bar repeated what the numbers already said. */}
              <div className={`episode-progress ${percent === 100 ? 'complete' : 'incomplete'}`} title={`${show.episodesPresent} of ${show.episodesTotal} episodes present`}>
                <span className="episode-progress-fill" style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />
                <span className="episode-progress-label">Episodes</span>
                <strong>{show.episodesPresent} / {show.episodesTotal}</strong>
              </div></div></Link></article>
})

export function ShowDetailPage({ id }: { id: string }) {
  const confirm = useConfirm()
  const plexAvailable = usePlexAvailable()
  const [show, setShow] = useState<Show | null>(null)
  useCurrentLibrary(show?.libraryId)
  const { libraries } = useLibraries()
  const showLibrary = libraries?.find((item) => item.id === show?.libraryId)
  const backLabel = `Back to ${showLibrary?.name ?? 'Shows'}`
  const backTo = showLibrary ? `/library/${showLibrary.id}` : '/shows'
  const [selectedSeasonId, setSelectedSeasonId] = useState('')
  const [episodeFilter, setEpisodeFilter] = useState<'all' | 'missing' | 'problems'>('all')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [mappingEditor, setMappingEditor] = useState<{ media: EpisodeMedia; season: Season } | null>(null)
  const [mappingEpisodeIds, setMappingEpisodeIds] = useState<string[]>([])
  const [orderingOpen, setOrderingOpen] = useState(false)
  const [searching, setSearching] = useState<Season | null>(null)
  const load = async (reportError = true) => { try { setShow(await showsApi.show(id)); if (reportError) setError('') } catch (reason) { if (reportError) setError(reason instanceof Error ? reason.message : 'Show not found.') } }
  useEffect(() => { void load() }, [id])
  useLiveLibraryRefresh(() => load(false))
  // Each season's download moves in place, every couple of seconds.
  useLiveDownloads((show?.incomingDownloads ?? []).flatMap((item) => item.torrentId ? [item.torrentId] : []), (update) => {
    setShow((current) => current?.incomingDownloads ? { ...current, incomingDownloads: current.incomingDownloads.map((item) => withLiveUpdate(item, update)) } : current)
  })
  const refreshMetadata = async () => {
    setBusy(true); setMessage('Starting TMDB metadata refresh…')
    try {
      const accepted = await showsApi.refreshShowMetadata(id)
      setMessage(`TMDB metadata refresh queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
      const [job] = await systemApi.waitForJobs([accepted.job_id])
      await load()
      if (job?.state === 'completed') {
        const summary = job.summary ?? {}
        setMessage(`TMDB metadata refreshed: ${Number(summary.seasons ?? 0)} season${Number(summary.seasons ?? 0) === 1 ? '' : 's'} and ${Number(summary.episodes ?? 0)} episode${Number(summary.episodes ?? 0) === 1 ? '' : 's'} updated. Existing media mappings were preserved.`)
      } else if (job?.state === 'cancelled') setMessage('TMDB metadata refresh cancelled.')
      else setMessage(`TMDB metadata refresh failed${job?.error ? `: ${job.error}` : '.'} Check Tasks for details.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Metadata refresh failed.') }
    finally { setBusy(false) }
  }
  const recheckPlex = async () => {
    setBusy(true); setMessage('Starting Plex recheck…')
    try {
      const accepted = await showsApi.recheckShowPlex(id)
      setMessage(`Plex recheck queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
      const [job] = await systemApi.waitForJobs([accepted.job_id])
      await load()
      const summary = job?.summary ?? {}
      const count = (key: string) => Number(summary[key] ?? 0)
      if (job?.state === 'completed') setMessage(`Plex recheck complete: ${count('matched_releases')} matched, ${count('not_found_releases')} not in Plex, ${count('conflict_releases')} conflicts.`)
      else if (job?.state === 'cancelled') setMessage('Plex recheck cancelled. No further Plex verification was performed.')
      else setMessage(`Plex recheck failed${job?.error ? `: ${job.error}` : '.'} Check Tasks for details.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Plex recheck failed.') }
    finally { setBusy(false) }
  }
  const setShowMonitored = async (monitored: boolean) => { if (!show) return; setBusy(true); try { await showsApi.updateShow(id, { monitored, expected_revision: show.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update monitoring.') } finally { setBusy(false) } }
  const setSeasonMonitored = async (season: Season, monitored: boolean) => { try { await showsApi.updateSeason(season.id, { monitored, expected_revision: season.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update season monitoring.') } }
  const [orderings, setOrderings] = useState<EpisodeOrdering[] | null>(null)
  const [orderingBusy, setOrderingBusy] = useState(false)

  // Loaded on demand: it costs two TMDB calls, and most shows never need it.
  const loadOrderings = async () => {
    if (orderings || orderingBusy) return
    setOrderingBusy(true)
    try { setOrderings(await showsApi.episodeOrderings(id)) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not read episode orderings.') }
    finally { setOrderingBusy(false) }
  }

  // Switching renumbers the show's episodes, so it is always an explicit act,
  // never something a background metadata refresh does on its own.
  const chooseOrdering = async (option: EpisodeOrdering) => {
    if (option.selected || !show) return
    if (!await confirm({
      title: `Switch to ${option.name} ordering?`,
      body: 'Episodes keep their identity but are renumbered, so files already matched may need a re-scan to line up again.',
      confirmLabel: 'Switch ordering',
      tone: 'primary',
    })) return
    setOrderingBusy(true)
    try {
      await showsApi.updateShow(id, { tmdb_episode_group_id: option.id ?? '', expected_revision: show.revision })
      setOrderings(null)
      await load()
      setMessage(`Episode ordering switched to ${option.name}. Re-scan this show's media folder if file matches look off.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not switch episode ordering.') }
    finally { setOrderingBusy(false) }
  }

  const setSeasonCounted = async (season: Season, counted: boolean) => { try { await showsApi.updateSeason(season.id, { counted, expected_revision: season.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update season counting.') } }
  const setEpisodeMonitored = async (episode: Episode, monitored: boolean) => { try { await showsApi.updateEpisode(episode.id, { monitored, expected_revision: episode.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update episode monitoring.') } }
  const editMapping = (media: EpisodeMedia, season: Season) => { const selected = season.episodes.filter((episode) => media.mappedEpisodeNumbers.includes(episode.episodeNumber)).map((episode) => episode.id); setMappingEpisodeIds(selected); setMappingEditor({ media, season }) }
  const saveMapping = async () => { if (!mappingEditor || !mappingEpisodeIds.length) return; setBusy(true); try { await showsApi.correctEpisodeMapping(mappingEditor.media.mediaFileId, mappingEpisodeIds); setMessage('Episode mapping corrected. The media file and path were not changed.'); setMappingEditor(null); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not correct episode mapping.') } finally { setBusy(false) } }
  const seasons = show?.seasonDetail ?? []
  useEffect(() => {
    if (!seasons.length) return
    setSelectedSeasonId((current) => current && seasons.some((season) => season.id === current) ? current : (seasons.find((season) => season.missingCount > 0) ?? seasons[0]).id)
  }, [show, seasons])
  if (error) return <Page title="Show unavailable" subtitle={error} back={backLabel} backTo={backTo}><EmptyState title="Could not load this Show" detail={error} /></Page>
  if (!show) return <Page title="Loading Show" subtitle="Retrieving seasons and episode inventory." back={backLabel} backTo={backTo}><EmptyState title="Loading…" detail="Loading this show." /></Page>
  const selectedSeason = seasons.find((season) => season.id === selectedSeasonId) ?? seasons[0]
  const selectedReleaseMedia = selectedSeason
    ? [...new Map(selectedSeason.episodes.flatMap((episode) => episode.media).filter((media) => media.showReleaseId).map((media) => [media.showReleaseId, media])).values()]
    : []
  const selectedDownloads = (show.incomingDownloads ?? []).filter((item) => item.seasonNumber === selectedSeason?.seasonNumber)
  const visibleEpisodes = selectedSeason?.episodes.filter((episode) => episodeFilter === 'all' || episodeFilter === 'missing' && episode.status !== 'Present' || episodeFilter === 'problems' && plexAvailable && episode.plex === 'Conflict') ?? []
  const missing = show.episodesMissing ?? Math.max(0, show.episodesTotal - show.episodesPresent)
  // Only states worth acting on: "Present" is the default for a show you are looking at.
  const heroStatus = needsAttention(show.status)
  const problems = show.problems ?? []
  const openOrdering = () => { setOrderingOpen(true); void loadOrderings() }
  return <Page title={show.title} subtitle={`${show.year || 'Year unknown'} · ${show.tmdbId ? `TMDB ${show.tmdbId}` : show.id}`} back={backLabel} backTo={backTo}>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {mappingEditor && <Panel title="Correct episode mapping" eyebrow="LOGICAL MAPPING ONLY"><div className="settings-note"><Icon name="info" size={15} /><span>This changes only Medialogue's episode mapping. The file is not renamed, moved, copied, or modified.</span></div><div className="mapping-path">{mappingEditor.media.path}</div><div className="mapping-grid">{mappingEditor.season.episodes.map((episode) => <label className="inline-check" key={episode.id}><input type="checkbox" checked={mappingEpisodeIds.includes(episode.id)} onChange={(event) => setMappingEpisodeIds((current) => event.target.checked ? [...current, episode.id] : current.filter((item) => item !== episode.id))} />S{String(episode.seasonNumber).padStart(2, '0')}E{String(episode.episodeNumber).padStart(2, '0')} · {episode.title || 'Untitled episode'}</label>)}</div><div className="settings-footer"><Button variant="ghost" onClick={() => setMappingEditor(null)}>Cancel</Button><Button variant="primary" onClick={() => void saveMapping()} disabled={busy || !mappingEpisodeIds.length}>{busy ? 'Saving…' : 'Save mapping'}</Button></div></Panel>}
    <div className="detail-single">
      <div className="detail-top">
      <Panel className="detail-hero">
        <div className={`detail-poster poster-${show.id} ${show.poster ? 'poster-has-image' : 'poster-empty'}`}><PosterImage reference={show.poster} title={show.title} />{!show.poster && <span className="poster-fallback-title">{show.title}</span>}</div>
        <div className="detail-intro">
          <div className="detail-title-row">
            <h2>{show.title} {show.year ? <span className="detail-year">({show.year})</span> : null}</h2>
            <Tooltip content={show.monitored === false ? 'Not watched for new episodes or upgrades. Click to monitor.' : 'Watched for new episodes and upgrades. Click to stop.'}>
              <button type="button" className={`detail-monitor ${show.monitored === false ? 'off' : 'on'}`} disabled={busy} onClick={() => void setShowMonitored(show.monitored === false)}>
                <Icon name={show.monitored === false ? 'monitored-off' : 'monitored'} size={15} />
                {show.monitored === false ? 'Unmonitored' : 'Monitored'}
              </button>
            </Tooltip>
          </div>
          {(heroStatus || missing > 0 || plexAvailable) && <div className="badge-row detail-badges">
            {heroStatus && <Badge tone={statusTone(show.status)}>{show.status}</Badge>}
            {missing > 0 && <Badge tone="amber">{missing} missing</Badge>}
            {plexAvailable && <Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge>}
          </div>}
          <p className="detail-description">{show.overview ?? 'Episode presence is tracked independently while every file stays at its existing path.'}</p>
          <div className="detail-factline">
            <span>{show.episodesPresent} / {show.episodesTotal} episodes · {show.seasons} season{show.seasons === 1 ? '' : 's'}</span>
            <span>Last observed {formatEvidenceDate(show.lastObservedAt)}</span>
            {show.tmdbId ? <a href={`https://www.themoviedb.org/tv/${show.tmdbId}`} target="_blank" rel="noreferrer">TMDB <Icon name="external" size={12} /></a> : null}
            {show.tvdbId ? <a href={`https://thetvdb.com/dereferrer/series/${show.tvdbId}`} target="_blank" rel="noreferrer">TVDB <Icon name="external" size={12} /></a> : null}
            {/* A once-per-show fix, so it opens from here instead of holding a panel. */}
            <button type="button" className="detail-factlink" onClick={openOrdering}>Ordering: {show.tmdbEpisodeGroupId ? 'custom episode group' : 'TMDB default'} <Icon name="settings" size={12} /></button>
          </div>
          <div className="detail-actions">
            <Button variant="primary" icon="refresh" onClick={() => void refreshMetadata()} disabled={busy}>{busy ? 'Working…' : 'Refresh metadata'}</Button>
            {plexAvailable && <Button variant="secondary" icon="plex" onClick={() => void recheckPlex()} disabled={busy}>Recheck Plex</Button>}
          </div>
        </div>
      </Panel>
      <ShowProfilePanel resourceId={id} />
      </div>
      {problems.length > 0 && <Panel title="Problems" note={`${problems.length} NEED ATTENTION`}>
        {problems.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}>
          <div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div>
          <div><strong>{item.title}</strong><span>{item.detail}</span></div>
        </div>)}
      </Panel>}
      <Panel className="season-panel">
        {seasons.length ? <div className="season-tabs" role="tablist" aria-label="Seasons">
          {/* Specials last: they are the extras, not where the show starts. */}
          {[...seasons].sort((a, b) => (a.seasonNumber === 0 ? 1 : 0) - (b.seasonNumber === 0 ? 1 : 0) || a.seasonNumber - b.seasonNumber).map((season) => <button type="button" role="tab" aria-selected={selectedSeason?.id === season.id} key={season.id}
            className={`season-tab ${selectedSeason?.id === season.id ? 'selected' : ''} ${season.missingCount ? 'incomplete' : 'complete'}`}
            onClick={() => { setSelectedSeasonId(season.id); setEpisodeFilter('all') }}>
            {seasonName(season)}
            <span className="season-tab-count">{season.presentCount}/{season.episodeCount}</span>
            {(() => {
              const downloading = (show.incomingDownloads ?? []).filter((item) => item.seasonNumber === season.seasonNumber)
              if (!downloading.length) return null
              const percent = Math.round(Math.min(...downloading.map((item) => item.progress)))
              return <span className="season-tab-download" aria-label={`Downloading, ${percent}%`}><Icon name="download" size={11} />{percent}%</span>
            })()}
          </button>)}
        </div> : <div className="history-empty">No season metadata exists yet.</div>}
        {selectedSeason && <>
          <div className="season-bar">
            <MonitorMark monitored={selectedSeason.monitored} onToggle={(value) => void setSeasonMonitored(selectedSeason, value)} />
            <span className="season-bar-count">{selectedSeason.presentCount} / {selectedSeason.episodeCount} present</span>
            <Tooltip content={selectedSeason.counted ? 'Included in the show’s episode totals.' : 'Left out of the show’s episode totals.'}>
              <label className="inline-check"><input type="checkbox" checked={selectedSeason.counted} onChange={(event) => void setSeasonCounted(selectedSeason, event.target.checked)} />Counted</label>
            </Tooltip>
            <div className="episode-filter-chips"><button className={episodeFilter === 'all' ? 'active' : ''} onClick={() => setEpisodeFilter('all')}>All</button><button className={episodeFilter === 'missing' ? 'active' : ''} onClick={() => setEpisodeFilter('missing')}>Missing</button><button className={episodeFilter === 'problems' ? 'active' : ''} onClick={() => setEpisodeFilter('problems')}>Problems</button></div>
            <Button variant="secondary" icon="search" onClick={() => setSearching(selectedSeason)}>Search season</Button>
          </div>
          {selectedDownloads.length > 0 && <div className="release-status-list season-downloads">{selectedDownloads.map((item) => <IncomingReleaseRow key={item.id ?? item.name} incoming={item} />)}</div>}
          {selectedReleaseMedia.length > 0 && <div className="release-status-list">{selectedReleaseMedia.map((media) => <div className="release-evidence-row" key={media.showReleaseId}><div className="release-icon kind-show"><Icon name="tv-screen" size={16} /></div><div className="release-main"><strong>{media.releaseName ?? media.path}</strong><small>{media.releaseScope === 'season_pack' ? 'Season pack' : 'Episode release'}</small></div><div className="release-tags"><QbitStatusBadge status={media.torrentStatus} showState /></div></div>)}</div>}
          <div className="episode-table-wrap"><table className="data-table episode-table"><tbody>{visibleEpisodes.map((episode) => {
            const media = episode.media[0]
            return <tr key={episode.id} className={episode.monitored || media?.releaseScope === 'season_pack' ? '' : 'episode-unmonitored'}>
              <td className="episode-monitor-cell">{media?.releaseScope === 'season_pack'
                ? <Tooltip content="From a season pack: monitored with the season."><span className="episode-pack-mark"><Icon name="archive" size={14} /></span></Tooltip>
                : <MonitorMark monitored={episode.monitored} onToggle={(value) => void setEpisodeMonitored(episode, value)} />}</td>
              <td><div className="episode-title-cell"><strong><span className="episode-code">E{String(episode.episodeNumber).padStart(2, '0')}</span>{episode.title || 'Untitled episode'}</strong>{episode.airDate && <small>{episode.airDate}</small>}</div></td>
              <td><div className="episode-media-cell">
                {media ? <Tooltip content={media.path}><strong>{episode.quality || fileName(media.path)}</strong></Tooltip> : <span className="muted">—</span>}
                {media && (media.mappedEpisodeNumbers.length > 1 || media.manualMapping) && <div className="badge-row compact">{media.mappedEpisodeNumbers.length > 1 && <Badge tone="blue">Multi-episode</Badge>}{media.manualMapping && <Badge tone="neutral">Manual mapping</Badge>}</div>}
              </div></td>
              <td>{selectedDownloads.length && !media
                ? <Badge tone="blue">Downloading</Badge>
                : <Badge tone={statusTone(episode.status)}>{episode.status}</Badge>}</td>
              {plexAvailable && <td><Badge tone={statusTone(episode.plex)}>Plex {episode.plex}</Badge></td>}
              <td className="episode-map-cell">{media && <Tooltip content="Map this file to other episodes"><button type="button" className="icon-button" aria-label={`Map ${media.path}`} onClick={() => editMapping(media, selectedSeason)}><Icon name="shuffle" size={14} /></button></Tooltip>}</td>
            </tr>
          })}</tbody></table>{!visibleEpisodes.length && <div className="history-empty">No episodes match this filter.</div>}</div>
        </>}
      </Panel>
      <Panel title="Release history">{show.recentEvents?.length ? show.recentEvents.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{formatEvidenceDate(event.createdAt)}</span></div><Badge tone={event.type.includes('replaced') ? 'purple' : event.type.includes('duplicate') || event.type.includes('conflict') ? 'red' : 'neutral'}>{event.type.replaceAll('.', ' ')}</Badge></div>) : <div className="history-empty">No show or episode events recorded yet.</div>}</Panel>
      <TorrentHistoryPanel items={show.torrentHistory} noun="show" />
    </div>
    {searching && <InteractiveSearchDialog
      searchKey={searching.id}
      start={() => showsApi.startSeasonSearch(searching.id)}
      scope="shows"
      title={`${show.title}${show.year ? ` (${show.year})` : ''} · ${seasonName(searching)}`}
      onClose={() => setSearching(null)}
      onStarted={(text) => { setMessage(text); void load() }}
    />}
    {orderingOpen && <Modal title="Episode ordering" eyebrow={show.title.toUpperCase()} icon="list" onClose={() => setOrderingOpen(false)}>
      {orderings ? <div className="ordering-list">{orderings.map((option) => <button className={`ordering-option ${option.selected ? 'selected' : ''}`} key={option.id ?? 'default'} disabled={orderingBusy} onClick={() => void chooseOrdering(option)}><span className="ordering-copy"><strong>{option.name}</strong><span>{option.season_count} season{option.season_count === 1 ? '' : 's'} · {option.episode_count} episode{option.episode_count === 1 ? '' : 's'}{option.network ? ` · ${option.network}` : ''}</span>{option.description && <small>{option.description}</small>}</span><Badge tone={option.selected ? 'green' : 'neutral'}>{option.selected ? 'In use' : option.type_label}</Badge></button>)}<p className="cf-modal-note">If this show&rsquo;s seasons do not match your files, another ordering probably does. Switching renumbers the existing episodes rather than replacing them.</p></div>
        : <div className="history-empty">Loading TMDB orderings…</div>}
    </Modal>}
  </Page>
}

function ShowProfilePanel({ resourceId }: { resourceId: string }) {
  const [settings, setSettings] = useState<MediaProfileSettings | null>(null)
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [qualities, setQualities] = useState<QualityDefinition[]>([])
  const [profileId, setProfileId] = useState('')
  const [minimumOverrideId, setMinimumOverrideId] = useState('')
  const [minimumScoreOverride, setMinimumScoreOverride] = useState('')
  const [message, setMessage] = useState('')
  const load = async () => { try { const [value, profileRows, qualityRows] = await Promise.all([settingsApi.showProfileSettings(resourceId), settingsApi.qualityProfiles(), settingsApi.qualityDefinitions()]); setSettings(value); setProfiles(profileRows); setQualities(qualityRows); setProfileId(value.qualityProfileInherited ? '' : value.qualityProfileId ?? ''); setMinimumOverrideId(value.minimumQualityOverridden ? value.minimumQuality?.id ?? '' : ''); setMinimumScoreOverride(value.minimumCustomFormatScoreOverridden && value.minimumCustomFormatScore !== undefined ? String(value.minimumCustomFormatScore) : '') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load Show profile.') } }
  useEffect(() => { void load() }, [resourceId])
  const save = async () => { try { const value = await settingsApi.saveShowProfileSettings(resourceId, { quality_profile_id: profileId || null, minimum_quality_definition_override_id: minimumOverrideId || null, minimum_custom_format_score_override: minimumScoreOverride === '' ? null : Number(minimumScoreOverride), custom_format_score_overrides: Object.fromEntries((settings?.customFormatScores ?? []).filter((item) => item.overrideScore !== undefined).map((item) => [item.customFormatId, item.overrideScore ?? 0])), expected_revision: settings?.revision ?? 0 }); setSettings(value); setMessage('Show Quality Profile saved.') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save Show profile.') } }
  const dirty = profileFormChanged(settings, { profileId, minimumOverrideId, minimumScoreOverride })
  return <Panel className="profile-panel">
    <div className="settings-form profile-form">
      <SettingsGroup>
        <Field label="Quality profile" help="Which profile ranks and scores releases for this show.">
          <Select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
            <option value="">{defaultProfileLabel(profiles)}</option>
            {profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}
          </Select>
        </Field>
        <Field label="Minimum quality" help="Releases below this are rejected for automatic selection.">
          <Select value={minimumOverrideId} onChange={(event) => setMinimumOverrideId(event.target.value)}>
            <option value="">Same as profile</option>
            {qualities.filter((quality) => (profiles.find((profile) => profileId ? profile.id === profileId : profile.isDefault)?.qualities ?? []).some((item) => item.id === quality.id)).map((quality) => <option value={quality.id} key={quality.id}>{quality.name}</option>)}
          </Select>
        </Field>
        <Field label="Minimum Custom Format score" help={settings?.minimumCustomFormatScore !== undefined ? `Currently ${settings.minimumCustomFormatScore}${settings.minimumCustomFormatScoreOverridden ? ', set here' : ', from the profile'}.` : 'Leave blank to use the profile’s own minimum.'}>
          <Input type="number" value={minimumScoreOverride} onChange={(event) => setMinimumScoreOverride(event.target.value)} placeholder={settings?.profileMinimumCustomFormatScore === undefined ? 'Same as profile' : `Same as profile (${settings.profileMinimumCustomFormatScore})`} />
        </Field>
      </SettingsGroup>
    </div>
    <div className="settings-footer">
      {message && <span className="footer-state">{message}</span>}
      <Button variant="primary" onClick={() => void save()} disabled={!dirty}>Save</Button>
    </div>
  </Panel>
}
