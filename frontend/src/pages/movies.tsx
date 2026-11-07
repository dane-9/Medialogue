import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { needsAttention, statusTone } from '../lib/status'
import { MovieInteractiveSearch } from '../components/MovieInteractiveSearch'
import { ChangeMatchModal } from '../components/ChangeMatchModal'
import { editionLabel, qualityMark } from '../lib/qualityMark'
import { releaseScoreBreakdown } from '../lib/releaseScore'
import { profileFormChanged, profileFormState } from '../lib/profileForm'
import { Link, useSearchParams } from 'react-router-dom'
import { Field, SettingsGroup } from '../components/settings'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { usePlexAvailable } from '../components/Shell'
import { Badge, Button, EmptyState, Input, Panel, Progress, Select } from '../components/ui'
import { LibraryHistoryModal } from '../components/LibraryHistoryModal'
import { moviesApi } from '../api/modules/movies'
import { settingsApi } from '../api/modules/settings'
import { systemApi } from '../api/modules/system'
import { upgradesApi } from '../api/modules/upgrades'
import { contextMenuSelection, normalizeMediaView, toggleIdSelection } from '../lib/uiState'
import { reuseUnchanged } from '../lib/reuseUnchanged'
import { VirtualGrid, useVirtualRows } from '../components/VirtualList'
import type { IncomingDownload, MediaProfileSettings, Movie, MovieRelease, QualityDefinition, QualityProfile, ReconciliationEvidence, ReleaseState, TorrentArchiveItem, Tag } from '../types'
import { LibraryFilter, MonitorMark, Page, PosterImage, QbitStatusBadge, defaultProfileLabel, formatEvidenceDate, useLiveLibraryRefresh } from './shared'

export function MoviesPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [movies, setMovies] = useState<Movie[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [view, setView] = useState<'cards' | 'table'>(() => normalizeMediaView(window.localStorage.getItem('medialogue.movies.view')))
  const [filter, setFilter] = useState('All')
  const [sort, setSort] = useState('title')
  const [query, setQuery] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [tagFilter, setTagFilter] = useState(searchParams.get('tag') ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [context, setContext] = useState<{ x: number; y: number } | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [cardBusy, setCardBusy] = useState<string | null>(null)

  const applyMovies = (items: Movie[]) => {
    setMovies((current) => reuseUnchanged(current, items))
    const ids = new Set(items.map((movie) => movie.id))
    setSelected((current) => [...current].every((id) => ids.has(id)) ? current : new Set([...current].filter((id) => ids.has(id))))
    setError('')
  }

  const loadMovies = async () => {
    try { applyMovies(await moviesApi.movies('', tagFilter, sort)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load movies.') }
    finally { setLoading(false) }
  }

  // Live events already trigger a reload, so the poll is only a fallback.
  useLiveLibraryRefresh(loadMovies, 60000)

  useEffect(() => {
    let alive = true
    moviesApi.movies('', tagFilter, sort).then((items) => { if (alive) applyMovies(items) })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load movies.') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [tagFilter, sort])

  useEffect(() => {
    let alive = true
    Promise.all([moviesApi.tags(), settingsApi.qualityProfiles()]).then(([tagRows, profileRows]) => {
      if (alive) { setTags(tagRows); setProfiles(profileRows) }
    }).catch(() => undefined)
    return () => { alive = false }
  }, [])

  const updateTagFilter = (value: string) => {
    setTagFilter(value)
    const next = new URLSearchParams(searchParams)
    if (value) next.set('tag', value); else next.delete('tag')
    setSearchParams(next, { replace: true })
  }

  const toggleSelected = useCallback((movieId: string) => setSelected((current) => toggleIdSelection(current, movieId)), [])

  const openContext = useCallback((event: React.MouseEvent, movieId: string) => {
    event.preventDefault()
    event.stopPropagation()
    setSelected((current) => contextMenuSelection(current, movieId))
    setContext({ x: event.clientX, y: event.clientY })
  }, [])

  const changeMovieView = (next: 'cards' | 'table') => {
    setView(next)
    window.localStorage.setItem('medialogue.movies.view', next)
  }

  const runBulk = async (action: Parameters<typeof moviesApi.bulkMovies>[0]['action'], options: { profileId?: string | null; tagIds?: string[] } = {}) => {
    const ids = [...selected]
    if (!ids.length) return
    setBulkBusy(true); setMessage('')
    try {
      const result = await moviesApi.bulkMovies({ movie_ids: ids, action, quality_profile_id: options.profileId, tag_ids: options.tagIds })
      const label = action.replaceAll('_', ' ')
      if ('job_id' in result) {
        setMessage(`${label} queued as job ${result.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
        const [job] = await systemApi.waitForJobs([result.job_id])
        const summary = job?.summary ?? {}
        const requested = Number(summary.requested ?? ids.length)
        const updated = Number(summary.updated ?? 0)
        if (job?.state === 'completed') setMessage(`${label}: ${updated} of ${requested} selected movie${requested === 1 ? '' : 's'} processed. Check Tasks for any per-title failures.`)
        else if (job?.state === 'cancelled') setMessage(`${label} cancelled after processing ${updated} of ${requested} selected movies.`)
        else setMessage(`${label} failed${job?.error ? `: ${job.error}` : '.'} Check Tasks for details.`)
      } else setMessage(`${label}: ${result.updated} of ${result.requested} selected movie${result.requested === 1 ? '' : 's'} updated.`)
      await loadMovies()
      setContext(null)
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Bulk action failed.')
    } finally { setBulkBusy(false) }
  }

  const createTag = async (name: string) => {
    const row = await moviesApi.createTag(name)
    setTags((items) => [...items, row].sort((a, b) => a.name.localeCompare(b.name)))
    return row
  }

  const runMovieCardAction = async (movie: Movie, action: 'upgrade' | 'plex' | 'monitor' | 'unmonitor') => {
    setCardBusy(movie.id)
    try {
      const accepted = action === 'upgrade'
        ? await upgradesApi.searchUpgrades({ movieId: movie.id })
        : action === 'plex'
          ? await moviesApi.recheckMoviePlex(movie.id)
          : await moviesApi.bulkMovies({ movie_ids: [movie.id], action })
      if ('jobId' in accepted) {
        setMessage(`Checking ${movie.title} for upgrades…`)
        const [job] = await systemApi.waitForJobs([accepted.jobId])
        const found = Number(job?.summary?.candidates_found ?? 0)
        if (job?.state === 'completed') setMessage(found ? `${found} upgrade ${found === 1 ? 'candidate' : 'candidates'} found for ${movie.title}. Review ${found === 1 ? 'it' : 'them'} in Upgrade Media.` : `No upgrade found for ${movie.title}.`)
        else setMessage(`Upgrade check for ${movie.title} ${job?.state ?? 'failed'}. Check Tasks for details.`)
      } else if ('job_id' in accepted) setMessage(`Plex recheck queued as job ${accepted.job_id.slice(0, 8)}….`)
      // Monitor/unmonitor needs no notice: the mark on the card already shows
      // the new state as soon as the reload lands.
      else setMessage('')
      await loadMovies()
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : `Could not ${action} ${movie.title}.`)
    } finally { setCardBusy(null) }
  }

  // Cards are memoised, so they get one stable handler that always calls the
  // latest version of the action.
  const cardActionRef = useRef(runMovieCardAction)
  cardActionRef.current = runMovieCardAction
  const onCardAction = useCallback((movie: Movie, action: 'upgrade' | 'plex' | 'monitor' | 'unmonitor') => cardActionRef.current(movie, action), [])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return movies.filter((movie) => {
      const matchesFilter = filter === 'All' || (filter === 'Problems' ? needsReview(movie) : filter === 'Unmonitored' ? movie.monitored === false : movie.status === filter)
      return matchesFilter && (!needle || movie.title.toLowerCase().includes(needle) || String(movie.year ?? '').includes(needle))
    })
  }, [movies, filter, query])
  return <Page title="Movies" subtitle="Your library, exactly where it was downloaded." action={<Button variant="secondary" icon="clock" onClick={() => setHistoryOpen(true)}>History</Button>}>
    {historyOpen && <LibraryHistoryModal mediaType="movies" onClose={() => setHistoryOpen(false)} />}
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${movies.length} ${movies.length === 1 ? 'movie' : 'movies'}…`} /></div>{selected.size > 0 && <span className="selection-count">{selected.size} selected · right-click for actions</span>}<div className="toolbar-spacer" /><Select className="library-control" selectedPrefix="SORT" aria-label="Sort movies" value={sort} onChange={(event) => setSort(event.target.value)}><option value="title">Title A–Z</option><option value="-title">Title Z–A</option><option value="year">Year oldest</option><option value="-year">Year newest</option><option value="quality">Quality low–high</option><option value="-quality">Quality high–low</option><option value="last_observed">Last observed oldest</option><option value="-last_observed">Last observed newest</option><option value="problems">Problems fewest</option><option value="-problems">Problems most</option></Select><LibraryFilter ariaLabel="Filter movies" value={filter} options={['All', 'Missing', 'Problems', 'Unmonitored', 'Present']} onChange={setFilter} /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => changeMovieView('cards')} aria-label="Card view"><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => changeMovieView('table')} aria-label="Table view"><Icon name="list" size={16} /></button></div></div>
    <div className="media-filter-row media-tag-filter">{tags.map((tag) => <button type="button" className={`filter-chip ${tagFilter === tag.name ? 'active' : ''}`} key={tag.id} onClick={() => updateTagFilter(tagFilter === tag.name ? '' : tag.name)}>{tag.name}</button>)}</div>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {error && <EmptyState title="Could not load the movie library" detail={error} />}
    {!error && !loading && (view === 'cards' ? <VirtualGrid className="media-grid" items={filtered} itemKey={(movie) => movie.id} renderItem={(movie) => <MovieCard movie={movie} selected={selected.has(movie.id)} onToggle={toggleSelected} onContext={openContext} busy={cardBusy === movie.id} onAction={onCardAction} />} /> : <MovieTable items={filtered} selected={selected} onToggle={toggleSelected} onContext={openContext} onTagClick={updateTagFilter} />)}
    {!error && !loading && !filtered.length && <EmptyState icon="clapper" title="No movies discovered yet" detail={tagFilter ? `No movies match the tag “${tagFilter}”.` : 'Add a Movie media folder, then scan it once to find your files.'} />}
    {context && <><div className="context-dismiss-layer" onClick={() => setContext(null)} onContextMenu={(event) => { event.preventDefault(); setContext(null) }} /><MovieBulkContextMenu position={context} count={selected.size} profiles={profiles} tags={tags} busy={bulkBusy} onRun={runBulk} onCreateTag={createTag} onClear={() => { setSelected(new Set()); setContext(null) }} /></>}
  </Page>
}

function MovieTagChips({ movie, onTagClick }: { movie: Movie; onTagClick?: (tag: string) => void }) {
  if (!movie.tags?.length) return null
  return <div className="tag-chip-row">{movie.tags.map((tag) => <span key={tag.id} className="tag-chip" role={onTagClick ? 'button' : undefined} tabIndex={onTagClick ? 0 : undefined} onClick={(event) => { if (!onTagClick) return; event.preventDefault(); event.stopPropagation(); onTagClick(tag.name) }} onKeyDown={(event) => { if (onTagClick && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onTagClick(tag.name) } }}>{tag.name}</span>)}</div>
}

const needsReview = (movie: Movie) => movie.status === 'Conflict' || movie.status === 'Duplicate' || (movie.problemCount ?? 0) > 0

const MovieCard = memo(function MovieCard({ movie, selected, onToggle, onContext, busy, onAction }: { movie: Movie; selected: boolean; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void; busy: boolean; onAction: (movie: Movie, action: 'upgrade' | 'plex' | 'monitor' | 'unmonitor') => Promise<void> }) {
  const plexAvailable = usePlexAvailable()
  const tier = qualityMark(movie.quality)
  const edition = editionLabel(movie.edition)
  const run = (action: 'upgrade' | 'plex' | 'monitor' | 'unmonitor') => (event: React.MouseEvent) => { event.preventDefault(); event.stopPropagation(); void onAction(movie, action) }
  return <article className={`media-card ${selected ? 'media-selected' : ''} ${needsAttention(movie.status) ? 'media-attention' : ''}`} onContextMenu={(event) => onContext(event, movie.id)}>
    <Link className="media-card-link" to={`/movies/${movie.id}`} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}>
      <div className={`poster poster-${movie.id} ${movie.poster ? 'poster-has-image' : 'poster-empty'}`}><PosterImage reference={movie.poster} title={movie.title} size="w342" />{!movie.poster && <span className="poster-fallback-title">{movie.title}</span>}<span className="poster-marks">{movie.incomingReplacement && <span className="incoming-mark"><Icon name="download" size={12} />{movie.incomingKind === 'replacement' ? 'Replacing' : 'Incoming'}</span>}<span className="media-card-actions" aria-label={`Actions for ${movie.title}`}><Tooltip asChild content="Check for Upgrade"><button className="icon-button" aria-label={`Check ${movie.title} for an upgrade`} disabled={busy} onClick={run('upgrade')}><Icon name="upgrade-media" size={13} /></button></Tooltip>{plexAvailable && <Tooltip asChild content="Recheck Plex"><button className="icon-button" aria-label={`Recheck Plex for ${movie.title}`} disabled={busy} onClick={run('plex')}><Icon name="refresh" size={13} /></button></Tooltip>}</span><MonitorMark monitored={movie.monitored !== false} title={movie.title} busy={busy} onToggle={(next) => void onAction(movie, next ? 'monitor' : 'unmonitor')} /></span>{selected && <span className="selection-mark"><Icon name="check" size={14} /></span>}</div>
      <div className="media-card-body"><div className="media-card-meta">{needsAttention(movie.status) && <Badge tone={statusTone(movie.status)} className="media-card-primary-badge">{movie.status}</Badge>}<QbitStatusBadge status={movie.torrentStatus} compact />{plexAvailable && <Tooltip content={`Plex ${movie.plex}`}><Badge tone={statusTone(movie.plex)} className="media-card-secondary-badge">Plex</Badge></Tooltip>}</div><div className="media-card-title"><strong>{movie.title}</strong>{/* An edition is long enough that sharing a row with the tier truncated one
        of them, so it takes the year's row and the tier drops to its own. */}
        <span className="media-card-sub">{movie.year || 'Year unknown'}{edition && <em className="media-card-quality">{edition}</em>}{!edition && tier && <em className="media-card-quality">{tier}</em>}</span>{edition && tier && <span className="media-card-sub media-card-tier">{tier}</span>}</div></div>
    </Link>
  </article>
})

function MovieTable({ items, selected, onToggle, onContext, onTagClick }: { items: Movie[]; selected: Set<string>; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void; onTagClick: (tag: string) => void }) {
  const plexAvailable = usePlexAvailable()
  const rows = useVirtualRows(items, plexAvailable ? 9 : 8)
  return <Panel className="table-panel"><table className="data-table"><thead><tr><th>Title</th><th>State</th><th>Current release</th><th>qBittorrent</th>{plexAvailable && <th>Plex</th>}<th>Tags</th><th>Confidence</th><th>Location</th><th /></tr></thead><tbody ref={rows.bodyRef}>{rows.before}{rows.visible.map((movie) => <tr key={movie.id} className={selected.has(movie.id) ? 'row-selected' : ''} onContextMenu={(event) => onContext(event, movie.id)} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}><td><Link className="table-title" to={`/movies/${movie.id}`}><span className={`table-poster poster-${movie.id}`}><PosterImage reference={movie.poster} title={movie.title} size="w92" /></span><MonitorMark monitored={movie.monitored !== false} />{movie.title}<span className="muted">{movie.year}</span></Link></td><td><Badge tone={statusTone(movie.status)}>{movie.status}</Badge></td><td>{movie.quality}{movie.edition && <span className="table-sub">{movie.edition}</span>}</td><td><QbitStatusBadge status={movie.torrentStatus} showState /></td>{plexAvailable && <td><Badge tone={statusTone(movie.plex)}>{movie.plex}</Badge></td>}<td><MovieTagChips movie={movie} onTagClick={onTagClick} /></td><td><span className="confidence">{movie.confidence}%</span></td><td className="path-cell">{movie.location}</td><td>{selected.has(movie.id) ? <Icon name="check" size={15} /> : <Icon name="chevron" size={15} />}</td></tr>)}{rows.after}</tbody></table></Panel>
}

function MovieBulkContextMenu({ position, count, profiles, tags, busy, onRun, onCreateTag, onClear }: { position: { x: number; y: number }; count: number; profiles: QualityProfile[]; tags: Tag[]; busy: boolean; onRun: (action: Parameters<typeof moviesApi.bulkMovies>[0]['action'], options?: { profileId?: string | null; tagIds?: string[] }) => Promise<void>; onCreateTag: (name: string) => Promise<Tag>; onClear: () => void }) {
  const plexAvailable = usePlexAvailable()
  const [profileId, setProfileId] = useState('')
  const [tagId, setTagId] = useState('')
  const [newTag, setNewTag] = useState('')
  const [localMessage, setLocalMessage] = useState('')
  const left = Math.max(12, Math.min(position.x, window.innerWidth - 340))
  const top = Math.max(12, Math.min(position.y, window.innerHeight - 540))
  const create = async () => {
    if (!newTag.trim()) return
    try { const row = await onCreateTag(newTag.trim()); setTagId(row.id); setNewTag(''); setLocalMessage(`Created ${row.name}.`) }
    catch (reason) { setLocalMessage(reason instanceof Error ? reason.message : 'Could not create tag.') }
  }
  return <div className="bulk-context-menu" style={{ left, top }} onClick={(event) => event.stopPropagation()}>
    <div className="bulk-context-head"><div><div className="eyebrow">BULK MOVIE ACTIONS</div><strong>{count} selected</strong></div><button className="icon-button" onClick={onClear} aria-label="Clear selection"><Icon name="close" size={14} /></button></div>
    <div className="context-action-grid"><button onClick={() => void onRun('monitor')} disabled={busy}><Icon name="check" size={14} />Monitor</button><button onClick={() => void onRun('unmonitor')} disabled={busy}><Icon name="pause" size={14} />Unmonitor</button>{plexAvailable && <button onClick={() => void onRun('recheck_plex')} disabled={busy}><Icon name="refresh" size={14} />Recheck Plex</button>}<button onClick={() => void onRun('reevaluate_parser')} disabled={busy}><Icon name="activity" size={14} />Re-evaluate parser</button><button onClick={() => void onRun('reevaluate_custom_formats')} disabled={busy}><Icon name="sliders" size={14} />Re-evaluate CFs</button></div>
    <div className="context-section"><span className="field-label">Quality Profile</span><div className="context-inline"><Select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">{defaultProfileLabel(profiles)}</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</Select><Button variant="ghost" disabled={busy} onClick={() => void onRun('change_profile', { profileId: profileId || null })}>Apply</Button></div><small>Per-title score/minimum overrides are preserved.</small></div>
    <div className="context-section"><span className="field-label">Tags</span><div className="context-inline"><Select value={tagId} onChange={(event) => setTagId(event.target.value)}><option value="">Choose tag…</option>{tags.map((tag) => <option value={tag.id} key={tag.id}>{tag.name}</option>)}</Select></div><div className="context-inline"><Button variant="ghost" disabled={busy || !tagId} onClick={() => void onRun('add_tags', { tagIds: [tagId] })}>Add</Button><Button variant="ghost" disabled={busy || !tagId} onClick={() => void onRun('remove_tags', { tagIds: [tagId] })}>Remove</Button></div><div className="context-inline"><Input value={newTag} onChange={(event) => setNewTag(event.target.value)} placeholder="New tag…" onKeyDown={(event) => { if (event.key === 'Enter') void create() }} /><Button variant="ghost" disabled={!newTag.trim()} onClick={() => void create()}>Create</Button></div></div>
    {localMessage && <small className="context-message">{localMessage}</small>}
    <div className="context-foot">Ctrl/Cmd-click additional movies before right-clicking to build a selection.</div>
  </div>
}

function releaseStateHelp(state: ReleaseState): string {
  if (state === 'duplicate') return 'Another registered copy has the same edition. Remove one outside Medialogue and recheck.'
  if (state === 'conflict') return 'The evidence for this copy disagrees with another source.'
  if (state === 'missing') return 'The files were registered once but are not on disk now.'
  if (state === 'replaced') return 'A newer copy took over this slot. The old evidence is kept.'
  if (state === 'removed') return 'This copy is no longer part of the library.'
  return 'This copy is registered and present.'
}

function releaseStateTone(state: ReleaseState) {
  if (state === 'current') return 'green'
  if (state === 'missing' || state === 'replaced' || state === 'removed') return 'amber'
  if (state === 'duplicate' || state === 'conflict') return 'red'
  return 'neutral'
}

/** Whether a torrent for this copy is still loaded in a download client.
 *
 * The question before deleting a copy is "am I still seeding this", and the
 * answer already exists per release in the torrent history; it was only ever
 * shown grouped at the bottom of the page.
 */
function ReleaseEvidenceRow({ release }: { release: MovieRelease; torrents?: TorrentArchiveItem[] }) {
  const directory = release.directories.find((item) => item.exists) ?? release.directories[0]
  const folder = directory?.path.split(/[\\/]/).filter(Boolean).pop()
  const filename = directory?.mediaFilename
  // The file is usually the folder name with an extension on the end, so it only
  // earns its own line when it actually says something different.
  const separateFile = filename && folder && filename.replace(/\.[^.]+$/, '').toLowerCase() !== folder.toLowerCase() ? filename : undefined
  const score = release.currentCustomFormatScore
  const scoreChanged = release.originalCustomFormatScore !== undefined && score !== undefined && release.originalCustomFormatScore !== score
  const breakdown = releaseScoreBreakdown(release.parser)
  return <div className="release-evidence-row">
    <Tooltip asChild content={directory?.storageRoot ? `Media folder: ${directory.storageRoot}` : 'No media folder recorded'}>
      <div className={`release-icon ${directory?.storageRoot ? 'release-icon-labelled' : ''}`}>
        <Icon name="clapper" size={18} />
        {directory?.storageRoot && <span>{directory.storageRoot}</span>}
      </div>
    </Tooltip>
    <div className="release-main">
      <strong title={directory?.path}>{folder ?? release.name}</strong>
      {separateFile && <small>{separateFile}</small>}
    </div>
    {/* A number belongs in a column, not in a pill among the format tags: as a
        pill it read as another attribute of the release rather than as the
        figure you compare between releases. Hovering shows what built it. */}
    <Tooltip asChild placement="below" delay={250} content={<span className="cf-breakdown">
      <span className="cf-breakdown-head"><strong>Custom Formats</strong><small>{breakdown.profileName ?? 'Current profile'}</small></span>
      {breakdown.matched.length
        ? <span className="cf-breakdown-rows">
            {breakdown.matched.map((format) => <span key={format.id} className="cf-breakdown-row cf-breakdown-pair">
              <span>{format.name}</span>
              <b className={format.contribution > 0 ? 'cf-plus' : format.contribution < 0 ? 'cf-minus' : 'cf-zero'}>{format.contribution > 0 ? '+' : ''}{format.contribution}</b>
            </span>)}
          </span>
        : <small>No Custom Formats matched this release.</small>}
      <span className="cf-breakdown-row cf-breakdown-pair cf-breakdown-total">
        <span>Total</span>
        <b>{breakdown.total ?? score ?? 0}</b>
      </span>
      {scoreChanged && <small>Scored {release.originalCustomFormatScore} when downloaded.</small>}
    </span>}>
      <div className="release-score">
        <em>CF score</em>
        <strong className={score === undefined ? '' : score < 0 ? 'score-negative' : score > 0 ? 'score-positive' : 'score-zero'}>{score ?? '—'}</strong>
        {scoreChanged && <small>was {release.originalCustomFormatScore}</small>}
      </div>
    </Tooltip>
    {/* Only exceptions get called out on the right. Saying "current, 100%" on
        every healthy row taught nobody what either number meant. */}
    <div className="release-tags">
      <QbitStatusBadge status={release.torrentStatus} showState />
      {directory && !directory.exists && <Tooltip content="This folder was not found on disk at the last file check"><Badge tone="amber">File missing</Badge></Tooltip>}
      {release.state !== 'current' && <Tooltip content={releaseStateHelp(release.state)}><Badge tone={releaseStateTone(release.state) as 'green' | 'amber' | 'red' | 'neutral'}>{release.state}</Badge></Tooltip>}
      {release.confidence !== undefined && release.confidence < 100 && <Tooltip content="How sure Medialogue is that this release belongs to this movie"><Badge tone="amber">{Math.round(release.confidence)}% match</Badge></Tooltip>}
    </div>
  </div>
}

function IncomingReplacement({ incoming }: { incoming: IncomingDownload }) {
  const label = incoming.kind === 'replacement' ? 'INCOMING REPLACEMENT' : 'INCOMING RELEASE'
  return <div className="incoming-replacement"><div className="incoming-icon"><Icon name="download" size={18} /></div><div className="incoming-copy"><div className="eyebrow">{label}</div><strong>{incoming.quality || incoming.name}</strong><span>{incoming.name}{incoming.edition ? ` · ${incoming.edition}` : ''}</span><div className="incoming-meta"><span>{incoming.client}</span>{incoming.state && <span>{incoming.state}</span>}{incoming.eta && <span>ETA {incoming.eta}</span>}</div><Progress value={incoming.progress} tone="blue" /></div><strong className="incoming-percent">{Math.round(incoming.progress)}%</strong></div>
}

function torrentTrackerLabel(tracker?: string) {
  if (!tracker) return undefined
  try { return new URL(tracker).hostname }
  catch { return tracker }
}

function torrentHistoryGroups(items: TorrentArchiveItem[]) {
  const groups = new Map<string, TorrentArchiveItem[]>()
  items.forEach((item) => {
    const key = item.releaseId || `${item.releaseName || item.torrentName}|${item.quality || ''}|${item.edition || ''}`
    groups.set(key, [...(groups.get(key) ?? []), item])
  })
  return [...groups.values()]
}

function TorrentHistoryEntry({ torrent, nested = false }: { torrent: TorrentArchiveItem; nested?: boolean }) {
  const tracker = torrentTrackerLabel(torrent.tracker)
  return <div className={`history-row ${nested ? 'torrent-history-member' : ''}`}><span className="history-line" /><div><strong>{torrent.releaseName ?? torrent.torrentName}</strong><span>{tracker ? `${tracker} · ` : ''}{torrent.infoHash.slice(0, 12)}… · {torrent.originalDownloadClient ?? 'Unknown client'} · qBit {torrent.qbitPresent ? 'present' : 'removed'}</span></div><Badge tone={torrent.archiveState === 'archived' ? 'green' : torrent.archiveState === 'failed' ? 'red' : 'amber'}>{torrent.archiveState.replaceAll('_', ' ')}</Badge></div>
}

function TorrentHistoryPanel({ items }: { items?: TorrentArchiveItem[] }) {
  const groups = torrentHistoryGroups(items ?? [])
  return <Panel title="Torrent history">{groups.length ? groups.map((group) => {
    const first = group[0]
    if (group.length === 1) return <TorrentHistoryEntry key={first.id} torrent={first} />
    const presentCount = group.filter((item) => item.qbitPresent).length
    const trackers = [...new Set(group.map((item) => torrentTrackerLabel(item.tracker)).filter(Boolean))]
    return <details className="torrent-history-group" key={first.releaseId || first.id}>
      <summary className="torrent-history-summary"><span className="history-line" /><div><strong>{first.releaseName ?? first.torrentName}</strong><span>Cross-seeded · {group.length} torrents · {presentCount} qBit present</span>{trackers.length > 0 && <small>{trackers.join(' · ')}</small>}</div><Badge tone="blue">{group.length} torrents</Badge></summary>
      <div className="torrent-history-members">{group.map((torrent) => <TorrentHistoryEntry key={torrent.id} torrent={torrent} nested />)}</div>
    </details>
  }) : <div className="history-empty">No torrent recovery history is associated with this movie yet.</div>}</Panel>
}

function evidenceFromMovie(movie: Movie): ReconciliationEvidence[] {
  const evidence = [...(movie.problems ?? [])]
  const aggregate = movie.reconciliation
  if (aggregate?.qbitMediaDisagreement && !evidence.some((item) => item.code === 'QBIT_MEDIA_DISAGREEMENT')) evidence.push({ code: 'QBIT_MEDIA_DISAGREEMENT', title: 'qBittorrent / media disagreement', detail: 'qBittorrent reports a completed item but the expected media path is not present.', severity: 'high', source: 'qBittorrent + filesystem' })
  if ((aggregate?.rootOffline || movie.rootHealth === 'unavailable') && !evidence.some((item) => item.code === 'ROOT_OFFLINE')) evidence.push({ code: 'ROOT_OFFLINE', title: 'Media folder offline', detail: `${aggregate?.rootAffectedCount ?? movie.rootAffectedCount ?? 0} media items are affected; absence is not treated as Missing while the folder is offline.`, severity: 'high', source: 'Media folder' })
  return evidence
}

function MovieProfilePanel({ resourceId }: { resourceId: string }) {
  const [settings, setSettings] = useState<MediaProfileSettings | null>(null)
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [qualities, setQualities] = useState<QualityDefinition[]>([])
  const [profileId, setProfileId] = useState('')
  const [minimumOverrideId, setMinimumOverrideId] = useState('')
  const [minimumScoreOverride, setMinimumScoreOverride] = useState('')
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const load = async () => {
    try {
      const [value, profileRows, qualityRows] = await Promise.all([settingsApi.movieProfileSettings(resourceId), settingsApi.qualityProfiles(), settingsApi.qualityDefinitions()])
      setSettings(value); setProfiles(profileRows); setQualities(qualityRows)
      const form = profileFormState(value)
      setProfileId(form.profileId)
      setMinimumOverrideId(form.minimumOverrideId)
      setMinimumScoreOverride(form.minimumScoreOverride)
      setOverrides(Object.fromEntries(value.customFormatScores.filter((item) => item.overrideScore !== undefined).map((item) => [item.customFormatId, item.overrideScore ?? 0])))
      setMessage('')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load profile settings.') }
  }
  useEffect(() => { void load() }, [resourceId])
  const save = async () => {
    setBusy(true); setMessage('Saving profile assignment…')
    try {
      const value = await settingsApi.saveMovieProfileSettings(resourceId, {
        quality_profile_id: profileId || null,
        minimum_quality_definition_override_id: minimumOverrideId || null,
        minimum_custom_format_score_override: minimumScoreOverride === '' ? null : Number(minimumScoreOverride),
        custom_format_score_overrides: overrides,
        expected_revision: settings?.revision ?? 0,
      })
      setSettings(value); setMessage('Profile saved. Stored release scores were re-evaluated.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save profile settings.') }
    finally { setBusy(false) }
  }
  const dirty = profileFormChanged(settings, { profileId, minimumOverrideId, minimumScoreOverride })
  return <Panel title="Quality profile" className="profile-panel"
    action={<Button variant="primary" onClick={() => void save()} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save profile settings'}</Button>}>
    <div className="settings-form profile-form">
      <SettingsGroup>
        <Field label="Quality profile" help="Which profile ranks and scores releases for this movie.">
          <Select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
            <option value="">{defaultProfileLabel(profiles)}</option>
            {profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}
          </Select>
        </Field>
        <Field label="Minimum quality" help={settings?.minimumQuality ? `Currently ${settings.minimumQuality.name}${settings.minimumQualityOverridden ? ', set here' : ', from the profile'}.` : 'Releases below this are rejected for automatic selection.'}>
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
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
  </Panel>
}

export function MovieDetailPage({ id }: { id: string }) {
  const [detailSearchParams, setDetailSearchParams] = useSearchParams()
  const openSearchFromQuery = detailSearchParams.get('search') === '1'
  const plexAvailable = usePlexAvailable()
  const [movie, setMovie] = useState<Movie | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [matchOpen, setMatchOpen] = useState(false)
  useEffect(() => {
    if (!openSearchFromQuery) return
    setSearchOpen(true)
    // One-shot deep link: drop the flag so reloads and back-navigation do not
    // reopen the search dialog.
    setDetailSearchParams((current) => { const next = new URLSearchParams(current); next.delete('search'); return next }, { replace: true })
  }, [id, openSearchFromQuery, setDetailSearchParams])
  const load = async (reportError = true) => {
    try { setMovie(await moviesApi.movie(id)); if (reportError) setError('') }
    catch (reason) { if (reportError) setError(reason instanceof Error ? reason.message : 'Movie not found.') }
  }
  useEffect(() => { void load() }, [id])
  useLiveLibraryRefresh(() => load(false))
  const setMonitored = async (monitored: boolean) => {
    setBusy(true); setMessage(monitored ? 'Monitoring…' : 'Unmonitoring…')
    try {
      await moviesApi.bulkMovies({ movie_ids: [id], action: monitored ? 'monitor' : 'unmonitor' })
      await load()
      setMessage(monitored ? 'Now monitored. Medialogue will look for upgrades.' : 'No longer monitored. Nothing is searched for automatically.')
    }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not change monitoring.') }
    finally { setBusy(false) }
  }
  const recheckPlex = async () => {
    setBusy(true); setMessage('Starting Plex recheck…')
    try {
      const accepted = await moviesApi.recheckMoviePlex(id)
      setMessage(`Plex recheck queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
      const [job] = await systemApi.waitForJobs([accepted.job_id])
      await load()
      const summary = job?.summary ?? {}
      const count = (key: string) => Number(summary[key] ?? 0)
      if (job?.state === 'completed') setMessage(`Plex recheck complete: ${count('matched_releases')} matched, ${count('not_found_releases')} not in Plex, ${count('multiple_version_releases')} multiple-version matches, ${count('conflict_releases')} conflicts.`)
      else if (job?.state === 'cancelled') setMessage('Plex recheck cancelled. No further Plex verification was performed.')
      else setMessage(`Plex recheck failed${job?.error ? `: ${job.error}` : '.'} Check Tasks for details.`)
    }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start Plex recheck.') }
    finally { setBusy(false) }
  }
  const refreshEvidence = async () => {
    setBusy(true); setMessage('Rechecking files…')
    try {
      const refresh = await systemApi.runReconciliation()
      const jobIds = [...new Set([...(refresh.job_ids ?? []), ...(refresh.active_job_ids ?? [])].map(String))]
      const jobs = jobIds.length ? await systemApi.waitForJobs(jobIds) : []
      await load()
      const failures = jobs.filter((job) => job.state !== 'completed')
      if (failures.length) setMessage(`File check finished with ${failures.length} failed, cancelled, or interrupted job${failures.length === 1 ? '' : 's'}. Review Tasks for details.`)
      else if (refresh.uninitialized_root_ids?.length) setMessage(`File check complete. ${refresh.uninitialized_root_ids.length} media folder${refresh.uninitialized_root_ids.length === 1 ? ' was' : 's were'} skipped; scan ${refresh.uninitialized_root_ids.length === 1 ? 'it' : 'them'} once first.`)
      else if (!jobs.length) setMessage('Nothing needed rechecking. No files were changed.')
      else setMessage('Recheck complete. No files were changed.')
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Could not recheck the files.')
    } finally { setBusy(false) }
  }
  if (error) return <Page title="Movie unavailable" subtitle={error}><EmptyState title="Could not load this movie" detail={error} /></Page>
  if (!movie) return <Page title="Loading movie" subtitle="Retrieving current and historical release state."><EmptyState title="Loading…" detail="Loading this movie." /></Page>
  const releases = movie.releasesDetail ?? []
  const evidence = evidenceFromMovie(movie)
  const currentReleases = releases.filter((release) => release.state === 'current' || release.state === 'duplicate' || release.state === 'conflict')
  const historyReleases = releases.filter((release) => release.state === 'replaced' || release.state === 'removed' || release.state === 'missing')
  const history = movie.recentEvents ?? []
  const currentSummary = <div className="release-row"><div className="release-icon"><Icon name="clapper" size={18} /></div><div className="release-main"><strong>{movie.quality}{movie.edition ? ` · ${movie.edition}` : ''}</strong><span>{movie.location}</span></div><Badge tone={statusTone(movie.status)}>{movie.status}</Badge></div>
  return <Page title={movie.title} subtitle={`${movie.year} · ${movie.tmdbId ? `TMDB ${movie.tmdbId}` : `Internal ID ${movie.id}`}`} back="Back to Movies">
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {(movie.rootHealth === 'unavailable' || movie.reconciliation?.rootOffline) && <div className="reconciliation-banner reconciliation-banner-red"><Icon name="alert" size={17} /><div><strong>Media Folder Offline</strong><span>{movie.reconciliation?.rootAffectedCount ?? movie.rootAffectedCount ?? 0} media affected. Missing grace is held until the folder is reachable.</span></div></div>}
    <div className="detail-single">
      <div className="detail-top">
      <Panel className="detail-hero">
        <div className="detail-poster"><PosterImage reference={movie.poster} title={movie.title} /></div>
        <div className="detail-intro">
          <h2>{movie.title} <span className="detail-year">({movie.year})</span></h2>
          {/* Only states worth acting on. "Present" is the default for a movie
              you are looking at, and a confidence figure told nobody anything
              they could do something about. */}
          {(needsAttention(movie.status) || plexAvailable || movie.monitored === false) && <div className="badge-row">
            {needsAttention(movie.status) && <Badge tone={statusTone(movie.status)}>{movie.status}</Badge>}
            {plexAvailable && <Badge tone={statusTone(movie.plex)}>Plex {movie.plex}</Badge>}
            {movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}
          </div>}
          {movie.tags?.length ? <div className="tag-chip-row detail-tag-row">{movie.tags.map((tag) => <Link key={tag.id} className="tag-chip" to={`/movies?tag=${encodeURIComponent(tag.name)}`}>{tag.name}</Link>)}</div> : null}
          <p className="detail-description">{movie.overview ?? 'Your files are the source of truth. Rechecking never moves or deletes media, and old paths are kept.'}</p>
          {/* The facts the sidebar used to hold, inline and unlabelled where the
              label would only repeat the value. */}
          <div className="detail-facts">
            <span><em>Last observed</em><strong>{formatEvidenceDate(movie.lastObservedAt)}</strong></span>
            {movie.tmdbId ? <span><em>TMDB</em><strong>{movie.tmdbId}</strong></span> : null}
          </div>
          {/* Everything you can do to this movie sits in one row, beside the
              title it acts on, rather than split across the page header. */}
          <div className="detail-actions">
            <Button variant="primary" icon="search" onClick={() => setSearchOpen(true)}>Interactive search</Button>
            <Button variant="secondary" icon="refresh" onClick={refreshEvidence} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh evidence'}</Button>
            {plexAvailable && <Button variant="secondary" icon="refresh" onClick={recheckPlex} disabled={busy}>{busy ? 'Checking Plex…' : 'Recheck Plex'}</Button>}
            <Button variant="secondary" icon="external" onClick={() => setMatchOpen(true)}>Change match</Button>
            {/* The hero already reported this state; now it can be changed
                from the same place it is shown. */}
            <Button variant="secondary" icon={movie.monitored === false ? 'monitored' : 'monitored-off'} onClick={() => void setMonitored(movie.monitored === false)} disabled={busy}>
              {movie.monitored === false ? 'Monitor' : 'Unmonitor'}
            </Button>
          </div>
        </div>
      </Panel>
      <MovieProfilePanel resourceId={id} />
      </div>
      {/* Problems appear only when there are problems. A titled panel saying
          nothing is wrong was the common case and pure furniture. */}
      {evidence.length > 0 && <Panel title="Problems" note={`${evidence.length} NEED ATTENTION`}>
        {evidence.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}>
          <div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div>
          <div><strong>{item.title}</strong><span>{item.detail}</span></div>
        </div>)}
      </Panel>}
      {movie.incoming && <IncomingReplacement incoming={movie.incoming} />}
      <Panel title="Current releases">{currentReleases.length ? currentReleases.map((release) => <ReleaseEvidenceRow key={release.id || release.name} release={release} torrents={movie.torrentHistory} />) : currentSummary}</Panel>
      <Panel title="Release history">{history.length ? history.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{formatEvidenceDate(event.createdAt)}</span></div><Badge tone={event.type.includes('replaced') ? 'purple' : event.type.includes('duplicate') || event.type.includes('conflict') ? 'red' : 'neutral'}>{event.type.replaceAll('.', ' ')}</Badge></div>) : historyReleases.map((release) => <div className="history-row" key={release.id || release.name}><span className="history-line" /><div><strong>{release.name}</strong><span>{release.state} · first observed {formatEvidenceDate(release.firstSeenAt)}</span></div><Badge tone={releaseStateTone(release.state) as 'green' | 'amber' | 'red' | 'neutral'}>Release</Badge></div>)}{!history.length && !historyReleases.length && <div className="history-empty">No persisted release events yet. New replacement and reappearance events will appear here.</div>}</Panel>
      <TorrentHistoryPanel items={movie.torrentHistory} />
    </div>
    {searchOpen && <MovieInteractiveSearch
      movieId={id}
      title={`${movie.title}${movie.year ? ` (${movie.year})` : ''}`}
      onClose={() => setSearchOpen(false)}
      onStarted={(text) => { setMessage(text); void load() }}
    />}
    {matchOpen && <ChangeMatchModal
      movieId={id}
      title={movie.title}
      year={movie.year}
      currentTmdbId={movie.tmdbId}
      onClose={() => setMatchOpen(false)}
      onMatched={(text) => { setMessage(text); void load() }}
    />}
  </Page>
}
