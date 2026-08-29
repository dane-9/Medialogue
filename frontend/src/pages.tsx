import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { badgeColor } from './lib/badgeColor'
import { eventLabel, eventStamp } from './lib/eventLabel'
import { formatAge } from './lib/age'
import { isGeneratedRootName, nextRootName } from './lib/rootName'
import { moveItem } from './lib/reorder'
import { CONFIDENT, reviewRoots } from './lib/reviewTriage'
import { rootBrowseStart, rootDraftChanged, rootPathAfterBrowse } from './lib/rootDraft'
import type { RootDraft } from './lib/rootDraft'
import { describeScan } from './lib/scanProgress'
import { needsAttention, statusTone } from './lib/status'
import { pathParts } from './lib/pathParts'
import { releaseAudio, releaseFlags, releaseHdr, releaseVideoCodec } from './lib/releaseMedia'
import { qualityMark } from './lib/qualityMark'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Field, Note, SaveFooter, SectionHead, Secret, failed, ok, pending } from './components/settings'
import type { Message, StatusTone } from './components/settings'
import { Icon } from './components/Icon'
import { Tooltip } from './components/Tooltip'
import { Dropdown } from './components/Dropdown'
import { useConfirm } from './components/Confirm'
import { Modal } from './components/Modal'
import { PageTopbar, usePlexAvailable } from './components/Shell'
import { Badge, Button, EmptyState, EnabledButton, Input, Panel, Progress, Segmented, Select, Slider, Stat } from './components/ui'
import { ApiError, api } from './api/client'
import CustomFormatsPageView from './CustomFormatsPage'
import { contextMenuSelection, normalizeMediaView, toggleIdSelection } from './lib/uiState'
import { updateUrlParams, useUrlNumber, useUrlState } from './lib/urlState'
import type { EpisodeOrdering, CustomFormat, Download, DownloadClient, IncomingDownload, Indexer, IndexerScope, LibrarySummary, MediaProfileSettings, Movie, MediaDiscovery, MovieEditionUpdate, MovieRelease, Problem, QualityDefinition, QualityProfile, ReconciliationEvidence, ReleaseState, Show, Season, Episode, EpisodeMedia, TMDBShowLookup, TMDBMovieLookup, FilesystemListing, StorageRoot, RemotePathMapping, TorrentArchiveItem, EventHistoryItem, Job, RecoveryCapabilities, ScheduledTask, Tag } from './types'
import { parseJobStatusLiveEvent } from './validation/liveEvents'
import prowlarrIcon from './assets/prowlarr.svg'
import jackettIcon from './assets/jackett.svg'

function tmdbPosterUrl(reference?: string) {
  if (!reference) return undefined
  if (/^https?:\/\//i.test(reference)) return reference
  return `https://image.tmdb.org/t/p/w500${reference.startsWith('/') ? reference : `/${reference}`}`
}

function PosterImage({ reference, title }: { reference?: string; title: string }) {
  const src = tmdbPosterUrl(reference)
  if (!src) return null
  return <img className="poster-image" src={src} alt={`${title} poster`} loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = 'none' }} />
}

const libraryInvalidationEvents = [
  'scan.progress', 'scan.completed', 'scan.failed',
  'media.present', 'media.missing', 'media.reappeared',
  'plex.matched', 'plex.verified', 'plex.not_found', 'plex.pending', 'plex.conflict', 'plex.multiple_versions', 'plex.unavailable', 'plex.sync_completed',
  'problem.created', 'problem.updated', 'problem.resolved', 'problem.deleted',
  'show.metadata_refreshed', 'movie.monitoring_updated', 'movie.tags_updated',
] as const

function useLiveLibraryRefresh(refresh: () => void | Promise<void>, pollIntervalMs = 15000, watchScanJobs = false) {
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh

  useEffect(() => {
    let refreshTimer: number | undefined
    let stopped = false
    const run = () => {
      if (!stopped && document.visibilityState !== 'hidden') void refreshRef.current()
    }
    // Throttle high-frequency scan progress to at most a couple of REST reads
    // per second while still making newly discovered rows appear during a scan.
    const invalidate = () => {
      if (refreshTimer !== undefined) return
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined
        run()
      }, 500)
    }

    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    libraryInvalidationEvents.forEach((name) => stream.addEventListener(name, invalidate))
    const scanJobChanged = (event: MessageEvent<string>) => {
      if (!watchScanJobs) return
      const payload = parseJobStatusLiveEvent(event.data)
      if (payload && (payload.data.job_type === 'storage_root_scan' || payload.data.job_type === 'reconciliation')) invalidate()
    }
    if (watchScanJobs) stream.addEventListener('job.status', scanJobChanged)
    const poll = window.setInterval(run, pollIntervalMs)
    const onVisibility = () => { if (document.visibilityState === 'visible') invalidate() }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      stopped = true
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
      window.clearInterval(poll)
      document.removeEventListener('visibilitychange', onVisibility)
      if (watchScanJobs) stream.removeEventListener('job.status', scanJobChanged)
      stream.close()
    }
  }, [pollIntervalMs, watchScanJobs])
}

function FilterChips({ value, options, onChange, className = '' }: { value: string; options: string[]; onChange: (value: string) => void; className?: string }) {
  return <div className={`media-filter-row ${className}`} role="group" aria-label="Filter library">{options.map((option) => <button type="button" className={`filter-chip ${value === option ? 'active' : ''}`} key={option} onClick={() => onChange(option)}>{option}</button>)}</div>
}

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
  const [tagFilter, setTagFilter] = useState(searchParams.get('tag') ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [context, setContext] = useState<{ x: number; y: number } | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [cardBusy, setCardBusy] = useState<string | null>(null)

  const loadMovies = async () => {
    try {
      const items = await api.movies(query, tagFilter, sort)
      setMovies(items)
      setSelected((current) => new Set([...current].filter((id) => items.some((movie) => movie.id === id))))
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load movies.')
    } finally { setLoading(false) }
  }

  useLiveLibraryRefresh(loadMovies)

  useEffect(() => {
    let alive = true
    const timer = window.setTimeout(() => {
      api.movies(query, tagFilter, sort).then((items) => {
        if (!alive) return
        setMovies(items)
        setSelected((current) => new Set([...current].filter((id) => items.some((movie) => movie.id === id))))
        setError('')
      }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load movies.') }).finally(() => { if (alive) setLoading(false) })
    }, 150)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [query, tagFilter, sort])

  useEffect(() => {
    let alive = true
    Promise.all([api.tags(), api.qualityProfiles()]).then(([tagRows, profileRows]) => {
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

  const toggleSelected = (movieId: string) => setSelected((current) => toggleIdSelection(current, movieId))

  const openContext = (event: React.MouseEvent, movieId: string) => {
    event.preventDefault()
    event.stopPropagation()
    setSelected((current) => contextMenuSelection(current, movieId))
    setContext({ x: event.clientX, y: event.clientY })
  }

  const changeMovieView = (next: 'cards' | 'table') => {
    setView(next)
    window.localStorage.setItem('medialogue.movies.view', next)
  }

  const runBulk = async (action: Parameters<typeof api.bulkMovies>[0]['action'], options: { profileId?: string | null; tagIds?: string[] } = {}) => {
    const ids = [...selected]
    if (!ids.length) return
    setBulkBusy(true); setMessage('')
    try {
      const result = await api.bulkMovies({ movie_ids: ids, action, quality_profile_id: options.profileId, tag_ids: options.tagIds })
      const label = action.replaceAll('_', ' ')
      if ('job_id' in result) {
        setMessage(`${label} queued as job ${result.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
        const [job] = await api.waitForJobs([result.job_id])
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
    const row = await api.createTag(name)
    setTags((items) => [...items, row].sort((a, b) => a.name.localeCompare(b.name)))
    return row
  }

  const runMovieCardAction = async (movie: Movie, action: 'search' | 'plex' | 'monitor' | 'unmonitor') => {
    setCardBusy(movie.id)
    try {
      const accepted = action === 'search'
        ? await api.startMovieSearch(movie.id)
        : action === 'plex'
          ? await api.recheckMoviePlex(movie.id)
          : await api.bulkMovies({ movie_ids: [movie.id], action })
      if ('job_id' in accepted) setMessage(`${action === 'search' ? 'Movie search' : action === 'plex' ? 'Plex recheck' : action} queued as job ${accepted.job_id.slice(0, 8)}….`)
      else setMessage(`${action} applied to ${movie.title}.`)
      await loadMovies()
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : `Could not ${action} ${movie.title}.`)
    } finally { setCardBusy(null) }
  }

  const needsReview = (movie: Movie) => movie.status === 'Conflict' || movie.status === 'Duplicate' || (movie.problemCount ?? 0) > 0
  const filtered = useMemo(() => movies.filter((movie) => {
    const matchesFilter = filter === 'All' || (filter === 'Problems' ? needsReview(movie) : filter === 'Unmonitored' ? movie.monitored === false : movie.status === filter)
    return matchesFilter && movie.title.toLowerCase().includes(query.toLowerCase())
  }), [movies, filter, query])
  const missing = movies.filter((movie) => movie.status === 'Missing').length
  const review = movies.filter(needsReview).length
  return <Page title="Movies" subtitle="Your library, exactly where it was downloaded.">
    <div className="stats-row stats-row-three media-stats-row"><Stat label="Movies" value={String(movies.length)} tone="blue" /><Stat label="Missing" value={String(missing)} tone="amber" /><Stat label="Problems" value={String(review)} tone="red" /></div>
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search movies…" /></div><Select aria-label="Sort movies" value={sort} onChange={(event) => setSort(event.target.value)}><option value="title">Title A–Z</option><option value="-title">Title Z–A</option><option value="year">Year oldest</option><option value="-year">Year newest</option><option value="quality">Quality low–high</option><option value="-quality">Quality high–low</option><option value="last_observed">Last observed oldest</option><option value="-last_observed">Last observed newest</option><option value="problems">Problems fewest</option><option value="-problems">Problems most</option></Select><FilterChips value={filter} options={['All', 'Missing', 'Problems', 'Unmonitored', 'Present']} onChange={setFilter} className="toolbar-filter-row" />{selected.size > 0 && <span className="selection-count">{selected.size} selected · right-click for actions</span>}<div className="toolbar-spacer" /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => changeMovieView('cards')} aria-label="Card view"><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => changeMovieView('table')} aria-label="Table view"><Icon name="list" size={16} /></button></div><Button variant="ghost" icon="refresh" onClick={() => void loadMovies()}>Refresh</Button></div>
    <div className="media-filter-row media-tag-filter">{tags.map((tag) => <button type="button" className={`filter-chip ${tagFilter === tag.name ? 'active' : ''}`} key={tag.id} onClick={() => updateTagFilter(tagFilter === tag.name ? '' : tag.name)}>{tag.name}</button>)}</div>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {error && <EmptyState title="Could not load the movie library" detail={error} />}
    {!error && !loading && (view === 'cards' ? <div className="media-grid">{filtered.map((movie) => <MovieCard key={movie.id} movie={movie} selected={selected.has(movie.id)} onToggle={toggleSelected} onContext={openContext} busy={cardBusy === movie.id} onAction={runMovieCardAction} />)}</div> : <MovieTable items={filtered} selected={selected} onToggle={toggleSelected} onContext={openContext} onTagClick={updateTagFilter} />)}
    {!error && !loading && !filtered.length && <EmptyState title="No movies discovered yet" detail={tagFilter ? `No movies match the tag “${tagFilter}”.` : 'Add a Movie media folder, then scan it once to find your files.'} />}
    {context && <><div className="context-dismiss-layer" onClick={() => setContext(null)} onContextMenu={(event) => { event.preventDefault(); setContext(null) }} /><MovieBulkContextMenu position={context} count={selected.size} profiles={profiles} tags={tags} busy={bulkBusy} onRun={runBulk} onCreateTag={createTag} onClear={() => { setSelected(new Set()); setContext(null) }} /></>}
  </Page>
}

function MovieTagChips({ movie, onTagClick }: { movie: Movie; onTagClick?: (tag: string) => void }) {
  if (!movie.tags?.length) return null
  return <div className="tag-chip-row">{movie.tags.map((tag) => <span key={tag.id} className="tag-chip" role={onTagClick ? 'button' : undefined} tabIndex={onTagClick ? 0 : undefined} onClick={(event) => { if (!onTagClick) return; event.preventDefault(); event.stopPropagation(); onTagClick(tag.name) }} onKeyDown={(event) => { if (onTagClick && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onTagClick(tag.name) } }}>{tag.name}</span>)}</div>
}

function MovieCard({ movie, selected, onToggle, onContext, busy, onAction }: { movie: Movie; selected: boolean; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void; busy: boolean; onAction: (movie: Movie, action: 'search' | 'plex' | 'monitor' | 'unmonitor') => Promise<void> }) {
  const plexAvailable = usePlexAvailable()
  const mark = qualityMark(movie.quality, movie.edition)
  const run = (action: 'search' | 'plex' | 'monitor' | 'unmonitor') => (event: React.MouseEvent) => { event.preventDefault(); event.stopPropagation(); void onAction(movie, action) }
  return <article className={`media-card ${selected ? 'media-selected' : ''} ${needsAttention(movie.status) ? 'media-attention' : ''}`} onContextMenu={(event) => onContext(event, movie.id)}>
    <Link className="media-card-link" to={`/movies/${movie.id}`} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}>
      <div className={`poster poster-${movie.id} ${movie.poster ? 'poster-has-image' : 'poster-empty'}`}><PosterImage reference={movie.poster} title={movie.title} />{!movie.poster && <span className="poster-fallback-title">{movie.title}</span>}{movie.incomingReplacement && <span className="incoming-mark"><Icon name="download" size={12} />{movie.incomingKind === 'replacement' ? 'Replacing' : 'Incoming'}</span>}{selected && <span className="selection-mark"><Icon name="check" size={14} /></span>}</div>
      <div className="media-card-body"><div className="media-card-title"><strong>{movie.title}</strong><span className="media-card-sub">{movie.year || 'Year unknown'}{mark && <em className="media-card-quality">{mark}</em>}</span></div><div className="media-card-meta">{needsAttention(movie.status) && <Badge tone={statusTone(movie.status)} className="media-card-primary-badge">{movie.status}</Badge>}{plexAvailable && <Tooltip content={`Plex ${movie.plex}`}><Badge tone={statusTone(movie.plex)} className="media-card-secondary-badge">Plex</Badge></Tooltip>}{movie.monitored === false && <Badge tone="neutral" className="media-card-secondary-badge">Unmonitored</Badge>}</div></div>
    </Link>
    <div className="media-card-actions" aria-label={`Actions for ${movie.title}`}><Tooltip asChild content="Interactive search"><button className="icon-button" aria-label={`Search for ${movie.title}`} disabled={busy} onClick={run('search')}><Icon name="search" size={14} /></button></Tooltip>{plexAvailable && <Tooltip asChild content="Recheck Plex"><button className="icon-button" aria-label={`Recheck Plex for ${movie.title}`} disabled={busy} onClick={run('plex')}><Icon name="refresh" size={14} /></button></Tooltip>}<Tooltip asChild content={movie.monitored === false ? 'Monitor' : 'Unmonitor'}><button className="icon-button" aria-label={movie.monitored === false ? `Monitor ${movie.title}` : `Unmonitor ${movie.title}`} disabled={busy} onClick={run(movie.monitored === false ? 'monitor' : 'unmonitor')}><Icon name={movie.monitored === false ? 'eye' : 'eye-off'} size={14} /></button></Tooltip></div>
  </article>
}

function MovieTable({ items, selected, onToggle, onContext, onTagClick }: { items: Movie[]; selected: Set<string>; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void; onTagClick: (tag: string) => void }) {
  const plexAvailable = usePlexAvailable()
  return <Panel className="table-panel"><table className="data-table"><thead><tr><th>Title</th><th>State</th><th>Current release</th>{plexAvailable && <th>Plex</th>}<th>Tags</th><th>Confidence</th><th>Location</th><th /></tr></thead><tbody>{items.map((movie) => <tr key={movie.id} className={selected.has(movie.id) ? 'row-selected' : ''} onContextMenu={(event) => onContext(event, movie.id)} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}><td><Link className="table-title" to={`/movies/${movie.id}`}><span className={`table-poster poster-${movie.id}`}><PosterImage reference={movie.poster} title={movie.title} /></span>{movie.title}<span className="muted">{movie.year}</span>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</Link></td><td><Badge tone={statusTone(movie.status)}>{movie.status}</Badge></td><td>{movie.quality}{movie.edition && <span className="table-sub">{movie.edition}</span>}</td>{plexAvailable && <td><Badge tone={statusTone(movie.plex)}>{movie.plex}</Badge></td>}<td><MovieTagChips movie={movie} onTagClick={onTagClick} /></td><td><span className="confidence">{movie.confidence}%</span></td><td className="path-cell">{movie.location}</td><td>{selected.has(movie.id) ? <Icon name="check" size={15} /> : <Icon name="chevron" size={15} />}</td></tr>)}</tbody></table></Panel>
}

function MovieBulkContextMenu({ position, count, profiles, tags, busy, onRun, onCreateTag, onClear }: { position: { x: number; y: number }; count: number; profiles: QualityProfile[]; tags: Tag[]; busy: boolean; onRun: (action: Parameters<typeof api.bulkMovies>[0]['action'], options?: { profileId?: string | null; tagIds?: string[] }) => Promise<void>; onCreateTag: (name: string) => Promise<Tag>; onClear: () => void }) {
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

function formatEvidenceDate(value?: string) {
  if (!value) return 'Time not recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

// Every title resolves to a profile: the one it was given, or the default it
// inherits. "No profile" is no longer reachable, so the empty option names what
// actually happens instead of implying nothing applies.
/** Trigger-only value: the included folders do not agree on one profile. */
const MIXED_PROFILE = '__mixed__'

function defaultProfileLabel(profiles: QualityProfile[]): string {
  const fallback = profiles.find((profile) => profile.isDefault)
  return fallback ? `Use default (${fallback.name})` : 'Use default'
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
function releaseSeeding(release: MovieRelease, torrents: TorrentArchiveItem[]): { present: number; total: number } | undefined {
  const linked = torrents.filter((item) => item.releaseId && item.releaseId === release.id)
  if (!linked.length) return undefined
  return { present: linked.filter((item) => item.qbitPresent).length, total: linked.length }
}

function ReleaseEvidenceRow({ release, torrents = [] }: { release: MovieRelease; torrents?: TorrentArchiveItem[] }) {
  const directory = release.directories.find((item) => item.exists) ?? release.directories[0]
  const folder = directory?.path.split(/[\\/]/).filter(Boolean).pop()
  const filename = directory?.mediaFilename
  // The file is usually the folder name with an extension on the end, so it only
  // earns its own line when it actually says something different.
  const separateFile = filename && folder && filename.replace(/\.[^.]+$/, '').toLowerCase() !== folder.toLowerCase() ? filename : undefined
  const score = release.currentCustomFormatScore
  const scoreChanged = release.originalCustomFormatScore !== undefined && score !== undefined && release.originalCustomFormatScore !== score
  // Quality alone cannot separate two REMUXes of one movie; HDR flavour, audio
  // track and Proper/Repack are what actually differ between them.
  const pills = [
    release.quality,
    release.edition,
    ...releaseHdr(release.parser),
    releaseAudio(release.parser),
    releaseVideoCodec(release.parser),
  ].filter(Boolean) as string[]
  const flags = releaseFlags(release.parser)
  const seeding = releaseSeeding(release, torrents)
  return <div className="release-evidence-row">
    <div className="release-icon"><Icon name="clapper" size={18} /></div>
    <div className="release-main">
      <strong title={directory?.path}>{folder ?? release.name}</strong>
      {separateFile && <small title={separateFile}>{separateFile}</small>}
      <div className="release-pills">
        {directory?.storageRoot && <Tooltip content={`Media folder: ${directory.storageRoot}`}><span className="release-pill release-pill-folder"><Icon name="folder" size={11} />{directory.storageRoot}</span></Tooltip>}
        {pills.map((pill) => <span className="release-pill" key={pill}>{pill}</span>)}
        {flags.map((flag) => <Tooltip key={flag} content={flag === 'Proper' ? 'Re-released to fix a problem with the original' : flag === 'Repack' ? 'Repackaged by the release group' : 'Built from more than one source'}><span className="release-pill release-pill-flag">{flag}</span></Tooltip>)}
        {(score !== undefined && score !== 0) || scoreChanged
          ? <Tooltip content={scoreChanged ? `Custom Format score: ${release.originalCustomFormatScore} when downloaded, ${score} under the current profile` : 'Custom Format score under the current profile'}><span className={`release-pill ${scoreChanged ? 'release-pill-warn' : ''}`}>Score {score ?? 0}{scoreChanged ? ` (was ${release.originalCustomFormatScore})` : ''}</span></Tooltip>
          : null}
      </div>
    </div>
    {/* Only exceptions get called out on the right. Saying "current, 100%" on
        every healthy row taught nobody what either number meant. */}
    <div className="release-tags">
      {seeding && <Tooltip content={seeding.present
        ? `${seeding.present} of ${seeding.total} torrent${seeding.total === 1 ? '' : 's'} for this copy ${seeding.present === 1 ? 'is' : 'are'} still loaded in a download client`
        : `${seeding.total} archived torrent${seeding.total === 1 ? '' : 's'} for this copy, none still in a download client`}>
        <Badge tone={seeding.present ? 'green' : 'neutral'}>{seeding.present ? 'Seeding' : 'Not seeding'}</Badge>
      </Tooltip>}
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
  return <Panel title="Torrent history" eyebrow="RECOVERY EVIDENCE">{groups.length ? groups.map((group) => {
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
  const [formats, setFormats] = useState<CustomFormat[]>([])
  const [profileId, setProfileId] = useState('')
  const [minimumOverrideId, setMinimumOverrideId] = useState('')
  const [minimumScoreOverride, setMinimumScoreOverride] = useState('')
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [newOverride, setNewOverride] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const load = async () => {
    try {
      const [value, profileRows, qualityRows, formatRows] = await Promise.all([api.movieProfileSettings(resourceId), api.qualityProfiles(), api.qualityDefinitions(), api.customFormats()])
      setSettings(value); setProfiles(profileRows); setQualities(qualityRows); setFormats(formatRows)
      setProfileId(value.qualityProfileId ?? '')
      setMinimumOverrideId(value.minimumQualityOverridden ? value.minimumQuality?.id ?? '' : '')
      setMinimumScoreOverride(value.minimumCustomFormatScoreOverridden && value.minimumCustomFormatScore !== undefined ? String(value.minimumCustomFormatScore) : '')
      setOverrides(Object.fromEntries(value.customFormatScores.filter((item) => item.overrideScore !== undefined).map((item) => [item.customFormatId, item.overrideScore ?? 0])))
      setMessage('')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load profile settings.') }
  }
  useEffect(() => { void load() }, [resourceId])
  const save = async () => {
    setBusy(true); setMessage('Saving profile assignment…')
    try {
      const value = await api.saveMovieProfileSettings(resourceId, {
        quality_profile_id: profileId || null,
        minimum_quality_definition_override_id: minimumOverrideId || null,
        minimum_custom_format_score_override: minimumScoreOverride === '' ? null : Number(minimumScoreOverride),
        custom_format_score_overrides: overrides,
        expected_revision: settings?.revision ?? 0,
      })
      setSettings(value); setMessage('Profile and overrides saved. Stored release scores were re-evaluated.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save profile settings.') }
    finally { setBusy(false) }
  }
  const effectiveRows = settings?.customFormatScores ?? []
  const formatById = new Map(formats.map((item) => [item.id, item]))
  const available = formats.filter((item) => !Object.prototype.hasOwnProperty.call(overrides, item.id))
  return <Panel title="Quality Profile & Overrides" eyebrow="SEARCH SCORING">
    <div className="field-grid"><label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">{defaultProfileLabel(profiles)}</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</Select></label><label><span className="field-label">Minimum quality override</span><Select value={minimumOverrideId} onChange={(event) => setMinimumOverrideId(event.target.value)}><option value="">Inherit profile</option>{qualities.map((quality) => <option value={quality.id} key={quality.id}>{quality.name}</option>)}</Select></label><label><span className="field-label">Minimum score override</span><Input type="number" value={minimumScoreOverride} onChange={(event) => setMinimumScoreOverride(event.target.value)} placeholder={settings?.profileMinimumCustomFormatScore === undefined ? 'Inherit: no minimum' : `Inherit: ${settings.profileMinimumCustomFormatScore}`} /></label></div>
    <div className="settings-note"><Icon name="info" size={16} /><span>Overrides replace this title's profile score for the selected Custom Format. They never alter the Custom Format definition or another title.</span></div>
    <div className="score-section"><div className="section-title"><div><div className="eyebrow">TITLE OVERRIDES</div><h3>Differences from profile</h3></div><div className="search-client-chooser"><Select value={newOverride} onChange={(event) => setNewOverride(event.target.value)}><option value="">Add override…</option>{available.map((format) => <option value={format.id} key={format.id}>{format.name}</option>)}</Select><Button variant="ghost" icon="plus" disabled={!newOverride} onClick={() => { if (newOverride) { const inherited = effectiveRows.find((item) => item.customFormatId === newOverride)?.profileScore ?? 0; setOverrides((value) => ({ ...value, [newOverride]: inherited })); setNewOverride('') } }}>Add</Button></div></div>
      {Object.entries(overrides).map(([formatId, score]) => { const inherited = effectiveRows.find((item) => item.customFormatId === formatId)?.profileScore ?? 0; return <div className="score-row" key={formatId}><span><strong>{formatById.get(formatId)?.name ?? effectiveRows.find((item) => item.customFormatId === formatId)?.customFormatName ?? 'Custom Format'}</strong><small>Profile {inherited > 0 ? '+' : ''}{inherited} → override {score > 0 ? '+' : ''}{score}</small></span><Input type="number" value={String(score)} onChange={(event) => setOverrides((value) => ({ ...value, [formatId]: Number(event.target.value) || 0 }))} /><strong className={score < 0 ? 'score-negative' : 'score-positive'}>{score > 0 ? '+' : ''}{score}</strong><button className="icon-button" onClick={() => setOverrides((value) => { const next = { ...value }; delete next[formatId]; return next })}><Icon name="close" size={14} /></button></div> })}
      {!Object.keys(overrides).length && !minimumOverrideId && minimumScoreOverride === '' && <span className="muted">No per-title overrides. This movie inherits its assigned profile exactly.</span>}
    </div>
    {settings?.minimumQuality && <div className="settings-note"><Icon name="info" size={16} /><span>Effective minimum: {settings.minimumQuality.name}{settings.minimumQualityOverridden ? ' (title override)' : ' (profile)'}</span></div>}
    {settings?.minimumCustomFormatScore !== undefined && <div className="settings-note"><Icon name="info" size={16} /><span>Effective minimum Custom Format score: {settings.minimumCustomFormatScore}{settings.minimumCustomFormatScoreOverridden ? ' (title override)' : ' (profile)'}</span></div>}
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    <div className="settings-footer"><Button variant="primary" onClick={() => void save()} disabled={busy}>{busy ? 'Saving…' : 'Save profile settings'}</Button></div>
  </Panel>
}

export function MovieDetailPage({ id }: { id: string }) {
  const plexAvailable = usePlexAvailable()
  const [movie, setMovie] = useState<Movie | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async (reportError = true) => {
    try { setMovie(await api.movie(id)); if (reportError) setError('') }
    catch (reason) { if (reportError) setError(reason instanceof Error ? reason.message : 'Movie not found.') }
  }
  useEffect(() => { void load() }, [id])
  useLiveLibraryRefresh(() => load(false))
  const recheckPlex = async () => {
    setBusy(true); setMessage('Starting Plex recheck…')
    try {
      const accepted = await api.recheckMoviePlex(id)
      setMessage(`Plex recheck queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
      const [job] = await api.waitForJobs([accepted.job_id])
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
      const refresh = await api.runReconciliation()
      const jobIds = [...new Set([...(refresh.job_ids ?? []), ...(refresh.active_job_ids ?? [])].map(String))]
      const jobs = jobIds.length ? await api.waitForJobs(jobIds) : []
      await load()
      const failures = jobs.filter((job) => job.state !== 'completed')
      if (failures.length) setMessage(`File check finished with ${failures.length} failed, cancelled, or interrupted job${failures.length === 1 ? '' : 's'}. Review Tasks for details.`)
      else if (refresh.uninitialized_root_ids?.length) setMessage(`File check complete. ${refresh.uninitialized_root_ids.length} media folder${refresh.uninitialized_root_ids.length === 1 ? ' was' : 's were'} skipped; scan ${refresh.uninitialized_root_ids.length === 1 ? 'it' : 'them'} once first.`)
      else if (!jobs.length) setMessage('Nothing needed rechecking. No files were changed.')
      else setMessage('Recheck complete. No files were changed.')
    } catch (reason) {
      // Older servers do not expose the optional rescan action; a GET still refreshes persisted evidence.
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 405)) {
        try { setMovie(await api.movie(id)); setMessage('Refreshed. This server has no recheck action.') }
        catch (fallbackReason) { setMessage(fallbackReason instanceof Error ? fallbackReason.message : 'Could not refresh state.') }
      } else setMessage(reason instanceof Error ? reason.message : 'Could not recheck the files.')
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
  return <Page title={movie.title} subtitle={`${movie.year} · ${movie.tmdbId ? `TMDB ${movie.tmdbId}` : `Internal ID ${movie.id}`}`} back="Back to Movies" action={<><Button variant="ghost" icon="refresh" onClick={refreshEvidence} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh evidence'}</Button>{plexAvailable && <Button variant="ghost" icon="refresh" onClick={recheckPlex} disabled={busy}>{busy ? 'Checking Plex…' : 'Recheck Plex'}</Button>}<Button variant="primary" icon="search">Interactive search</Button></>}>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {(movie.rootHealth === 'unavailable' || movie.reconciliation?.rootOffline) && <div className="reconciliation-banner reconciliation-banner-red"><Icon name="alert" size={17} /><div><strong>Media Folder Offline</strong><span>{movie.reconciliation?.rootAffectedCount ?? movie.rootAffectedCount ?? 0} media affected. Missing grace is held until the folder is reachable.</span></div></div>}
    <div className="detail-layout"><div><Panel className="detail-hero"><div className="detail-poster"><PosterImage reference={movie.poster} title={movie.title} /><span className="poster-title">{movie.title}</span><span className="poster-year">{movie.year}</span></div><div className="detail-intro"><div className="eyebrow">MOVIE {movie.tmdbId ? `· TMDB ${movie.tmdbId}` : ''}</div><h2>{movie.title} <span className="detail-year">({movie.year})</span></h2><div className="badge-row"><Badge tone={statusTone(movie.status)}>{movie.status}</Badge>{plexAvailable && <Badge tone={statusTone(movie.plex)}>Plex {movie.plex}</Badge>}<Badge tone="blue">{movie.confidence}% match</Badge>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</div>{movie.tags?.length ? <div className="tag-chip-row detail-tag-row">{movie.tags.map((tag) => <Link key={tag.id} className="tag-chip" to={`/movies?tag=${encodeURIComponent(tag.name)}`}>{tag.name}</Link>)}</div> : null}<p className="detail-description">{movie.overview ?? 'Your files are the source of truth. Rechecking never moves or deletes media, and old paths are kept.'}</p><div className="detail-actions"><Button variant="ghost" icon="external">Change match</Button></div></div></Panel>{movie.incoming && <IncomingReplacement incoming={movie.incoming} />}<Panel title="Current releases" eyebrow="REGISTERED MEDIA">{currentReleases.length ? currentReleases.map((release) => <ReleaseEvidenceRow key={release.id || release.name} release={release} torrents={movie.torrentHistory} />) : currentSummary}</Panel><MovieProfilePanel resourceId={id} /><Panel title="Release history" eyebrow="PRESERVED EVIDENCE">{history.length ? history.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{event.type} · {formatEvidenceDate(event.createdAt)}</span></div><Badge tone={event.type.includes('replaced') ? 'purple' : event.type.includes('duplicate') || event.type.includes('conflict') ? 'red' : 'neutral'}>{event.type.replaceAll('.', ' ')}</Badge></div>) : historyReleases.map((release) => <div className="history-row" key={release.id || release.name}><span className="history-line" /><div><strong>{release.name}</strong><span>{release.state} · first observed {formatEvidenceDate(release.firstSeenAt)}</span></div><Badge tone={releaseStateTone(release.state) as 'green' | 'amber' | 'red' | 'neutral'}>Release</Badge></div>)}{!history.length && !historyReleases.length && <div className="history-empty">No persisted release events yet. New replacement and reappearance events will appear here.</div>}</Panel><TorrentHistoryPanel items={movie.torrentHistory} /></div><aside className="detail-side"><Panel title="At a glance" eyebrow="STATUS"><DetailFact label="Library state" value={movie.status} tone={statusTone(movie.status)} />{plexAvailable && <DetailFact label="Plex verification" value={movie.plex} tone={statusTone(movie.plex)} />}<DetailFact label="Media folder" value={movie.storageRoot ?? 'Unknown folder'} /><DetailFact label="Last observed" value={formatEvidenceDate(movie.lastObservedAt)} /></Panel><Panel title="Problems" eyebrow={evidence.length ? `${evidence.length} NEED ATTENTION` : 'NEEDS ATTENTION'}>{evidence.length ? evidence.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}><div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div><div><strong>{item.title}</strong><span>{item.detail}</span></div></div>) : <div className="problem-mini"><div className="problem-icon"><Icon name="check" size={14} /></div><div><strong>No unresolved problems</strong><span>{plexAvailable ? 'Identity, qBittorrent, Plex, and path evidence agree.' : 'Identity, qBittorrent, and path evidence agree.'}</span></div></div>}</Panel></aside></div>
  </Page>
}

function DetailFact({ label, value, tone }: { label: string; value: string; tone?: string }) { return <div className="detail-fact"><span>{label}</span>{tone ? <Badge tone={tone as 'green' | 'amber' | 'red' | 'neutral'}>{value}</Badge> : <strong>{value}</strong>}</div> }

export function ShowsPage() {
  const confirm = useConfirm()
  const plexAvailable = usePlexAvailable()
  const [items, setItems] = useState<Show[]>([])
  const [view, setView] = useState<'cards' | 'table'>(() => normalizeMediaView(window.localStorage.getItem('medialogue.shows.view')))
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('All')
  const [sort, setSort] = useState('title')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [countSpecials, setCountSpecials] = useState(true)
  const [specialsBusy, setSpecialsBusy] = useState(false)
  const load = async (foreground = true) => {
    if (foreground) setLoading(true)
    try { setItems(await api.shows(query, sort)); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load shows.') }
    finally { if (foreground) setLoading(false) }
  }
  useLiveLibraryRefresh(() => load(false))
  useEffect(() => { const timer = window.setTimeout(() => { void load() }, 150); return () => window.clearTimeout(timer) }, [query, sort])
  useEffect(() => { api.specialsCounting().then((value) => setCountSpecials(value.count_specials)).catch(() => undefined) }, [])

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
      const result = await api.setSpecialsCounting(next)
      setCountSpecials(result.count_specials)
      setError('')
      await load(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not change Specials counting.') }
    finally { setSpecialsBusy(false) }
  }
  const missingEpisodes = items.reduce((sum, show) => sum + (show.episodesMissing ?? Math.max(0, show.episodesTotal - show.episodesPresent)), 0)
  const totalEpisodes = items.reduce((sum, show) => sum + show.episodesTotal, 0)
  const needsReview = (show: Show) => show.status === 'Conflict' || (show.problemCount ?? 0) > 0
  const filtered = useMemo(() => items.filter((show) => filter === 'All' || (filter === 'Missing' ? show.status === 'Missing' || (show.episodesMissing ?? 0) > 0 : filter === 'Problems' ? needsReview(show) : filter === 'Unmonitored' ? show.monitored === false : show.status === filter)), [items, filter])
  return <Page title="Shows" subtitle="Track seasons and episodes without reorganizing your files.">
    <div className="stats-row media-stats-row"><Stat label="Shows" value={String(items.length)} tone="blue" /><Stat label="Episodes" value={String(totalEpisodes)} tone="green" /><Stat label="Missing" value={String(missingEpisodes)} tone="amber" /><Stat label="Problems" value={String(items.filter(needsReview).length)} tone="red" /></div>
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search shows…" /></div><Select aria-label="Sort shows" value={sort} onChange={(event) => setSort(event.target.value)}><option value="title">Title A–Z</option><option value="-title">Title Z–A</option><option value="year">Year oldest</option><option value="-year">Year newest</option><option value="missing">Missing fewest</option><option value="-missing">Missing most</option><option value="problems">Problems fewest</option><option value="-problems">Problems most</option><option value="-last_observed">Last observed newest</option><option value="last_observed">Last observed oldest</option></Select><FilterChips value={filter} options={['All', 'Missing', 'Problems', 'Unmonitored', 'Present']} className="toolbar-filter-row" onChange={setFilter} /><Tooltip asChild content={countSpecials ? 'Specials count toward episode totals on every show' : 'Specials are excluded from episode totals. Monitoring is unaffected.'}><div className="specials-toggle"><span className="specials-toggle-label">Count specials</span><button type="button" className="toggle" aria-pressed={countSpecials} aria-label="Count Specials toward episode totals on every show" disabled={specialsBusy} onClick={() => void toggleSpecials()}><span /></button><span className="toggle-label">{specialsBusy ? 'Applying…' : countSpecials ? 'Yes' : 'No'}</span></div></Tooltip><div className="toolbar-spacer" /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => { setView('cards'); window.localStorage.setItem('medialogue.shows.view', 'cards') }} aria-label="Card view"><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => { setView('table'); window.localStorage.setItem('medialogue.shows.view', 'table') }} aria-label="Table view"><Icon name="list" size={16} /></button></div><Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button></div>
    {error && <EmptyState title="Could not load Shows" detail={error} />}
    {!error && !loading && (view === 'cards' ? <div className="media-grid show-grid">{filtered.map((show) => <ShowCard key={show.id} show={show} />)}</div> : <Panel className="table-panel"><table className="data-table"><thead><tr><th>Show</th><th>Episodes</th><th>Status</th>{plexAvailable && <th>Plex</th>}<th>Seasons</th><th /></tr></thead><tbody>{filtered.map((show) => <tr key={show.id}><td><Link className="table-title" to={`/shows/${show.id}`}><span className={`table-poster poster-${show.id}`}><PosterImage reference={show.poster} title={show.title} /></span>{show.title}<span className="muted">{show.year || ''}</span></Link></td><td>{show.episodesPresent} / {show.episodesTotal}</td><td><Badge tone={statusTone(show.status)}>{show.status}</Badge></td>{plexAvailable && <td><Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge></td>}<td>{show.seasons}</td><td><Icon name="chevron" size={15} /></td></tr>)}</tbody></table></Panel>)}
    {!error && !loading && !filtered.length && <EmptyState title={items.length ? 'No shows match this filter' : 'No Shows yet'} detail={items.length ? 'Try another library filter.' : 'Add a Show from TMDB, or scan a Show media folder to find existing files.'} />}
  </Page>
}

function ShowCard({ show }: { show: Show }) {
  const plexAvailable = usePlexAvailable()
  const percent = show.episodesTotal ? Math.round((show.episodesPresent / show.episodesTotal) * 100) : 0
  return <article className={`media-card ${needsAttention(show.status) ? 'media-attention' : ''}`}><Link className="media-card-link" to={`/shows/${show.id}`}><div className={`poster poster-${show.id} ${show.poster ? 'poster-has-image' : 'poster-empty'}`}><PosterImage reference={show.poster} title={show.title} />{!show.poster && <span className="poster-fallback-title">{show.title}</span>}<span className="poster-mark">{show.seasons === 1 ? '1 SEASON' : `${show.seasons} SEASONS`}</span></div><div className="media-card-body"><div className="media-card-title"><strong>{show.title}</strong><span>{show.year || 'Year unknown'}</span></div><div className="media-card-meta">{needsAttention(show.status) && <Badge tone={statusTone(show.status)} className="media-card-primary-badge">{show.status}</Badge>}{plexAvailable && <Tooltip content={`Plex ${show.plex}`}><Badge tone={statusTone(show.plex)} className="media-card-secondary-badge">Plex</Badge></Tooltip>}{show.monitored === false && <Badge tone="neutral" className="media-card-secondary-badge">Unmonitored</Badge>}</div><div className="episode-progress"><div><span>Episodes</span><strong>{show.episodesPresent} / {show.episodesTotal}</strong></div><Progress value={percent} tone={percent === 100 ? 'green' : 'amber'} /></div><div className="media-card-path"><Icon name="tv-screen" size={13} />{(show.problemCount ?? 0) === 1 ? '1 problem' : `${show.problemCount ?? 0} problems`}</div></div></Link></article>
}

export function ShowDetailPage({ id }: { id: string }) {
  const confirm = useConfirm()
  const plexAvailable = usePlexAvailable()
  const [show, setShow] = useState<Show | null>(null)
  const [selectedSeasonId, setSelectedSeasonId] = useState('')
  const [episodeFilter, setEpisodeFilter] = useState<'all' | 'missing' | 'problems'>('all')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [mappingEditor, setMappingEditor] = useState<{ media: EpisodeMedia; season: Season } | null>(null)
  const [mappingEpisodeIds, setMappingEpisodeIds] = useState<string[]>([])
  const load = async (reportError = true) => { try { setShow(await api.show(id)); if (reportError) setError('') } catch (reason) { if (reportError) setError(reason instanceof Error ? reason.message : 'Show not found.') } }
  useEffect(() => { void load() }, [id])
  useLiveLibraryRefresh(() => load(false))
  const refreshMetadata = async () => {
    setBusy(true); setMessage('Starting TMDB metadata refresh…')
    try {
      const accepted = await api.refreshShowMetadata(id)
      setMessage(`TMDB metadata refresh queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
      const [job] = await api.waitForJobs([accepted.job_id])
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
      const accepted = await api.recheckShowPlex(id)
      setMessage(`Plex recheck queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Tasks.`)
      const [job] = await api.waitForJobs([accepted.job_id])
      await load()
      const summary = job?.summary ?? {}
      const count = (key: string) => Number(summary[key] ?? 0)
      if (job?.state === 'completed') setMessage(`Plex recheck complete: ${count('matched_releases')} matched, ${count('not_found_releases')} not in Plex, ${count('conflict_releases')} conflicts.`)
      else if (job?.state === 'cancelled') setMessage('Plex recheck cancelled. No further Plex verification was performed.')
      else setMessage(`Plex recheck failed${job?.error ? `: ${job.error}` : '.'} Check Tasks for details.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Plex recheck failed.') }
    finally { setBusy(false) }
  }
  const setSeasonMonitored = async (season: Season, monitored: boolean) => { try { await api.updateSeason(season.id, { monitored, expected_revision: season.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update season monitoring.') } }
  const [orderings, setOrderings] = useState<EpisodeOrdering[] | null>(null)
  const [orderingBusy, setOrderingBusy] = useState(false)

  // Loaded on demand: it costs two TMDB calls, and most shows never need it.
  const loadOrderings = async () => {
    if (orderings || orderingBusy) return
    setOrderingBusy(true)
    try { setOrderings(await api.episodeOrderings(id)) }
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
      await api.updateShow(id, { tmdb_episode_group_id: option.id ?? '', expected_revision: show.revision })
      setOrderings(null)
      await load()
      setMessage(`Episode ordering switched to ${option.name}. Re-scan this show's media folder if file matches look off.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not switch episode ordering.') }
    finally { setOrderingBusy(false) }
  }

  const setSeasonCounted = async (season: Season, counted: boolean) => { try { await api.updateSeason(season.id, { counted, expected_revision: season.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update season counting.') } }
  const setEpisodeMonitored = async (episode: Episode, monitored: boolean) => { try { await api.updateEpisode(episode.id, { monitored, expected_revision: episode.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update episode monitoring.') } }
  const searchSeason = async (season: Season) => { try { const result = await api.startSeasonSearch(season.id); setMessage(`Season search started · job ${result.job_id.slice(0, 8)}…`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start season search.') } }
  const editMapping = (media: EpisodeMedia, season: Season) => { const selected = season.episodes.filter((episode) => media.mappedEpisodeNumbers.includes(episode.episodeNumber)).map((episode) => episode.id); setMappingEpisodeIds(selected); setMappingEditor({ media, season }) }
  const saveMapping = async () => { if (!mappingEditor || !mappingEpisodeIds.length) return; setBusy(true); try { await api.correctEpisodeMapping(mappingEditor.media.mediaFileId, mappingEpisodeIds); setMessage('Episode mapping corrected. The media file and path were not changed.'); setMappingEditor(null); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not correct episode mapping.') } finally { setBusy(false) } }
  const seasons = show?.seasonDetail ?? []
  useEffect(() => {
    if (!seasons.length) return
    setSelectedSeasonId((current) => current && seasons.some((season) => season.id === current) ? current : (seasons.find((season) => season.missingCount > 0) ?? seasons[0]).id)
  }, [show, seasons])
  if (error) return <Page title="Show unavailable" subtitle={error} back="Back to Shows" backTo="/shows"><EmptyState title="Could not load this Show" detail={error} /></Page>
  if (!show) return <Page title="Loading Show" subtitle="Retrieving seasons and episode inventory." back="Back to Shows" backTo="/shows"><EmptyState title="Loading…" detail="Loading this show." /></Page>
  const selectedSeason = seasons.find((season) => season.id === selectedSeasonId) ?? seasons[0]
  const visibleEpisodes = selectedSeason?.episodes.filter((episode) => episodeFilter === 'all' || episodeFilter === 'missing' && episode.status !== 'Present' || episodeFilter === 'problems' && (episode.status === 'Conflict' || episode.status === 'Unmatched' || plexAvailable && episode.plex === 'Conflict')) ?? []
  return <Page title={show.title} subtitle={`${show.year || 'Year unknown'} · ${show.tmdbId ? `TMDB ${show.tmdbId}` : show.id}${show.tvdbId ? ` · TVDB ${show.tvdbId}` : ''}`} back="Back to Shows" backTo="/shows" action={<><Button variant="ghost" icon="refresh" onClick={() => void refreshMetadata()} disabled={busy}>{busy ? 'Working…' : 'Refresh metadata'}</Button>{plexAvailable && <Button variant="ghost" icon="refresh" onClick={() => void recheckPlex()} disabled={busy}>{busy ? 'Working…' : 'Recheck Plex'}</Button>}</>}>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {mappingEditor && <Panel title="Correct episode mapping" eyebrow="LOGICAL MAPPING ONLY"><div className="settings-note"><Icon name="info" size={15} /><span>This changes only Medialogue's episode mapping. The file is not renamed, moved, copied, or modified.</span></div><div className="mapping-path">{mappingEditor.media.path}</div><div className="mapping-grid">{mappingEditor.season.episodes.map((episode) => <label className="inline-check" key={episode.id}><input type="checkbox" checked={mappingEpisodeIds.includes(episode.id)} onChange={(event) => setMappingEpisodeIds((current) => event.target.checked ? [...current, episode.id] : current.filter((item) => item !== episode.id))} />S{String(episode.seasonNumber).padStart(2, '0')}E{String(episode.episodeNumber).padStart(2, '0')} · {episode.title || 'Untitled episode'}</label>)}</div><div className="settings-footer"><Button variant="ghost" onClick={() => setMappingEditor(null)}>Cancel</Button><Button variant="primary" onClick={() => void saveMapping()} disabled={busy || !mappingEpisodeIds.length}>{busy ? 'Saving…' : 'Save mapping'}</Button></div></Panel>}
    <Panel className="detail-hero"><div className={`detail-poster poster-${show.id} ${show.poster ? 'poster-has-image' : 'poster-empty'}`}><PosterImage reference={show.poster} title={show.title} />{!show.poster && <span className="poster-fallback-title">{show.title}</span>}</div><div className="detail-intro"><div className="eyebrow">SHOW {show.tmdbId ? `· TMDB ${show.tmdbId}` : ''}</div><h2>{show.title} {show.year ? <span className="detail-year">({show.year})</span> : null}</h2><div className="badge-row"><Badge tone={statusTone(show.status)} className="media-card-primary-badge">{show.status}</Badge>{plexAvailable && <Badge tone={statusTone(show.plex)} className="media-card-secondary-badge">Plex {show.plex}</Badge>}<Badge tone={show.monitored === false ? 'neutral' : 'blue'} className="media-card-secondary-badge">{show.monitored === false ? 'Unmonitored' : 'Monitored'}</Badge></div><div className="detail-summary-grid"><div><strong>{show.episodesPresent} / {show.episodesTotal}</strong><span>episodes present</span></div><div><strong>{show.episodesMissing ?? Math.max(0, show.episodesTotal - show.episodesPresent)}</strong><span>missing</span></div><div><strong>{show.seasons}</strong><span>seasons</span></div></div><p className="detail-description">{show.overview ?? 'Episode presence is tracked independently while every file stays at its existing path.'}</p></div></Panel>
    <div className="detail-layout show-detail-layout"><div><Panel title="Episode ordering" eyebrow="TMDB STRUCTURE" action={orderings ? undefined : <Button variant="ghost" onClick={() => void loadOrderings()} disabled={orderingBusy}>{orderingBusy ? 'Loading…' : 'Show orderings'}</Button>}>
      {orderings ? <div className="ordering-list">{orderings.map((option) => <button className={`ordering-option ${option.selected ? 'selected' : ''}`} key={option.id ?? 'default'} disabled={orderingBusy} onClick={() => void chooseOrdering(option)}><span className="ordering-copy"><strong>{option.name}</strong><span>{option.season_count} season{option.season_count === 1 ? '' : 's'} · {option.episode_count} episode{option.episode_count === 1 ? '' : 's'}{option.network ? ` · ${option.network}` : ''}</span>{option.description && <small>{option.description}</small>}</span><Badge tone={option.selected ? 'green' : 'neutral'}>{option.selected ? 'In use' : option.type_label}</Badge></button>)}<p className="cf-modal-note">TMDB stores alternate orderings for many shows. Switching renumbers the existing episode identities rather than replacing them.</p></div> : <div className="ordering-summary"><span className="muted">Using {show.tmdbEpisodeGroupId ? 'a custom TMDB episode group' : "TMDB's default season structure"}.</span><small>If this show&rsquo;s seasons do not match your files, another ordering probably does.</small></div>}
    </Panel><div className="show-season-workspace"><Panel className="season-nav-panel" title="Seasons" eyebrow={`${show.episodesPresent} / ${show.episodesTotal} PRESENT`}><div className="season-nav-list">{seasons.map((season) => <button type="button" className={`season-nav-item ${selectedSeason?.id === season.id ? 'selected' : ''}`} key={season.id} onClick={() => { setSelectedSeasonId(season.id); setEpisodeFilter('all') }}><span><strong>{season.title || (season.seasonNumber === 0 ? 'Specials' : `Season ${season.seasonNumber}`)}</strong><small>{season.presentCount} / {season.episodeCount} present</small></span><Badge tone={season.missingCount ? 'amber' : 'green'}>{season.missingCount ? `${season.missingCount} missing` : 'Complete'}</Badge></button>)}</div>{!seasons.length && <div className="history-empty">No season metadata exists yet.</div>}</Panel><div className="season-main"><Panel className="season-detail-panel" title={selectedSeason ? (selectedSeason.title || (selectedSeason.seasonNumber === 0 ? 'Specials' : `Season ${selectedSeason.seasonNumber}`)) : 'Season'} eyebrow={selectedSeason ? `${selectedSeason.presentCount} / ${selectedSeason.episodeCount} PRESENT` : 'EPISODE INVENTORY'} action={selectedSeason && <div className="season-header-actions"><Button variant="ghost" onClick={() => void searchSeason(selectedSeason)}>Search season</Button><Button variant="ghost" onClick={() => void setSeasonMonitored(selectedSeason, !selectedSeason.monitored)}>{selectedSeason.monitored ? 'Unmonitor' : 'Monitor'}</Button></div>}>
      {selectedSeason && <><div className="season-control-row"><div className="season-control-copy"><span>{selectedSeason.missingCount ? `${selectedSeason.missingCount} episode${selectedSeason.missingCount === 1 ? '' : 's'} missing` : 'Everything present'}</span><span>{selectedSeason.counted ? 'Included in show totals' : 'Excluded from show totals'}</span></div><div className="season-control-actions"><label className="inline-check"><input type="checkbox" checked={selectedSeason.counted} onChange={(event) => void setSeasonCounted(selectedSeason, event.target.checked)} />Counted</label><div className="episode-filter-chips"><button className={episodeFilter === 'all' ? 'active' : ''} onClick={() => setEpisodeFilter('all')}>All</button><button className={episodeFilter === 'missing' ? 'active' : ''} onClick={() => setEpisodeFilter('missing')}>Missing</button><button className={episodeFilter === 'problems' ? 'active' : ''} onClick={() => setEpisodeFilter('problems')}>Problems</button></div></div></div><div className="episode-table-wrap"><table className="data-table episode-table"><thead><tr><th>Episode</th><th>Media</th><th>State</th>{plexAvailable && <th>Plex</th>}<th /></tr></thead><tbody>{visibleEpisodes.map((episode) => <tr key={episode.id}><td><div className="episode-title-cell"><strong>S{String(episode.seasonNumber).padStart(2, '0')}E{String(episode.episodeNumber).padStart(2, '0')} · {episode.title || 'Untitled episode'}</strong>{episode.airDate && <small>{episode.airDate}</small>}</div></td><td><div className="episode-media-cell"><strong>{episode.quality || 'No media'}</strong>{episode.media[0]?.path && <small>{episode.media[0].path}</small>}<div className="badge-row compact">{episode.media[0]?.releaseScope === 'season_pack' && <Badge tone="purple">Season pack</Badge>}{episode.media[0]?.mappedEpisodeNumbers.length > 1 && <Badge tone="blue">Multi-episode</Badge>}{episode.media[0]?.manualMapping && <Badge tone="neutral">Manual mapping</Badge>}</div></div></td><td><Badge tone={statusTone(episode.status)} className="media-card-primary-badge">{episode.status}</Badge></td>{plexAvailable && <td><Badge tone={statusTone(episode.plex)} className="media-card-secondary-badge">{episode.plex}</Badge></td>}<td><details className="episode-actions-menu"><summary className="button button-ghost"><Icon name="menu" size={14} />Actions</summary><div className="episode-actions-popover"><label className="inline-check"><input type="checkbox" checked={episode.monitored} onChange={(event) => void setEpisodeMonitored(episode, event.target.checked)} />Monitored</label>{episode.media[0] && <button type="button" onClick={() => editMapping(episode.media[0], selectedSeason)}>Map episode</button>}</div></details></td></tr>)}</tbody></table>{!visibleEpisodes.length && <div className="history-empty">No episodes match this filter.</div>}</div></>}
    </Panel><ShowProfilePanel resourceId={id} /><Panel title="Recent history" eyebrow="EVENTS">{show.recentEvents?.length ? show.recentEvents.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{event.type} · {formatEvidenceDate(event.createdAt)}</span></div></div>) : <div className="history-empty">No Show or episode events recorded yet.</div>}</Panel></div></div></div><aside className="detail-side"><Panel title="At a glance" eyebrow="STATUS"><DetailFact label="Library state" value={show.status} tone={statusTone(show.status)} />{plexAvailable && <DetailFact label="Plex verification" value={show.plex} tone={statusTone(show.plex)} />}<DetailFact label="Episodes present" value={`${show.episodesPresent} / ${show.episodesTotal}`} /><DetailFact label="Last observed" value={formatEvidenceDate(show.lastObservedAt)} /></Panel><Panel title="Problems" eyebrow={`${show.problemCount ?? 0} NEED ATTENTION`}>{show.problems?.length ? show.problems.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}><div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div><div><strong>{item.title}</strong><span>{item.detail}</span></div></div>) : <div className="problem-mini"><div className="problem-icon"><Icon name="check" size={14} /></div><div><strong>No unresolved problems</strong><span>Mapped episode files are consistent with current evidence.</span></div></div>}</Panel></aside></div>
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
  const load = async () => { try { const [value, profileRows, qualityRows] = await Promise.all([api.showProfileSettings(resourceId), api.qualityProfiles(), api.qualityDefinitions()]); setSettings(value); setProfiles(profileRows); setQualities(qualityRows); setProfileId(value.qualityProfileId ?? ''); setMinimumOverrideId(value.minimumQualityOverridden ? value.minimumQuality?.id ?? '' : ''); setMinimumScoreOverride(value.minimumCustomFormatScoreOverridden && value.minimumCustomFormatScore !== undefined ? String(value.minimumCustomFormatScore) : '') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load Show profile.') } }
  useEffect(() => { void load() }, [resourceId])
  const save = async () => { try { const value = await api.saveShowProfileSettings(resourceId, { quality_profile_id: profileId || null, minimum_quality_definition_override_id: minimumOverrideId || null, minimum_custom_format_score_override: minimumScoreOverride === '' ? null : Number(minimumScoreOverride), custom_format_score_overrides: Object.fromEntries((settings?.customFormatScores ?? []).filter((item) => item.overrideScore !== undefined).map((item) => [item.customFormatId, item.overrideScore ?? 0])), expected_revision: settings?.revision ?? 0 }); setSettings(value); setMessage('Show Quality Profile saved.') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save Show profile.') } }
  return <Panel title="Quality Profile" eyebrow="SHOW SEARCH SCORING"><div className="field-grid"><label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">{defaultProfileLabel(profiles)}</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</Select></label><label><span className="field-label">Minimum quality override</span><Select value={minimumOverrideId} onChange={(event) => setMinimumOverrideId(event.target.value)}><option value="">Inherit profile</option>{qualities.map((quality) => <option value={quality.id} key={quality.id}>{quality.name}</option>)}</Select></label><label><span className="field-label">Minimum score override</span><Input type="number" value={minimumScoreOverride} onChange={(event) => setMinimumScoreOverride(event.target.value)} placeholder={settings?.profileMinimumCustomFormatScore === undefined ? 'Inherit: no minimum' : `Inherit: ${settings.profileMinimumCustomFormatScore}`} /></label></div>{settings?.minimumCustomFormatScore !== undefined && <div className="settings-note"><Icon name="info" size={16} /><span>Effective minimum Custom Format score: {settings.minimumCustomFormatScore}{settings.minimumCustomFormatScoreOverridden ? ' (show override)' : ' (profile)'}</span></div>}{message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}<div className="settings-footer"><Button variant="primary" onClick={() => void save()}>Save profile settings</Button></div></Panel>
}

export function DownloadsPage() {
  const [items, setItems] = useState<Download[]>([])
  const [filter, setFilter] = useState<Download['state'] | 'All'>('Downloading')
  const [sort, setSort] = useState<{ key: keyof Download; direction: 'asc' | 'desc' }>({ key: 'name', direction: 'asc' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = async () => {
    setLoading(true)
    try { setItems(await api.downloads()); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load downloads.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  const filtered = useMemo(() => {
    const visible = filter === 'All' ? items : items.filter((item) => item.state === filter)
    return [...visible].sort((left, right) => {
      const a = String(left[sort.key]); const b = String(right[sort.key]); const comparison = a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
      return sort.direction === 'asc' ? comparison : -comparison
    })
  }, [items, filter, sort])
  const setSortKey = (key: keyof Download) => setSort((current) => current.key === key ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' } : { key, direction: 'asc' })
  const sortLabel = (key: keyof Download, label: string) => <button className="table-sort" onClick={() => setSortKey(key)}>{label}{sort.key === key && <span>{sort.direction === 'asc' ? ' ↑' : ' ↓'}</span>}</button>
  const downloading = items.filter((item) => item.state === 'Downloading')
  const checking = items.filter((item) => item.state === 'Checking')
  const seeding = items.filter((item) => item.state === 'Seeding')
  const completed = items.filter((item) => item.state === 'Completed')
  const clientCount = new Set(items.map((item) => item.client)).size
  const disagreements = items.filter((item) => item.reconciliationState?.toLowerCase().includes('disagree') || item.mediaState?.toLowerCase().includes('missing') || item.mediaState?.toLowerCase().includes('conflict')).length
  return <Page title="Downloads" subtitle="Observe qBittorrent activity without importing or moving media." action={<Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button>}>
    <div className="stats-row"><Stat label="Downloading" value={String(downloading.length)} detail="Live progress" tone="blue" /><Stat label="Checking" value={String(checking.length)} detail="Verifying data" tone="blue" /><Stat label="Seeding" value={String(seeding.length)} detail="Active torrents" tone="green" /><Stat label="Completed" value={String(completed.length)} detail="Observed torrents" /><Stat label="Tracked clients" value={String(clientCount)} detail={clientCount ? 'Reporting activity' : 'No activity'} />{disagreements > 0 && <Stat label="Evidence mismatch" value={String(disagreements)} detail="qBit / media review" tone="red" />}</div>
    <div className="toolbar"><div className="filter-tabs">{(['Downloading', 'Checking', 'Seeding', 'Completed', 'Paused', 'Error', 'All'] as const).map((item) => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item}</button>)}</div><div className="toolbar-spacer" /><Link className="button button-ghost" to="/settings"><Icon name="settings" size={16} />Client settings</Link></div>
    {error && <EmptyState icon="download" title="Could not load downloads" detail={error} action={<Button variant="ghost" icon="refresh" onClick={() => void load()}>Try again</Button>} />}
    {!error && !loading && !filtered.length && <EmptyState icon="download" title={items.length ? 'No downloads match this filter' : 'No downloads observed'} detail={items.length ? 'Try another state filter or refresh qBittorrent.' : 'Configure an enabled qBittorrent client to begin read-only polling.'} />}
    {!error && (loading || filtered.length > 0) && <Panel className="table-panel"><table className="data-table downloads-table"><thead><tr><th>{sortLabel('name', 'Release')}</th><th>{sortLabel('client', 'Client')}</th><th>{sortLabel('kind', 'Scope')}</th><th>{sortLabel('progress', 'Progress')}</th><th>{sortLabel('size', 'Size')}</th><th>{sortLabel('eta', 'ETA')}</th><th>{sortLabel('path', 'Save path')}</th><th>Media evidence</th><th /></tr></thead><tbody>{loading ? <tr><td colSpan={9} className="table-loading">Reading qBittorrent observations…</td></tr> : filtered.map((download) => <tr key={download.id}><td><div className="release-cell"><span className={`state-icon state-${download.state.toLowerCase()}`}><Icon name={download.state === 'Downloading' ? 'download' : download.state === 'Checking' ? 'refresh' : download.state === 'Seeding' ? 'activity' : download.state === 'Error' ? 'alert' : 'check'} size={14} /></span><strong>{download.name}</strong>{(download.quality || download.edition) && <small>{[download.quality, download.edition].filter(Boolean).join(' · ')}</small>}</div></td><td><span className="client-name"><span className="client-dot" />{download.client}</span></td><td><Badge tone="neutral">{download.kind}</Badge></td><td><div className="download-progress"><Progress value={download.progress} tone={download.state === 'Seeding' || download.state === 'Completed' ? 'green' : download.state === 'Error' ? 'amber' : 'blue'} /><span>{Math.round(download.progress)}%</span></div></td><td>{download.size}</td><td className="muted">{download.eta}</td><td className="path-cell">{download.path}</td><td>{download.reconciliationState || download.mediaState ? <Badge tone={download.reconciliationState?.toLowerCase().includes('disagree') || download.mediaState?.toLowerCase().includes('missing') || download.mediaState?.toLowerCase().includes('conflict') ? 'red' : 'neutral'}>{download.reconciliationState || download.mediaState}</Badge> : <span className="muted">Observed only</span>}</td><td>{download.movieId && <Link className="icon-button" to={`/movies/${download.movieId}`} aria-label={`Open ${download.name}`}><Icon name="chevron" size={15} /></Link>}</td></tr>)}</tbody></table></Panel>}
  </Page>
}

function taskInterval(seconds: number) {
  if (seconds < 60) return `${seconds} seconds`
  if (seconds % 3600 === 0) { const hours = seconds / 3600; return `${hours} hour${hours === 1 ? '' : 's'}` }
  const minutes = Math.round(seconds / 60)
  return `${minutes} minute${minutes === 1 ? '' : 's'}`
}

function taskTime(value?: string, future = false) {
  if (!value) return '—'
  if (!future) return formatAge(value)
  const remaining = Date.parse(value) - Date.now()
  if (!Number.isFinite(remaining) || remaining <= 0) return 'now'
  const seconds = Math.ceil(remaining / 1000)
  if (seconds < 60) return `in ${seconds} seconds`
  const minutes = Math.ceil(seconds / 60)
  return `in ${minutes} minute${minutes === 1 ? '' : 's'}`
}

function taskDuration(milliseconds?: number) {
  if (milliseconds === undefined) return '—'
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function jobDuration(job: Job) {
  if (!job.startedAt) return '—'
  const started = Date.parse(job.startedAt)
  const finished = job.finishedAt ? Date.parse(job.finishedAt) : Date.now()
  return Number.isFinite(started) && Number.isFinite(finished) ? taskDuration(finished - started) : '—'
}

export function TasksPage() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [runningId, setRunningId] = useState('')
  const [cancellingId, setCancellingId] = useState('')
  const [error, setError] = useState('')

  const load = async (foreground = false) => {
    if (foreground) setLoading(true)
    try {
      const [scheduled, recent] = await Promise.all([api.scheduledTasks(), api.jobs()])
      setTasks(scheduled)
      setJobs(recent.slice(0, 30))
      setError('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load scheduled tasks.') }
    finally { if (foreground) setLoading(false) }
  }

  useEffect(() => {
    void load(true)
    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const changed = () => void load()
    stream.addEventListener('job.status', changed)
    const timer = window.setInterval(changed, 10_000)
    return () => { stream.close(); window.clearInterval(timer) }
  }, [])

  const run = async (task: ScheduledTask) => {
    setRunningId(task.id); setError('')
    try { await api.runScheduledTask(task.id); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : `Could not run ${task.name}.`) }
    finally { setRunningId('') }
  }

  const cancel = async (job: Job) => {
    setCancellingId(job.id); setError('')
    try { await api.cancelJob(job.id); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : `Could not cancel ${job.title}.`) }
    finally { setCancellingId('') }
  }

  return <Page title="Tasks" subtitle="Scheduled tasks, active background work, and recent executions in one place." action={<Button variant="ghost" icon="refresh" onClick={() => void load(true)} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button>}>
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    <Panel className="table-panel tasks-panel" title="Scheduled" eyebrow={`${tasks.length} TASK${tasks.length === 1 ? '' : 'S'}`}>
      <div className="tasks-table-wrap"><table className="data-table tasks-table"><thead><tr><th>Name</th><th>Interval</th><th>Last execution</th><th>Last duration</th><th>Next execution</th><th /></tr></thead><tbody>{tasks.map((task) => {
          const active = Boolean(task.activeJobId)
          return <tr key={task.id}><td><strong>{task.name}</strong>{task.lastStatus && <span className="table-sub">{task.lastStatus.replaceAll('_', ' ')}</span>}</td><td>{task.enabled ? taskInterval(task.intervalSeconds) : 'Disabled'}</td><td>{taskTime(task.lastFinishedAt)}</td><td className="task-duration">{taskDuration(task.lastDurationMs)}</td><td>{active ? <Badge tone="blue">{task.activeJobStatus ?? 'queued'}</Badge> : task.enabled ? taskTime(task.nextRunAt, true) : '—'}</td><td><Tooltip asChild content={active ? 'Task is already running' : `Run ${task.name} now`}><button className="icon-button" aria-label={`Run ${task.name} now`} disabled={active || runningId === task.id || !task.enabled} onClick={() => void run(task)}><Icon name={active ? 'activity' : 'refresh'} size={15} /></button></Tooltip></td></tr>
        })}</tbody></table></div>
      {!loading && !tasks.length && <EmptyState icon="activity" title="No scheduled tasks" detail="No recurring backend work is registered." />}
    </Panel>
    <Panel className="table-panel tasks-panel" title="Queue" eyebrow={`${jobs.length} RECENT EXECUTION${jobs.length === 1 ? '' : 'S'}`}>
      <div className="tasks-table-wrap"><table className="data-table tasks-table tasks-queue-table"><thead><tr><th>Name</th><th>Status</th><th>Started</th><th>Ended</th><th>Duration</th><th>Progress</th><th>Detail</th><th /></tr></thead><tbody>{jobs.map((job) => {
          const canCancel = job.cancellable && (job.state === 'running' || job.state === 'queued')
          return <tr key={job.id}><td><strong>{job.title}</strong></td><td><Badge tone={job.state === 'completed' ? 'green' : job.state === 'failed' || job.state === 'interrupted' ? 'red' : job.state === 'cancelled' ? 'neutral' : 'blue'}>{job.state}</Badge></td><td>{taskTime(job.startedAt)}</td><td>{taskTime(job.finishedAt)}</td><td className="task-duration">{jobDuration(job)}</td><td>{job.progress !== undefined ? <div className="task-progress"><Progress value={job.progress} tone={job.state === 'completed' ? 'green' : 'blue'} /><span>{job.progress}%</span></div> : '—'}</td><td className="path-cell">{job.error || job.detail || '—'}</td><td>{canCancel && <Button variant="ghost" onClick={() => void cancel(job)} disabled={cancellingId === job.id}>{cancellingId === job.id ? 'Cancelling…' : 'Cancel'}</Button>}</td></tr>
        })}</tbody></table></div>
      {!loading && !jobs.length && <EmptyState icon="activity" title="No background work yet" detail="Scheduled and manually started executions will appear here." />}
    </Panel>
  </Page>
}

export function EventHistoryPage() {
  const confirm = useConfirm()
  const [events, setEvents] = useState<EventHistoryItem[]>([])
  const [indexerColors, setIndexerColors] = useState<Record<string, string>>({})
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(0)
  const [page, setPage] = useUrlNumber('page', 1)
  const [eventType, setEventType] = useUrlState('type')
  const [severity, setSeverity] = useUrlState('severity')
  const [entityType, setEntityType] = useUrlState('entity')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    api.indexers()
      .then((items) => {
        if (!alive) return
        setIndexerColors(Object.fromEntries(items.map((item) => [item.name, item.color ?? ''])))
      })
      .catch(() => undefined)
    return () => { alive = false }
  }, [])
  const [error, setError] = useState('')
  const [pageSize, setPageSize] = useState(100)
  const [sort, setSort] = useState('-created_at')

  const load = async (targetPage = page) => {
    setLoading(true)
    try {
      const payload = await api.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, sort, page: targetPage, pageSize })
      setEvents(payload.items)
      setTotal(payload.total)
      setPages(payload.pages)
      setPage(payload.page)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load events.')
    } finally { setLoading(false) }
  }

  useEffect(() => { setPage(1) }, [eventType, severity, entityType, pageSize, sort])

  useEffect(() => {
    let alive = true
    const refresh = () => api.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, sort, page, pageSize }).then((payload) => {
      if (alive) { setEvents(payload.items); setTotal(payload.total); setPages(payload.pages); setError('') }
    }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load events.') }).finally(() => { if (alive) setLoading(false) })
    void refresh()
    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const onDurableEvent = () => void refresh()
    ;[
      'download.completed', 'scan.completed', 'scan.failed', 'plex.health', 'plex.verified', 'plex.conflict', 'plex.sync_completed',
      'problem.created', 'problem.resolved', 'storage_root.unavailable', 'storage_root.restored', 'release.replaced',
      'media.missing', 'media.present', 'media.reappeared', 'torrent.detected', 'torrent.removed', 'torrent.reappeared',
      'search.download_submitted', 'show.metadata_refreshed', 'qbittorrent.health',
      'tag.created', 'tag.updated', 'tag.deleted', 'movie.tags_updated', 'movie.monitoring_updated',
      'quality_profile.assignment_updated', 'parser.reevaluated', 'custom_formats.reevaluated', 'bulk.operation_completed',
    ].forEach((name) => stream.addEventListener(name, onDurableEvent))
    const timer = window.setInterval(refresh, 30000)
    return () => { alive = false; stream.close(); window.clearInterval(timer) }
  }, [eventType, severity, entityType, page, pageSize, sort])

  const removeEvent = async (item: EventHistoryItem) => {
    if (!await confirm({
      title: 'Delete this event?',
      body: item.message,
      confirmLabel: 'Delete event',
      note: 'This only removes the row from Events.',
    })) return
    try { await api.deleteEvent(item.id); await load(page) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete event.') }
  }

  const clearHistory = async () => {
    const label = eventType || severity || entityType ? 'all events matching the current filters' : 'ALL events'
    if (!await confirm({
      title: `Delete ${label}?`,
      confirmLabel: 'Delete',
      note: 'Current media, torrents, problems and integrations are unaffected.',
    })) return
    try {
      const result = await api.clearEvents({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined })
      setPage(1)
      await load(1)
      setError(result.deleted ? '' : 'No matching history events were present.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not clear events.') }
  }

  return <Page title="Events" subtitle="Every change worth keeping. Live progress is not recorded here." action={<div className="page-actions"><Button variant="ghost" icon="refresh" onClick={() => void load()}>Refresh</Button><Button variant="danger" onClick={() => void clearHistory()}>Clear events</Button></div>}>
    <div className="toolbar event-toolbar">
      <Select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option><option value="info">Info</option><option value="warning">Warning</option><option value="error">Error</option></Select>
      <Select value={entityType} onChange={(event) => setEntityType(event.target.value)}><option value="">All entities</option><option value="movie">Movies</option><option value="show">Shows</option><option value="episode">Episodes</option><option value="movie_release">Movie releases</option><option value="show_release">Show releases</option><option value="torrent">Torrents</option><option value="storage_root">Media folders</option><option value="download_client">Download clients</option><option value="tag">Tags</option><option value="bulk_operation">Bulk operations</option></Select>
      <Input value={eventType} onChange={(event) => setEventType(event.target.value)} placeholder="Exact event type, e.g. release.replaced" />
      <Select aria-label="Events per page" value={String(pageSize)} onChange={(event) => setPageSize(Number(event.target.value))}>{[50, 100, 150, 200, 250, 500, 1000].map((size) => <option key={size} value={size}>{size} per page</option>)}</Select>
      <span className="muted event-count">{total} events · page {pages ? page : 0} of {pages}</span>
    </div>
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {!loading && <Panel className="event-history-panel">

      {events.length > 0 && <div className="event-columns">
        <span />
        <EventSortHeader label="When" field="created_at" sort={sort} onSort={setSort} />
        <EventSortHeader label="Event" field="event_type" sort={sort} onSort={setSort} />
        <span>Details</span>
        <span />
      </div>}
      {events.map((item) => <article className={`event-row event-row-${item.severity}`} key={item.id}>

        <details className="event-details">
          <summary className="event-row-main">

            <span className="event-severity">
              <Tooltip content={item.severity === 'error' ? 'Error' : item.severity === 'warning' ? 'Warning' : 'Info'}>
                <span><Icon name={item.severity === 'error' || item.severity === 'warning' ? 'alert' : 'info'} size={14} /></span>
              </Tooltip>
            </span>
            <span className="event-time">{eventStamp(item.createdAt)}</span>
            <span className="event-kind">{eventLabel(item.eventType)}</span>
            <div className="event-body">
              <p className="event-message">{item.message}</p>
              {eventIndexerName(item.details) ? <span className="release-pill release-pill-indexer event-indexer" style={indexerBadgePreview(indexerColors[eventIndexerName(item.details)!] ?? '')}><i>Indexer</i>{eventIndexerName(item.details)}</span> : null}
            </div>
            <span className="event-row-actions">
              <span className="event-delete" role="button" tabIndex={0} aria-label="Delete this event" onClick={(clicked) => { clicked.preventDefault(); void removeEvent(item) }}><Icon name="close" size={14} /></span>
            </span>
          </summary>
          <pre>{JSON.stringify(item.details, null, 2)}</pre>
        </details>
      </article>)}
      {!events.length && <div className="history-empty">No events match the selected filters.</div>}
    </Panel>}
    {!loading && pages > 1 && <div className="pagination-bar"><Button variant="ghost" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</Button><span>Page {page} of {pages} · {total} events</span><Button variant="ghost" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>Next</Button></div>}
    {loading && <EmptyState icon="clock" title="Loading events" detail="Loading events." />}
  </Page>
}

function EventSortHeader({ label, field, sort, onSort }: { label: string; field: string; sort: string; onSort: (sort: string) => void }) {
  const active = sort === field || sort === `-${field}`
  const descending = sort === `-${field}`
  return <button
    type="button"
    className={`event-sort ${active ? 'active' : ''}`}
    aria-sort={active ? (descending ? 'descending' : 'ascending') : 'none'}
    onClick={() => onSort(active && descending ? field : `-${field}`)}
  >{label}<Icon name="caret" size={12} className={descending ? '' : 'flipped'} /></button>
}

function eventIndexerName(details: Record<string, unknown>): string | undefined {
  const value = details.indexer_name ?? details.indexerName
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function problemRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function problemText(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return undefined
}

function problemIdentity(title: unknown, year: unknown): string | undefined {
  const name = problemText(title)
  if (!name) return undefined
  const releaseYear = typeof year === 'number' && Number.isFinite(year) ? year : problemText(year)
  return releaseYear ? `${name} (${releaseYear})` : name
}

function parserIdentity(details: Record<string, unknown>): { title?: string; year?: string; identity?: string; warnings: string[] } {
  const parse = problemRecord(details.parse)
  const identity = problemRecord(parse.identity)
  const title = problemText(details.parsed_title) ?? problemText(identity.title_candidate)
  const year = problemText(details.parsed_year) ?? problemText(identity.year)
  const warningsValue = Array.isArray(details.parser_warnings) ? details.parser_warnings : Array.isArray(parse.warnings) ? parse.warnings : []
  const warnings = warningsValue.map((value) => problemText(value)).filter((value): value is string => Boolean(value))
  return { title, year, identity: problemText(details.parsed_identity) ?? problemIdentity(title, year), warnings }
}

function readableEvidenceValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.map(readableEvidenceValue).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

type TMDBCandidate = TMDBMovieLookup | TMDBShowLookup

function problemTMDBCandidateLookup(candidate: Record<string, unknown>): TMDBCandidate | undefined {
  const tmdbId = Number(candidate.tmdb_id)
  const title = problemText(candidate.title)
  if (!Number.isInteger(tmdbId) || !title) return undefined
  const yearValue = Number(candidate.year)
  return {
    tmdbId,
    title,
    originalTitle: problemText(candidate.original_title),
    year: Number.isInteger(yearValue) ? yearValue : undefined,
    overview: problemText(candidate.overview),
    posterRef: problemText(candidate.poster_path ?? candidate.poster_ref),
  }
}

function ProblemEvidenceDetails({ problem }: { problem: Problem }) {
  const details = problem.details ?? {}
  if (problem.code === 'DUPLICATE_EPISODE_RELEASE') {
    const files = Array.isArray(details.media_files) ? details.media_files.map(problemRecord) : []
    // The files themselves are listed once, above. Only the caveat is left, and
    // only when a row it describes is actually on screen.
    if (!files.length || files.every((file) => file.physical_exists === true)) return null
    return <div className="problem-evidence-block">
      <div className="problem-evidence-note">Only distinct paths verified on disk count as physical duplicates. Catalogue rows in a missing-file grace period are shown for diagnosis but cannot be selected.</div>
    </div>
  }

  if (problem.code === 'TORRENT_PATH_NOT_FOUND') {
    const observations = Array.isArray(details.qbit_observations) ? details.qbit_observations.map(problemRecord) : []
    const mapped = Array.isArray(details.mapped_files)
      ? details.mapped_files.map(problemRecord)
      : Array.isArray(details.mapped_directories) ? details.mapped_directories.map(problemRecord) : []
    if (observations.length || mapped.length) return <div className="problem-evidence-block">
      <div className="problem-evidence-grid">
        <div className="problem-evidence-side"><span className="eyebrow">QBITTORRENT REPORTS</span><strong>{problemText(details.torrent_name) ?? problem.subject}</strong>{observations.length ? observations.map((item, index) => <div className="problem-candidate" key={`${problem.id}-qbit-${index}`}><code>{problemText(item.reported_path) ?? 'No reported path'}</code>{problemText(item.resolved_path) && problemText(item.resolved_path) !== problemText(item.reported_path) && <code>Local: {problemText(item.resolved_path)}</code>}<span>{item.is_present === true ? 'Present in qBittorrent' : 'Absent from qBittorrent'}{problemText(item.state) ? ` · ${problemText(item.state)}` : ''}</span></div>) : <span>No current client observation was recorded.</span>}</div>
        <div className="problem-evidence-side"><span className="eyebrow">MEDIALOGUE MAPPED MEDIA</span><strong>{mapped.length} mapped {mapped.length === 1 ? 'path' : 'paths'}</strong>{mapped.length ? mapped.map((item, index) => <div className="problem-candidate" key={`${problem.id}-mapped-${problemText(item.media_file_id) ?? index}`}><code>{problemText(item.path) ?? 'Path not recorded'}</code><span>{item.physical_exists === true ? 'Verified on disk' : 'Not found on disk'} · catalogue: {item.catalogue_exists === true ? 'present' : 'missing'}{problemText(item.missing_since) ? ` · missing since ${problemText(item.missing_since)}` : ''}</span></div>) : <span>No mapped paths are attached to this release.</span>}</div>
      </div>
    </div>
    // Not "recheck to collect evidence": a recheck reads the download clients,
    // so when none of them still holds this torrent there is nothing to collect
    // and the old note asked for something that could never succeed.
    return <div className="problem-evidence-block"><div className="problem-evidence-note">No download client currently reports this torrent, so there is no live path evidence to compare against.</div></div>
  }

  if (problem.code === 'PLEX_IDENTITY_MISMATCH') {
    const localPath = problemText(details.local_path ?? details.medialogue_path)
    const plexPath = problemText(details.plex_path ?? details.plex_reported_path)
    const differences = Array.isArray(details.differences) ? details.differences.map((value) => problemText(value)).filter((value): value is string => Boolean(value)) : []
    const conflicts = Array.isArray(details.conflicts) ? details.conflicts.map(problemRecord) : []
    if (problem.entityType === 'show' && conflicts.length > 0) return <div className="problem-evidence-block">
      <div className="problem-evidence-note">Plex show titles are advisory. This Problem exists only because Plex mapped the same physical file to different season/episode numbers.</div>
      <div className="problem-candidate-list">{conflicts.map((conflict, index) => <div className="problem-candidate" key={`${problem.id}-plex-${index}`}><strong>{problemText(conflict.local_episode) ?? 'Medialogue episode'} → {problemText(conflict.plex_episode) ?? 'Plex episode'}</strong><span>{problemText(conflict.local_show_title) ?? 'Show'} · Medialogue: {problemText(conflict.local_path) ?? 'unknown path'}</span><span>Plex metadata: {problemText(conflict.plex_show_title) ?? 'unknown show title'}{problemText(conflict.plex_episode_title) ? ` · ${problemText(conflict.plex_episode_title)}` : ''} · {problemText(conflict.plex_path) ?? 'unknown path'}</span><span>Different: {Array.isArray(conflict.differences) ? conflict.differences.map(readableEvidenceValue).join(', ') : 'episode numbering'}</span></div>)}</div>
    </div>
    // Identity, paths and differences are all covered by the comparison and the
    // key details above, so there is nothing left for this block to add.
    return null
  }

  // A dump of raw detail keys is not evidence: the values worth reading are
  // already pulled out by ProblemKeyDetails, and the rest is machine state.
  return null
}

function problemLabel(problem: Problem): string {
  const raw = problem.reason ?? ''
  return problem.label || raw.replace(/_/g, ' ').toLowerCase().replace(/^./, (character) => character.toUpperCase()) || 'Problem'
}

// The row already carries the reason as a pill. subject falls back to that same
// label whenever the entity behind a Problem cannot be resolved to a title, so
// rendering both prints the identical words twice on consecutive lines.
function problemSubjectLine(problem: Problem): string {
  const subject = (problem.subject ?? '').trim()
  return subject.toLowerCase() === problemLabel(problem).toLowerCase() ? '' : subject
}

function problemCandidateFromEvidence(candidate: Record<string, unknown>): TMDBCandidate | undefined {
  return problemTMDBCandidateLookup(candidate)
}

function duplicateMovieReleases(problem: Problem, movie: Movie | null): MovieRelease[] {
  const releases = movie?.releasesDetail ?? []
  const ids = new Set(Array.isArray(problem.details?.release_ids) ? problem.details.release_ids.map(String) : [])
  if (ids.size) return releases.filter((release) => ids.has(release.id))
  return releases.filter((release) => ['duplicate', 'current'].includes(release.state.toLowerCase()))
}

function plexIdentitySides(problem: Problem) {
  const details = problem.details ?? {}
  const localPath = problemText(details.local_path ?? details.medialogue_path ?? details.path ?? details.resolved_path)
  const folderName = localPath ? localPath.split(/[\\/]/).filter(Boolean).pop() : undefined
  // Not problem.subject unconditionally: when the entity cannot be resolved the
  // mapper falls back to the reason label, which rendered as "Identity conflict
  // (1973)" — the name of the problem, presented as the name of the title.
  const resolvedSubject = problem.subject && problem.subject !== problem.label && problem.subject !== localPath
    ? problem.subject
    : undefined
  const localTitle = problemText(details.local_title ?? details.medialogue_title) ?? resolvedSubject ?? folderName
  return {
    local: {
      identity: problemText(details.local_identity) ?? problemIdentity(localTitle, details.local_year ?? details.medialogue_year) ?? localTitle,
      tmdbId: problem.tmdbId ?? (typeof details.local_tmdb_id === 'number' ? details.local_tmdb_id : undefined),
    },
    plex: {
      identity: problemText(details.plex_identity) ?? problemIdentity(details.plex_title, details.plex_year),
      tmdbId: typeof details.plex_tmdb_id === 'number' ? details.plex_tmdb_id : undefined,
    },
    differences: Array.isArray(details.differences)
      ? details.differences.map((value) => problemText(value)).filter((value): value is string => Boolean(value))
      : [],
  }
}

function IdentityCard({ source, identity, tmdbId, poster, tone }: { source: string; identity?: string; tmdbId?: number; poster?: string; tone: 'local' | 'plex' }) {
  return <div className={`identity-card identity-card-${tone}`}>
    <div className="identity-poster">
      {poster
        ? <PosterImage reference={poster} title={identity ?? source} />
        : <Icon name={tone === 'plex' ? 'plex' : 'clapper'} size={22} />}
    </div>
    <div className="identity-copy">
      <span className="identity-source">{source}</span>
      <strong title={identity}>{identity ?? 'Not recorded'}</strong>
      {/* The id is what actually settles the dispute. Nothing is shown when
          there is none: Plex artwork and ids are not captured, and a line
          announcing that on every card says nothing about this title. */}
      {tmdbId ? <code>TMDB {tmdbId}</code> : null}
    </div>
  </div>
}

/** Front and centre, not folded into troubleshooting: an identity disagreement
 *  is a question about which title this is, and that is the whole Problem. */
function PlexIdentityComparison({ problem }: { problem: Problem }) {
  const { local, plex, differences } = plexIdentitySides(problem)
  return <section className="identity-conflict">
    <div className="identity-conflict-grid">
      <IdentityCard source="Medialogue says" identity={local.identity} tmdbId={local.tmdbId} poster={problem.posterRef} tone="local" />
      <span className="identity-versus">vs</span>
      {/* No poster on this side: Plex artwork is served from the Plex server
          behind its own token, so nothing is fetchable from the browser. */}
      <IdentityCard source="Plex says" identity={plex.identity} tmdbId={plex.tmdbId} tone="plex" />
    </div>
    {differences.length > 0 && <p className="identity-differences">Disagrees on {differences.join(', ')}.</p>}
  </section>
}

function ProblemKeyDetails({ problem, duplicateMovie }: { problem: Problem; duplicateMovie: Movie | null }) {
  const details = problem.details ?? {}
  const rows: Array<[string, string, string?]> = []
  if (problem.code === 'DUPLICATE_PHYSICAL_RELEASE' && duplicateMovie?.releasesDetail?.length) {
    duplicateMovieReleases(problem, duplicateMovie).forEach((release, index) => {
      const directory = release.directories.find((item) => item.exists) ?? release.directories[0]
      const parts = pathParts(directory?.path)
      rows.push([
        `Copy ${index + 1}`,
        parts?.name ?? release.name,
        [directory?.storageRoot, parts?.parent, [release.quality, release.edition].filter(Boolean).join(' · ')].filter(Boolean).join(' · '),
      ])
    })
  } else if (problem.code === 'DUPLICATE_PHYSICAL_RELEASE' && Array.isArray(details.release_names)) {
    // Without the movie loaded the generic branch printed a single Path row for
    // a Problem whose whole point is that there are two copies.
    const base = pathParts(problemText(details.path))?.parent
    details.release_names.map((value) => problemText(value)).filter(Boolean).forEach((name, index) => {
      rows.push([`Copy ${index + 1}`, name as string, base])
    })
  } else if (problem.code === 'DUPLICATE_EPISODE_RELEASE' && Array.isArray(details.media_files)) {
    const preferredId = problemText(details.preferred_media_file_id)
    // The filename leads, because duplicates of one episode share almost all of
    // their path. The folder that contains it follows, without repeating the
    // name. "Verified on disk" is dropped: it is true of every row that counts
    // as a duplicate, so only the exception is worth saying.
    details.media_files.map(problemRecord).forEach((file, index) => {
      const parts = pathParts(problemText(file.path))
      const filename = problemText(file.filename) ?? parts?.name
      const directory = parts?.parent
      rows.push([
        `File ${index + 1}`,
        filename ?? 'File not recorded',
        [
          problemText(file.media_file_id) === preferredId ? 'Preferred' : '',
          problemText(file.storage_root_name) ?? 'Unknown media folder',
          directory ?? '',
          file.physical_exists === true ? '' : file.missing_since ? `Not observed since ${problemText(file.missing_since)}` : 'Not currently verified on disk',
        ].filter(Boolean).join(' · '),
      ])
    })
  } else if (problem.code === 'PLEX_IDENTITY_MISMATCH' && Array.isArray(details.conflicts)) {
    const conflict = problemRecord(details.conflicts[0])
    rows.push(
      ['Medialogue says', problemText(conflict.local_episode) ?? problemText(details.local_identity) ?? problem.subject, problemText(conflict.local_path)],
      ['Plex says', problemText(conflict.plex_episode) ?? problemText(details.plex_identity) ?? 'Conflicting Plex identity', problemText(conflict.plex_path)],
    )
  } else {
    const path = problemText(details.path ?? details.local_path ?? details.reported_path ?? details.resolved_path)
    const parts = pathParts(path)
    if (parts) rows.push(['Path', parts.name, parts.parent])
    const parsed = parserIdentity(details)
    const parserWarnings = parsed.warnings
    if (parsed.identity) rows.push(['Parsed as', parsed.identity, parserWarnings.length ? parserWarnings.join(' · ') : undefined])
    const qbitPath = problemText(details.qbittorrent_path ?? details.remote_path)
    const qbitParts = qbitPath && qbitPath !== path ? pathParts(qbitPath) : undefined
    if (qbitParts) rows.push(['qBittorrent path', qbitParts.name, qbitParts.parent])
  }
  if (!rows.length) rows.push(['Affected media', problem.subject, problem.entityType ? `${problem.entityType} evidence` : undefined])
  return <section className="problems-key-details">{rows.map(([label, value, note], index) => <div className="problems-detail-row" key={`${label}-${index}`}><span>{label}</span><div><strong title={value}>{value}</strong>{note && <small>{note}</small>}</div></div>)}</section>
}

const CUSTOM_EDITION = '__custom__'
const THEATRICAL_EDITION = 'Theatrical Cut'

type EditionDraft = { selection: string; custom: string }

function initialEditionDraft(release: MovieRelease, options: string[]): EditionDraft {
  const edition = release.manualEditionOverride ?? release.parsedEdition ?? THEATRICAL_EDITION
  if (edition === THEATRICAL_EDITION || options.includes(edition)) return { selection: edition, custom: '' }
  return { selection: CUSTOM_EDITION, custom: edition }
}

function editionChoices(options: string[]): string[] {
  return [THEATRICAL_EDITION, ...options.filter((edition) => edition !== THEATRICAL_EDITION)]
}

function editionUpdate(releaseId: string, draft: EditionDraft): MovieEditionUpdate {
  return { releaseId, mode: 'named', edition: draft.selection === CUSTOM_EDITION ? draft.custom.trim() : draft.selection }
}

function movieEditionSlot(edition?: string): string {
  const normalized = (edition ?? '').trim().toLocaleLowerCase()
  return normalized === 'theatrical cut' ? '' : normalized
}

function MovieEditionResolver({ problem, movie, options, loading, onSaved }: { problem: Problem; movie: Movie; options: string[]; loading: boolean; onSaved: (resolved: boolean) => Promise<void> }) {
  const releases = duplicateMovieReleases(problem, movie)
  const initial = Object.fromEntries(releases.map((release) => [release.id, initialEditionDraft(release, options)]))
  const [drafts, setDrafts] = useState<Record<string, EditionDraft>>(initial)
  const [savingId, setSavingId] = useState('')
  const [error, setError] = useState('')
  const persist = async (releaseId: string, draft: EditionDraft) => {
    const update = editionUpdate(releaseId, draft)
    if (!update.edition?.trim()) return
    setSavingId(releaseId); setError('')
    try {
      const result = await api.updateMovieReleaseEditions(movie.id, [update])
      await onSaved(result.duplicateResolved)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save movie editions.') }
    finally { setSavingId('') }
  }

  return <section className="movie-edition-resolver">
    <div className="problems-resolver-head"><div><div className="eyebrow">MOVIE EDITIONS</div><p>Choose the edition for each copy. Changes are saved immediately; files and folders stay untouched.</p></div></div>
    <div className="movie-edition-list">{releases.map((release, index) => {
      const draft = drafts[release.id] ?? initialEditionDraft(release, options)
      const directory = release.directories.find((item) => item.exists) ?? release.directories[0]
      const original = initialEditionDraft(release, options)
      return <div className="movie-edition-copy" key={release.id}>
        <div className="movie-edition-copy-head"><div><span>Copy {index + 1}</span><strong>{release.name}</strong><small>{directory?.path ?? 'Path unavailable'}</small></div><div className="movie-edition-technical">{[release.quality, release.releaseGroup].filter(Boolean).join(' · ') || 'No quality details'}</div></div>
        <label><span>Edition</span><Dropdown
          ariaLabel={`Edition for copy ${index + 1}`}
          value={draft.selection}
          disabled={loading || savingId === release.id}
          options={[...editionChoices(options).map((edition) => ({ value: edition, label: edition })), { value: CUSTOM_EDITION, label: 'Other…' }]}
          onChange={(value) => {
            const next = { ...draft, selection: value }
            setDrafts((current) => ({ ...current, [release.id]: next }))
            if (next.selection !== CUSTOM_EDITION) void persist(release.id, next)
          }}
        /></label>
        {draft.selection === CUSTOM_EDITION && <label><span>Custom edition</span><Input value={draft.custom} maxLength={128} disabled={loading || savingId === release.id} onChange={(event) => setDrafts((current) => ({ ...current, [release.id]: { ...draft, custom: event.target.value } }))} onBlur={() => {
          if (draft.custom.trim() && JSON.stringify(editionUpdate(release.id, draft)) !== JSON.stringify(editionUpdate(release.id, original))) void persist(release.id, draft)
        }} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} placeholder="Edition name" /></label>}
        <small className="movie-edition-detected">Detected from folder: {release.parsedEdition ?? 'Theatrical Cut (default)'}{release.editionOverrideActive ? ' · manually overridden' : ''}{savingId === release.id ? ' · Saving…' : ''}</small>
      </div>
    })}</div>
    {error && <div className="field-error">{error}</div>}
  </section>
}

function IdentityPicker({
  kind,
  matches,
  selected,
  query,
  loading,
  onQuery,
  onSearch,
  onSelect,
  onApply,
  onCancel,
  submitLabel,
}: {
  kind: 'movie' | 'show'
  matches: TMDBCandidate[]
  selected?: TMDBCandidate
  query: string
  loading: boolean
  onQuery: (value: string) => void
  onSearch: () => void
  onSelect: (match: TMDBCandidate) => void
  onApply: () => void
  onCancel?: () => void
  submitLabel: string
}) {
  return <section className="problems-identity-resolver">
    <div className="problems-resolver-head"><div><div className="eyebrow">POSSIBLE MATCHES</div><p>Choose the exact {kind}. Media will not be renamed or moved.</p></div>{onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}</div>
    {matches.length ? <div className="problems-identity-grid">{matches.slice(0, 6).map((match) => <button type="button" className={`problems-identity-card ${selected?.tmdbId === match.tmdbId ? 'selected' : ''}`} key={match.tmdbId} onClick={() => onSelect(match)} aria-pressed={selected?.tmdbId === match.tmdbId}>
      <span className="problems-identity-poster">{match.posterRef ? <img src={tmdbPosterUrl(match.posterRef)} alt={`${match.title} poster`} loading="lazy" referrerPolicy="no-referrer" /> : <Icon name={kind === 'show' ? 'tv-screen' : 'clapper'} size={26} />}</span>
      <span className="problems-identity-copy"><span className="problems-identity-title"><strong>{match.title}</strong>{match.year && <span>{match.year}</span>}</span>{match.overview && <span className="problems-identity-overview">{match.overview}</span>}<span className="problems-identity-credits"><span><strong>{kind === 'show' ? 'Creator' : 'Director'}:</strong> {match.director ?? 'Not listed'}</span><span><strong>Cast:</strong> {match.cast?.length ? match.cast.join(' · ') : 'Not listed'}</span></span><span className="problems-identity-meta">TMDB {match.tmdbId}</span></span>
      <span className="problems-identity-radio" aria-hidden="true" />
    </button>)}</div> : <div className="problems-candidate-empty">{loading ? 'Loading TMDB suggestions…' : 'Search TMDB to load possible matches.'}</div>}
    <div className="problems-tmdb-search"><div><strong>Not one of these?</strong><span>Search TMDB directly</span></div><Input value={query} onChange={(event) => onQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') onSearch() }} placeholder="Title or title + year" /><Button variant="ghost" icon="search" onClick={onSearch} disabled={loading || !query.trim()}>{loading ? 'Searching…' : 'Search TMDB'}</Button></div>
    <div className="problems-identity-submit"><Button variant="primary" onClick={onApply} disabled={loading || !selected}>{submitLabel}</Button></div>
  </section>
}

function ProblemsIdentityPicker({
  problem,
  matches,
  selected,
  query,
  loading,
  onQuery,
  onSearch,
  onSelect,
  onApply,
  onCancel,
}: {
  problem: Problem
  matches: TMDBCandidate[]
  selected?: TMDBCandidate
  query: string
  loading: boolean
  onQuery: (value: string) => void
  onSearch: () => void
  onSelect: (match: TMDBCandidate) => void
  onApply: () => void
  onCancel?: () => void
}) {
  return <IdentityPicker kind={problem.entityType === 'show' ? 'show' : 'movie'} matches={matches} selected={selected} query={query} loading={loading} onQuery={onQuery} onSearch={onSearch} onSelect={onSelect} onApply={onApply} onCancel={onCancel} submitLabel="Use selected match" />
}

export function ProblemsPage() {
  const confirm = useConfirm()
  const navigate = useNavigate()
  const [, setProblemSearchParams] = useSearchParams()
  const [items, setItems] = useState<Problem[]>([])
  const [selected, setSelected] = useUrlState('problem')
  const [workflow] = useUrlState('workflow', 'all')
  const [reasonFilter] = useUrlState('reason', 'all')
  const [severityFilter] = useUrlState('severity', 'all')
  const [queueStatus] = useUrlState('problemStatus', 'open')
  const [page, setPage] = useUrlNumber('page', 1)
  const [pages, setPages] = useState(0)
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState<{ open: number; suppressed: number; workflows: Record<string, number> }>({ open: 0, suppressed: 0, workflows: {} })
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [duplicateMovie, setDuplicateMovie] = useState<Movie | null>(null)
  const [editionOptions, setEditionOptions] = useState<string[]>([])
  const [tmdbQuery, setTmdbQuery] = useState('')
  const [tmdbMatches, setTmdbMatches] = useState<TMDBCandidate[]>([])
  const [selectedTmdb, setSelectedTmdb] = useState<TMDBCandidate>()
  const [editingPlexIdentity, setEditingPlexIdentity] = useState(false)
  const [plexConfiguration, setPlexConfiguration] = useState<Awaited<ReturnType<typeof api.plexConfiguration>>>()
  const loadGeneration = useRef(0)
  const reloadCurrent = useRef<() => void>(() => undefined)
  const updateProblemView = (changes: Record<string, string | null>) => {
    setProblemSearchParams((current) => updateUrlParams(current, changes), { replace: true })
  }

  const load = async (targetPage = page, preserveMessage = false) => {
    const generation = ++loadGeneration.current
    setLoading(true)
    try {
      const [payload, nextSummary] = await Promise.all([
        api.problemsPage({ status: queueStatus, page: targetPage, pageSize: 250, category: reasonFilter, severity: severityFilter, workflow: queueStatus === 'open' ? workflow : 'all' }),
        api.problemSummary(),
      ])
      if (generation !== loadGeneration.current) return
      setItems(payload.items); setTotal(payload.total); setPages(payload.pages); setPage(payload.page); setSummary(nextSummary)
      if (selected && !payload.items.some((problem) => problem.id === selected)) setSelected('')
      if (!preserveMessage) setMessage('')
    } catch (reason) { if (generation === loadGeneration.current) setMessage(reason instanceof Error ? reason.message : 'Could not load Problems.') }
    finally { if (generation === loadGeneration.current) setLoading(false) }
  }
  reloadCurrent.current = () => void load(page, true)

  useEffect(() => { void load(1) }, [workflow, reasonFilter, severityFilter, queueStatus])
  useEffect(() => {
    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    let timer: number | undefined
    const invalidate = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      timer = window.setTimeout(() => reloadCurrent.current(), 150)
    }
    stream.addEventListener('problem.created', invalidate); stream.addEventListener('problem.updated', invalidate); stream.addEventListener('problem.resolved', invalidate); stream.addEventListener('problem.deleted', invalidate)
    return () => { if (timer !== undefined) window.clearTimeout(timer); stream.close() }
  }, [])
  useEffect(() => { api.plexConfiguration().then(setPlexConfiguration).catch(() => undefined) }, [])

  const visible = items.filter((problem) => !search.trim() || [problem.title, problem.subject, problem.detail, problem.code].join(' ').toLowerCase().includes(search.trim().toLowerCase()))
  const current = visible.find((problem) => problem.id === selected) ?? visible[0]

  const removeSolvedProblem = (problem: Problem) => {
    setItems((existing) => existing.filter((item) => item.id !== problem.id))
    setTotal((value) => Math.max(0, value - 1))
    setSelected((value) => value === problem.id ? '' : value)
    setSummary((value) => {
      if (queueStatus === 'dismissed') return { ...value, suppressed: Math.max(0, value.suppressed - 1) }
      return {
        ...value,
        open: Math.max(0, value.open - 1),
        workflows: { ...value.workflows, [problem.workflow]: Math.max(0, (value.workflows[problem.workflow] ?? 0) - 1) },
      }
    })
  }

  useEffect(() => {
    setDuplicateMovie(null); setEditionOptions([]); setSelectedTmdb(undefined); setEditingPlexIdentity(false); setTmdbMatches([])
    if (!current) return
    if (current.code === 'DUPLICATE_PHYSICAL_RELEASE' && current.entityId) {
      void Promise.all([api.movie(current.entityId), api.movieEditionOptions()]).then(([movie, options]) => { setDuplicateMovie(movie); setEditionOptions(options) }).catch(() => undefined)
    }
    const canMatch = current.availableActions?.some((action) => action === 'confirm_movie_match' || action === 'confirm_show_match')
    if (!canMatch || current.code === 'PLEX_IDENTITY_MISMATCH') return
    const details = current.details ?? {}
    const query = [problemText(details.parsed_title ?? details.title) ?? current.subject, problemText(details.parsed_year ?? details.year)].filter(Boolean).join(' ')
    setTmdbQuery(query)
    const candidates = Array.isArray(details.tmdb_candidates) ? details.tmdb_candidates.map(problemRecord).map(problemCandidateFromEvidence).filter((item): item is TMDBCandidate => Boolean(item)) : []
    setTmdbMatches(candidates.slice(0, 6))
    const searchCandidates = current.availableActions?.includes('confirm_show_match') ? api.lookupShows(query) : api.lookupMovies(query)
    void searchCandidates.then((matches) => setTmdbMatches(matches.slice(0, 6))).catch(() => undefined)
  }, [current?.id])

  const runTmdbSearch = async () => {
    if (!current || !tmdbQuery.trim()) return
    setLoading(true); setMessage('')
    try {
      const matches = current.availableActions?.includes('confirm_show_match') ? await api.lookupShows(tmdbQuery.trim()) : await api.lookupMovies(tmdbQuery.trim())
      setTmdbMatches(matches.slice(0, 6)); setSelectedTmdb(undefined)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'TMDB lookup failed.') }
    finally { setLoading(false) }
  }

  const applyTmdbMatch = async () => {
    if (!current || !selectedTmdb) return
    const action = current.availableActions?.includes('confirm_show_match') ? 'confirm_show_match' : 'confirm_movie_match'
    setLoading(true); setMessage('')
    try {
      const resolved = await api.resolveProblem(current.id, action, { tmdb_id: selectedTmdb.tmdbId })
      removeSolvedProblem(current)
      await load(page, true)
      const followup = typeof resolved.resolution?.followup_job_id === 'string'
      setMessage(`Matched to ${selectedTmdb.title}${selectedTmdb.year ? ` (${selectedTmdb.year})` : ''}.${followup ? ' Metadata import and file matching are still running, see Tasks.' : ''}`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not apply the selected identity.') }
    finally { setLoading(false) }
  }

  const recheckCurrent = async () => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      const response = await api.resolveProblem(current.id, 'recheck')
      const jobId = typeof response.resolution?.recheck_parent_job_id === 'string' ? response.resolution.recheck_parent_job_id : ''
      const jobs = jobId ? await api.waitForJobs([jobId]) : []
      if (jobs.some((job) => job.summary?.condition_cleared === true)) removeSolvedProblem(current)
      await load(page, true)
      setMessage('Recheck finished. Solved problems were removed from the queue.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not recheck this Problem.') }
    finally { setLoading(false) }
  }

  const editionsSaved = async (resolved: boolean) => {
    if (!current) return
    if (resolved) removeSolvedProblem(current)
    await load(page, true)
    if (!resolved && current.entityId) setDuplicateMovie(await api.movie(current.entityId))
    setMessage(resolved ? 'Editions saved. The copies are different editions, so the Problem was resolved.' : 'Editions saved. At least two copies still have the same edition.')
  }

  const recheckAll = async () => {
    setLoading(true); setMessage('')
    try { const result = await api.recheckProblems(); setMessage(result.requested ? `Rechecking ${result.requested} existing Problem${result.requested === 1 ? '' : 's'}…` : 'There are no open Problems to recheck.') }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not recheck Problems.') }
    finally { setLoading(false) }
  }

  const adminAction = async (action: 'dismiss' | 'restore' | 'delete') => {
    if (!current) return
    const prompt = action === 'dismiss' ? 'Suppress this Problem fingerprint? It will not be recreated until restored.' : action === 'restore' ? 'Restore this suppressed Problem to the active queue?' : 'Permanently delete this Problem record? It may be recreated if the condition is observed again.'
    if (!await confirm({ title: prompt, confirmLabel: 'Continue' })) return
    setLoading(true); setMessage('')
    try {
      if (action === 'delete') await api.deleteProblem(current.id)
      else await api.resolveProblem(current.id, action)
      removeSolvedProblem(current)
      await load(page, true); setMessage(action === 'dismiss' ? 'Problem suppressed.' : action === 'restore' ? 'Problem restored to the active queue.' : 'Problem record deleted.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : `Could not ${action} the Problem.`) }
    finally { setLoading(false) }
  }

  const plexRatingKey = (() => {
    const details = current?.details ?? {}
    if (problemText(details.plex_rating_key)) return problemText(details.plex_rating_key)
    if (Array.isArray(details.conflicts)) return problemText(problemRecord(details.conflicts[0]).plex_rating_key)
    return undefined
  })()
  const openPlex = () => {
    if (!plexConfiguration?.url || !plexRatingKey) { setMessage('This problem has no saved Plex link. Recheck it to fetch the current one.'); return }
    const machine = plexConfiguration.machine_identifier ? `/server/${encodeURIComponent(plexConfiguration.machine_identifier)}` : ''
    const url = `${plexConfiguration.url.replace(/\/$/, '')}/web/index.html#!${machine}/details?key=${encodeURIComponent(`/library/metadata/${plexRatingKey}`)}`
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const beginPlexIdentityChange = () => {
    if (!current) return
    const details = current.details ?? {}
    const query = problemText(details.local_identity ?? details.medialogue_title) ?? current.subject
    setTmdbQuery(query); setEditingPlexIdentity(true); setSelectedTmdb(undefined); setTmdbMatches([])
    const request = current.availableActions?.includes('confirm_show_match') ? api.lookupShows(query) : api.lookupMovies(query)
    void request.then((matches) => setTmdbMatches(matches.slice(0, 6))).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'TMDB lookup failed.'))
  }

  return <Page title="Problems" subtitle="See what is wrong, exactly where it is, and what to do next.">
    <div className="problems-toolbar"><label className="problems-search"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search issues, media, paths…" /></label><div className="problems-toolbar-rest"><Select value={reasonFilter} onChange={(event) => updateProblemView({ reason: event.target.value === 'all' ? null : event.target.value, problem: null, page: null })}><option value="all">All issue types</option><option value="duplicates">Duplicates</option><option value="identity">Identity / matching</option><option value="paths">Paths / storage</option><option value="PLEX_IDENTITY_MISMATCH">Plex conflicts</option></Select><Select value={severityFilter} onChange={(event) => updateProblemView({ severity: event.target.value === 'all' ? null : event.target.value, problem: null, page: null })}><option value="all">All priorities</option><option value="high">High priority</option><option value="medium">Medium priority</option><option value="low">Low priority</option></Select><div className="problems-toolbar-actions"><Button variant="ghost" onClick={() => updateProblemView({ problemStatus: queueStatus === 'dismissed' ? null : 'dismissed', problem: null, page: null })}>{queueStatus === 'dismissed' ? 'Back to active' : `Suppressed${summary.suppressed ? ` (${summary.suppressed})` : ''}`}</Button><Button variant="ghost" icon="refresh" onClick={() => void recheckAll()} disabled={loading || !summary.open}>{loading ? 'Working…' : 'Check all again'}</Button></div></div></div>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    <section className={`problems-layout ${selected ? 'show-detail' : ''}`}>
      <div className="problems-list"><div className="problems-list-head"><span>{queueStatus === 'dismissed' ? 'Suppressed Problems' : 'Problems'}</span><span>Showing {visible.length} of {total}</span></div>{visible.length ? visible.map((problem) => <button type="button" className={`problems-row ${current?.id === problem.id ? 'selected' : ''}`} onClick={() => setSelected(problem.id)} key={problem.id}><span className={`problems-severity severity-${problem.severity}`}><Icon name="alert" size={15} /></span><span className="problems-row-copy"><strong>{problem.title}</strong>{problemSubjectLine(problem) ? <span>{problemSubjectLine(problem)}</span> : null}<small>{problem.created}</small></span><Icon name="chevron" size={15} /></button>) : <EmptyState icon={queueStatus === 'dismissed' ? 'eye-off' : 'check'} title={queueStatus === 'dismissed' ? 'No suppressed Problems' : 'No Problems match these filters'} detail={queueStatus === 'dismissed' ? 'Admin suppressions will appear here.' : 'Change a filter or enjoy the quiet queue.'} />}</div>
      <article className="problems-detail">{current ? <><header className="problems-detail-head"><button className="icon-button problems-back-button" type="button" onClick={() => setSelected('')} aria-label="Back to Problems"><Icon name="chevron" size={16} style={{ transform: 'rotate(180deg)' }} /></button>
<div>{queueStatus === 'dismissed' && <div className="eyebrow">SUPPRESSED</div>}<h2><span className="problems-detail-title">{current.title}</span><span className={`problems-priority priority-${current.severity}`}>{current.severity} priority</span></h2></div>
<div className="problems-detail-actions">
  {queueStatus === 'dismissed'
    ? <Button variant="ghost" onClick={() => void adminAction('restore')}>Restore</Button>
    : <Button variant="ghost" onClick={() => void adminAction('dismiss')}>Suppress</Button>}
  <Button variant="danger" onClick={() => void adminAction('delete')}>Delete</Button>
</div></header><div className="problems-detail-body">{/* Its description is word for word the resolution guidance shown below. */}
        {current.code !== 'DUPLICATE_EPISODE_RELEASE' && <div className="problems-lead"><p>{current.detail}</p></div>}
        {current.code === 'PLEX_IDENTITY_MISMATCH' && <PlexIdentityComparison problem={current} />}
        <ProblemKeyDetails problem={current} duplicateMovie={duplicateMovie} />
        {queueStatus === 'open' && current.code === 'DUPLICATE_PHYSICAL_RELEASE' && duplicateMovie && <MovieEditionResolver key={`${current.id}-${duplicateMovie.releasesDetail?.map((release) => `${release.id}:${release.editionOverrideActive}:${release.edition ?? ''}`).join('|')}`} problem={current} movie={duplicateMovie} options={editionOptions} loading={loading} onSaved={editionsSaved} />}
        {queueStatus === 'open' && current.workflow === 'manual' && current.code !== 'DUPLICATE_PHYSICAL_RELEASE' && <div className="problems-resolution"><p>Delete the unwanted copy from your drive, then recheck. Medialogue will not delete media or choose a preferred copy for you.</p><Button variant="primary" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && current.workflow === 'config' && <div className="problems-resolution"><p>Correct the relevant media-folder or path-mapping configuration, then recheck this Problem.</p><Button variant="primary" icon="folder" onClick={() => navigate('/settings?tab=Media%20Folders')}>Open Media Folders</Button><Button variant="ghost" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && current.code === 'PLEX_IDENTITY_MISMATCH' && !editingPlexIdentity && <div className="problems-resolution"><p>Which system needs correcting?</p><Button variant="primary" icon="external" onClick={openPlex}>Medialogue is correct, open Plex</Button>{current.availableActions?.some((action) => action === 'confirm_movie_match' || action === 'confirm_show_match') && <Button variant="ghost" onClick={beginPlexIdentityChange}>Medialogue is wrong, change identity</Button>}<Button variant="ghost" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && current.workflow === 'waiting' && current.code !== 'PLEX_IDENTITY_MISMATCH' && <div className="problems-resolution"><p>Recheck the current external and filesystem evidence. If the condition is gone, this Problem will leave the queue.</p><Button variant="primary" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && (current.workflow === 'choice' || editingPlexIdentity) && <ProblemsIdentityPicker problem={current} matches={tmdbMatches} selected={selectedTmdb} query={tmdbQuery} loading={loading} onQuery={setTmdbQuery} onSearch={() => void runTmdbSearch()} onSelect={setSelectedTmdb} onApply={() => void applyTmdbMatch()} onCancel={editingPlexIdentity ? () => setEditingPlexIdentity(false) : undefined} />}
        <ProblemEvidenceDetails problem={current} />
      </div></> : <EmptyState icon="alert" title="Select a Problem" detail="Choose an item to see the details and what to do next." />}</article>
    </section>
    {pages > 1 && <div className="pagination-bar"><Button variant="ghost" disabled={page <= 1 || loading} onClick={() => void load(page - 1)}>Previous</Button><span>Page {page} of {pages}</span><Button variant="ghost" disabled={page >= pages || loading} onClick={() => void load(page + 1)}>Next</Button></div>}
  </Page>
}

function reviewFolderName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path
}

function capitalise(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export function ReviewPage() {
  const [items, setItems] = useState<MediaDiscovery[]>([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [rootName, setRootName] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [editionOptions, setEditionOptions] = useState<string[]>([])
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [profileBusy, setProfileBusy] = useState(false)

  const load = async (quiet = false) => {
    if (!quiet) setLoading(true)
    try { setItems(await api.discoveries()); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load Review.') }
    finally { if (!quiet) setLoading(false) }
  }
  useEffect(() => {
    void load()
    void api.movieEditionOptions().then(setEditionOptions).catch(() => undefined)
    void api.qualityProfiles().then(setProfiles).catch(() => undefined)
  }, [])
  useLiveLibraryRefresh(() => load(true))

  // Confirmed-missing directories are not actionable review work. The API
  // excludes them too; this guard also prevents an already-loaded stale row
  // from lingering until the next refresh.
  const actionableItems = useMemo(() => items.filter((item) => item.exists), [items])

  useEffect(() => {
    if (!selectedId) return
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('.review-match-cell, .review-folder-cell, .review-alt-pane, .dropdown-menu')) return
      setSelectedId('')
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [selectedId])

  const roots = useMemo(() => reviewRoots(actionableItems), [actionableItems])
  useEffect(() => {
    if (!roots.length) { setRootName(''); return }
    if (!roots.some((root) => root.name === rootName)) setRootName(roots[0].name)
  }, [roots, rootName])

  const root = roots.find((entry) => entry.name === rootName)
  const rows = useMemo(() => actionableItems.filter((item) => item.rootName === rootName), [actionableItems, rootName])
  const selected = rows.find((item) => item.id === selectedId)
  const importable = rows.filter((item) => !excluded.has(item.id) && item.selected && item.exists)
  const duplicateEditionIds = useMemo(() => {
    const groups = new Map<string, string[]>()
    importable.filter((item) => item.mediaType === 'movies' && item.selected).forEach((item) => {
      const key = `${item.selected!.tmdbId}:${movieEditionSlot(item.edition)}`
      groups.set(key, [...(groups.get(key) ?? []), item.id])
    })
    return new Set([...groups.values()].filter((ids) => ids.length > 1).flat())
  }, [importable])

  const sharedMovieIds = useMemo(() => {
    const groups = new Map<number, string[]>()
    rows.filter((item) => item.mediaType === 'movies' && item.selected).forEach((item) => {
      const tmdbId = item.selected!.tmdbId
      groups.set(tmdbId, [...(groups.get(tmdbId) ?? []), item.id])
    })
    return new Set([...groups.values()].filter((ids) => ids.length > 1).flat())
  }, [rows])
  const conflictingIds = new Set(importable.filter((item) => item.libraryEditionConflict).map((item) => item.id))
  duplicateEditionIds.forEach((id) => conflictingIds.add(id))
  const hasEditionConflicts = conflictingIds.size > 0
  const noun = root?.mediaType === 'shows' ? 'show' : 'movie'

  const toggle = (id: string) => setExcluded((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const setRowProfile = async (item: MediaDiscovery, profileId: string | null) => {
    setProfileBusy(true); setError('')
    try {
      const updated = await api.updateDiscoveryQualityProfile(item.id, profileId)
      setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not set the Quality Profile.') }
    finally { setProfileBusy(false) }
  }

  // What the included folders are already set to, or undefined when they differ.
  // Without this the control had nothing to display and needed a placeholder.
  const sharedProfile = useMemo(() => {
    const targets = rows.filter((item) => !excluded.has(item.id))
    if (!targets.length) return undefined
    const first = targets[0].qualityProfileId ?? ''
    return targets.every((item) => (item.qualityProfileId ?? '') === first) ? first : undefined
  }, [rows, excluded])

  // Applies to the folders currently listed and still included, not to the whole
  // queue: the visible selection is what the reviewer believes they are changing.
  const setAllProfiles = async (profileId: string | null) => {
    const targets = rows.filter((item) => !excluded.has(item.id))
    if (!targets.length) return
    setProfileBusy(true); setError(''); setMessage('')
    try {
      const result = await api.bulkDiscoveryQualityProfile(targets.map((item) => item.id), profileId)
      await load(true)
      const name = profiles.find((profile) => profile.id === profileId)?.name
      setMessage(`${result.succeeded.length} folder${result.succeeded.length === 1 ? '' : 's'} set to ${name ?? 'no Quality Profile'}.`)
      if (result.failed.length) setError(`${result.failed.length} could not be changed: ${result.failed[0].message}`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not set the Quality Profile.') }
    finally { setProfileBusy(false) }
  }

  const runImport = async () => {
    const ids = importable.map((item) => item.id)
    if (!ids.length || hasEditionConflicts) return
    setImporting(true); setError(''); setMessage('')
    try {
      const result = await api.bulkReviewDiscoveries(ids, 'approve')
      setItems((current) => current.filter((item) => !result.succeeded.includes(item.id)))
      window.dispatchEvent(new Event('medialogue-review-changed'))
      setMessage(`${result.succeeded.length} ${result.succeeded.length === 1 ? noun : `${noun}s`} imported.`)
      if (result.failed.length) {
        setError(`${result.failed.length} could not be imported: ${result.failed.map((failure) => failure.message).join(' ')}`)
        await load()
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not import those.') }
    finally { setImporting(false) }
  }

  const choose = async (item: MediaDiscovery, candidate: TMDBCandidate) => {
    setError('')
    const previous = item.selected
    setItems((current) => current.map((entry) => entry.id === item.id ? { ...entry, selected: candidate } : entry))
    try {
      const updated = await api.selectDiscovery(item.id, candidate)
      setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))
    } catch (reason) {
      // Put the old match back rather than leaving the row claiming a choice
      // that was never recorded.
      setItems((current) => current.map((entry) => entry.id === item.id ? { ...entry, selected: previous } : entry))
      setError(reason instanceof Error ? reason.message : 'Could not change the match.')
    }
  }

  if (loading) return <Page title="Review" subtitle="Check what a scan found, then import it in one go.">
    <div className="review-loading">Loading detected media…</div>
  </Page>

  if (!actionableItems.length) return <Page title="Review" subtitle="Check what a scan found, then import it in one go.">
    <EmptyState icon="review" title="Nothing to review" detail="New media from review-enabled media folders will appear here after a scan." />
  </Page>

  return <Page title="Review" subtitle="Check what a scan found, then import it in one go.">
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {message && !error && <div className="settings-note note-ok"><Icon name="check" size={16} /><span>{message}</span></div>}

    <div className="review-roots-label">Media folders</div>
    <div className="review-roots">
      {roots.map((entry) => <button
        type="button"
        key={entry.name}
        className={`review-root ${entry.name === rootName ? 'active' : ''}`}
        onClick={() => { setRootName(entry.name); setSelectedId('') }}
      >
        <span className="review-root-name">
          <span className={`kind-box kind-${entry.mediaType === 'movies' ? 'movie' : 'show'}`} aria-label={entry.mediaType === 'movies' ? 'Movies folder' : 'Shows folder'}>
            <Icon name={entry.mediaType === 'movies' ? 'clapper' : 'tv-screen'} size={13} />
          </span>
          <strong>{entry.name}</strong>
        </span>
        <small>{entry.count} {entry.count === 1 ? 'folder' : 'folders'}</small>
      </button>)}
    </div>

    <div className="review-workspace">

      <div className="review-scroll">
        <span className="review-col-head">{noun === 'show' ? 'Show' : 'Movie'} folder</span>
        <span className="review-col-head">Detected {noun}</span>
        {rows.map((item) => {
            const match = item.selected
            const active = item.id === selectedId
            const blocked = !item.exists || !match
            const included = !excluded.has(item.id)
            const editionConflict = included && conflictingIds.has(item.id)
            const state = !match ? { tone: 'needs-match', text: 'No results' }
              : item.confidence !== undefined && item.confidence < CONFIDENT ? { tone: 'unsure', text: `${Math.round(item.confidence * 100)}%` }
              : null
            // A display:contents wrapper, so both cells stay direct grid items
            // while still sharing one hover target across the whole row.
            return <div className="review-row" key={item.id}>
              <div
                className={`review-folder-cell ${active ? 'active' : ''} ${included ? '' : 'excluded'}`}
                onClick={() => setSelectedId(active ? '' : item.id)}
              >
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={included && !blocked}
                  aria-label={`Import ${reviewFolderName(item.path)}`}
                  className={`review-check ${included && !blocked ? 'on' : ''}`}
                  // Including or leaving out a folder is not the same intent as
                  // opening it, so the checkbox must not also select the row.
                  onClick={(event) => { event.stopPropagation(); toggle(item.id) }}
                  disabled={blocked}
                >{included && !blocked && <Icon name="check" size={11} />}</button>
                <div className="review-folder-copy">
                  <Tooltip content={item.path}><code>{reviewFolderName(item.path)}</code></Tooltip>
                  {/* Reveals on row hover like the edition control, but keeps its
                      space so the row does not resize under the pointer. */}
                  <span
                    className={`review-profile-cell ${item.qualityProfileId ? '' : 'optional'}`}
                    onClick={(event) => { event.preventDefault(); event.stopPropagation() }}
                  >
                    <Dropdown
                      ariaLabel={`Quality Profile for ${reviewFolderName(item.path)}`}
                      value={item.qualityProfileId ?? ''}
                      disabled={profileBusy}
                      options={[{ value: '', label: defaultProfileLabel(profiles) }, ...profiles.map((profile) => ({ value: profile.id, label: profile.name }))]}
                      onChange={(value) => void setRowProfile(item, value || null)}
                    />
                  </span>
                </div>
                {state && <span className={`review-state ${state.tone}`}>{state.text}</span>}
              </div>

              <div
                className={`review-match-cell ${active ? 'active' : ''} ${included ? '' : 'excluded'}`}
                onClick={() => setSelectedId(active ? '' : item.id)}
              >
                <button type="button" className="review-match-main">
                  <span className="review-poster">{match?.posterRef
                    ? <img src={tmdbPosterUrl(match.posterRef)} alt="" loading="lazy" referrerPolicy="no-referrer" />
                    : <Icon name={item.mediaType === 'shows' ? 'tv-screen' : 'clapper'} size={16} />}</span>
                  <span className="review-match-copy">
                    <strong>{match?.title ?? item.parsedTitle ?? 'Nothing found'}</strong>

                    <span className="review-match-sub">
                      <small>{match?.year ?? item.parsedYear ?? '—'}</small>
                      {item.mediaType === 'movies' && <span
                        className={`review-edition-cell ${editionConflict ? 'clash' : item.parsedEdition || item.manualEditionOverride || sharedMovieIds.has(item.id) ? '' : 'optional'}`}
                        title={editionConflict ? (item.libraryEditionConflict ? 'Already in your library with this edition' : 'Another copy in this queue has the same movie and edition') : undefined}
                        onClick={(event) => { event.preventDefault(); event.stopPropagation() }}
                      >
                        <ReviewDiscoveryEdition
                          key={`${item.id}:${item.editionOverrideActive}:${item.manualEditionOverride ?? ''}`}
                          item={item}
                          options={editionOptions}
                          onChanged={(updated) => setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))}
                          onError={setError}
                        />
                      </span>}
                    </span>
                  </span>
                </button>
              </div>
            </div>
        })}
      </div>

      <div className="review-import">
        {/* Sits with the import action because it describes what is about to be
            imported, and it is one write for the whole visible selection. */}
        <span className="review-import-profile">
          <span className="review-import-profile-label">Quality Profile</span>
          {/* A value, not a command: it shows what the included folders are set
              to and changing it sets them. The old placeholder option was an
              action sitting in a list of values, and read as one more choice. */}
          <Dropdown
            ariaLabel="Quality Profile for every included folder"
            value={sharedProfile ?? MIXED_PROFILE}
            disabled={profileBusy || !rows.some((item) => !excluded.has(item.id))}
            options={[
              ...(sharedProfile === undefined ? [{ value: MIXED_PROFILE, label: 'Mixed', disabled: true }] : []),
              { value: '', label: defaultProfileLabel(profiles) },
              ...profiles.map((profile) => ({ value: profile.id, label: profile.name })),
            ]}
            onChange={(value) => void setAllProfiles(value || null)}
          />
        </span>
        <div className="toolbar-spacer" />
        {hasEditionConflicts && <span className="review-import-warning">Resolve same-edition copies or leave one out before importing.</span>}
        <Button variant="primary" icon="check" onClick={() => void runImport()} disabled={importing || !importable.length || hasEditionConflicts}>
          {importing ? 'Adding…' : `Add ${importable.length} ${importable.length === 1 ? capitalise(noun) : `${capitalise(noun)}s`}`}
        </Button>
      </div>

      {selected
        ? <ReviewAlternatives key={selected.id} item={selected} onChoose={(candidate) => void choose(selected, candidate)} onSearched={(updated) => setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))} onError={setError} />
        : <aside className="review-alt-pane review-alt-idle">
          <div className="review-col-head review-alt-title">Possible matches</div>
          <div className="review-alt-empty">
            <Icon name="search" size={28} />
            <p>Select a detected {noun} to see the alternatives.</p>
          </div>
        </aside>}
    </div>
  </Page>
}

function ReviewAlternatives({ item, onChoose, onSearched, onError }: {
  item: MediaDiscovery
  onChoose: (candidate: TMDBCandidate) => void
  onSearched: (item: MediaDiscovery) => void
  onError: (message: string) => void
}) {
  const [query, setQuery] = useState(item.parsedTitle ?? item.selected?.title ?? '')
  const [searching, setSearching] = useState(false)
  const matches = item.candidates

  const search = async (term?: string) => {
    setSearching(true)
    try { onSearched(await api.searchDiscoveryCandidates(item.id, term)) }
    catch (reason) { onError(reason instanceof Error ? reason.message : 'TMDB lookup failed.') }
    finally { setSearching(false) }
  }

  return <aside className="review-alt-pane">
    <div className="review-col-head review-alt-title">Possible matches</div>
    <div className="review-alt-head">
      <div className="search-field">
        <Icon name="search" size={14} />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') void search(query) }}
          disabled={searching}
          placeholder={`Search TMDB for a ${item.mediaType === 'shows' ? 'show' : 'movie'}…`}
        />
      </div>
    </div>
    <div className="review-alt-list">
      {searching && <p className="review-alt-note">Searching…</p>}
      {!searching && !matches.length && <p className="review-alt-note">No candidates. Search above.</p>}
      {matches.map((candidate) => {
        const chosen = item.selected?.tmdbId === candidate.tmdbId
        const yearMatches = item.parsedYear !== undefined && candidate.year === item.parsedYear
        return <button
          type="button"
          key={candidate.tmdbId}
          className={`review-candidate ${chosen ? 'chosen' : ''}`}
          disabled={chosen}
          onClick={() => onChoose(candidate)}
        >
          <span className="review-poster">{candidate.posterRef
            ? <img src={tmdbPosterUrl(candidate.posterRef)} alt="" loading="lazy" referrerPolicy="no-referrer" />
            : <Icon name={item.mediaType === 'shows' ? 'tv-screen' : 'clapper'} size={16} />}</span>
          <span className="review-candidate-copy">
            <strong>{candidate.title}</strong>
            <small>{candidate.year ?? 'Year unknown'}{yearMatches ? ' · matches folder' : ''}</small>
          </span>
          {chosen && <Icon name="check" size={14} />}
        </button>
      })}
    </div>
  </aside>
}

function initialDiscoveryEdition(item: MediaDiscovery, options: string[]): EditionDraft {
  const edition = item.manualEditionOverride ?? item.parsedEdition ?? THEATRICAL_EDITION
  if (edition === THEATRICAL_EDITION || options.includes(edition)) return { selection: edition, custom: '' }
  return { selection: CUSTOM_EDITION, custom: edition }
}

function ReviewDiscoveryEdition({ item, options, onChanged, onError }: { item: MediaDiscovery; options: string[]; onChanged: (item: MediaDiscovery) => void; onError: (message: string) => void }) {
  const [draft, setDraft] = useState<EditionDraft>(() => initialDiscoveryEdition(item, options))
  const [saving, setSaving] = useState(false)
  const persist = async (next: EditionDraft) => {
    const update = editionUpdate(item.id, next)
    if (!update.edition?.trim()) return
    setSaving(true); onError('')
    try { onChanged(await api.updateDiscoveryEdition(item.id, { mode: update.mode, edition: update.edition })) }
    catch (reason) { onError(reason instanceof Error ? reason.message : 'Could not save the edition.') }
    finally { setSaving(false) }
  }
  return <div className="review-edition-picker">
    <Dropdown
      ariaLabel="Edition"
      value={draft.selection}
      disabled={saving}
      options={[...editionChoices(options).map((edition) => ({ value: edition, label: edition })), { value: CUSTOM_EDITION, label: 'Other…' }]}
      onChange={(value) => {
        const next = { ...draft, selection: value }
        setDraft(next)
        if (next.selection !== CUSTOM_EDITION) void persist(next)
      }}
    />
    {draft.selection === CUSTOM_EDITION && <Input value={draft.custom} maxLength={128} disabled={saving} onChange={(event) => setDraft((current) => ({ ...current, custom: event.target.value }))} onBlur={() => {
      const original = initialDiscoveryEdition(item, options)
      if (draft.custom.trim() && JSON.stringify(editionUpdate(item.id, draft)) !== JSON.stringify(editionUpdate(item.id, original))) void persist(draft)
    }} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} placeholder="Custom edition" />}
  </div>
}

export function TorrentArchivePage() {
  const [items, setItems] = useState<TorrentArchiveItem[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [health, setHealth] = useState<{ status?: string; path?: string; writable?: boolean; archived?: number; tracked?: number; missing_or_failed?: number; message?: string }>({ status: 'unknown' })
  const [restoreItem, setRestoreItem] = useState<TorrentArchiveItem | null>(null)
  const [restoreClientId, setRestoreClientId] = useState('')
  const [restorePath, setRestorePath] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [archive, downloadClients, archiveHealth] = await Promise.all([api.torrentArchive(query), api.downloadClients(), api.torrentArchiveHealth()])
      setItems(archive); setClients(downloadClients); setHealth(archiveHealth); setError('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load the torrent archive.') }
    finally { setLoading(false) }
  }
  useEffect(() => { const timer = window.setTimeout(() => { void load() }, 150); return () => window.clearTimeout(timer) }, [query])

  const beginRestore = (item: TorrentArchiveItem) => {
    const eligible = clients.filter((client) => !item.mediaType || client.scope === item.mediaType)
    setRestoreItem(item)
    setRestoreClientId(eligible.length === 1 ? eligible[0].id : '')
    setRestorePath(item.previousReportedPath ?? item.previousResolvedPath ?? '')
    setMessage('')
  }
  const restore = async () => {
    if (!restoreItem || !restoreClientId || !restorePath.trim()) return
    setBusy(true); setMessage('')
    try {
      const result = await api.restoreTorrentArchive(restoreItem.id, { download_client_id: restoreClientId, save_path: restorePath.trim() })
      const [job] = await api.waitForJobs([result.job_id])
      if (!job || job.state !== 'completed') {
        setMessage(`Restore ${job?.state ?? 'failed'}. Review Tasks for details.`)
      } else {
        setMessage(`Submitted ${restoreItem.torrentName} to qBittorrent. It will be observed on the next poll.`)
      }
      setRestoreItem(null)
      await load()
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Restore failed.') }
    finally { setBusy(false) }
  }
  const retryArchive = async (item: TorrentArchiveItem) => {
    setBusy(true); setMessage('')
    try {
      const accepted = await api.retryTorrentArchive(item.id)
      const [job] = await api.waitForJobs([accepted.job_id])
      if (!job || job.state !== 'completed') setMessage(`Archive retry ${job?.state ?? 'failed'}. Review Tasks for details.`)
      else setMessage(`Recovery archive completed for ${item.torrentName}.`)
      await load()
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Archive retry failed.') }
    finally { setBusy(false) }
  }
  const eligibleClients = restoreItem ? clients.filter((client) => !restoreItem.mediaType || client.scope === restoreItem.mediaType) : []
  const archiveTone = health.status === 'healthy' ? 'green' : health.status === 'unavailable' ? 'red' : 'amber'
  const formatSize = (bytes?: number) => {
    if (!bytes) return '—'
    const units = ['B', 'KB', 'MB', 'GB', 'TB']; let value = bytes; let unit = 0
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
    return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
  }
  const shortHash = (hash: string) => hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-8)}` : hash

  return <Page title="Torrent Archive" subtitle="Kept separately from qBittorrent, so it survives a torrent being removed there." action={<Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button>}>
    <div className="archive-callout"><div className="archive-callout-icon"><Icon name="archive" size={22} /></div><div><strong>{health.status === 'healthy' ? 'Archive mount is healthy' : 'Archive mount needs attention'}</strong><span>{health.archived ?? items.filter((item) => item.archiveState === 'archived').length} archived · {health.missing_or_failed ?? items.filter((item) => item.archiveState !== 'archived').length} pending/failed · retention: indefinite · {health.path ?? '/torrent-archive'}</span></div><Badge tone={archiveTone}>{health.status === 'healthy' ? 'Protected' : health.status ?? 'Unknown'}</Badge></div>
    {message && <div className="settings-note"><Icon name="info" size={16} /><span>{message}</span></div>}
    {restoreItem && <Panel title="Restore archived torrent" eyebrow="EXPLICIT QBITTORRENT RESTORE"><div className="settings-form"><label><span className="field-label">Torrent</span><Input value={restoreItem.torrentName} readOnly /></label><label><span className="field-label">qBittorrent client</span><Select value={restoreClientId} onChange={(event) => setRestoreClientId(event.target.value)}><option value="">Select client…</option>{eligibleClients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</Select></label><label><span className="field-label">qBittorrent save path</span><Input value={restorePath} onChange={(event) => setRestorePath(event.target.value)} placeholder="Enter the destination path explicitly" /></label></div><div className="settings-note"><Icon name="info" size={15} /><span>The destination must resolve inside a configured {restoreItem.mediaType ?? 'media'} media folder. Medialogue does not move or import the downloaded data.</span></div><div className="settings-footer"><Button variant="ghost" onClick={() => setRestoreItem(null)}>Cancel</Button><Button variant="primary" icon="download" onClick={() => void restore()} disabled={busy || !restoreClientId || !restorePath.trim()}>{busy ? 'Submitting…' : 'Restore to qBittorrent'}</Button></div></Panel>}
    <Panel className="table-panel" title="Archived torrents" eyebrow="RECOVERY INVENTORY" action={<div className="search-field compact"><Icon name="search" size={14} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter archive…" /></div>}>
      {error ? <EmptyState title="Could not load torrent archive" detail={error} /> : !loading && !items.length ? <EmptyState icon="archive" title="No torrent recovery records yet" detail="Relevant qBittorrent torrents will be archived automatically when they are observed." /> : <table className="data-table"><thead><tr><th>Release</th><th>Media</th><th>Info hash</th><th>Source</th><th>Size</th><th>Archive</th><th>qBit</th><th /></tr></thead><tbody>{items.map((torrent) => <tr key={torrent.id}><td><strong>{torrent.releaseName ?? torrent.torrentName}</strong>{torrent.quality && <span className="table-sub">{torrent.quality}{torrent.edition ? ` · ${torrent.edition}` : ''}</span>}</td><td>{torrent.mediaTitle ?? 'Unassociated'}{torrent.tmdbId && <span className="table-sub">TMDB {torrent.tmdbId}</span>}</td><Tooltip asChild content={torrent.infoHash}><td className="mono">{shortHash(torrent.infoHash)}</td></Tooltip><td>{torrent.originalDownloadClient ?? 'Unknown client'}</td><td>{formatSize(torrent.totalSize)}</td><td><Badge tone={torrent.archiveState === 'archived' ? 'green' : torrent.archiveState === 'failed' ? 'red' : 'amber'}>{torrent.archiveState.replaceAll('_', ' ')}</Badge></td><td><Badge tone={torrent.qbitPresent ? 'green' : 'neutral'}>{torrent.qbitPresent ? 'Present' : 'Removed'}</Badge></td><td><div className="button-row">{torrent.archiveState === 'archived' ? <Button variant="ghost" icon="download" onClick={() => beginRestore(torrent)}>Restore</Button> : <Button variant="ghost" icon="refresh" onClick={() => void retryArchive(torrent)} disabled={busy}>Retry</Button>}</div></td></tr>)}</tbody></table>}
    </Panel>
  </Page>
}

export function CustomFormatsPage() { return <CustomFormatsPageView /> }

const DISCARD_CHANGES = { title: 'Discard unsaved changes?', confirmLabel: 'Discard' } as const

const settingsTabItems: Array<{ name: string; icon: Parameters<typeof Icon>[0]['name'] }> = [
  { name: 'Media', icon: 'folder' },
  { name: 'qBittorrent', icon: 'qbittorrent' },
  { name: 'TMDB', icon: 'tmdb' },
  { name: 'Plex', icon: 'plex' },
  { name: 'Indexers', icon: 'indexers' },
  { name: 'Security', icon: 'security' },
  { name: 'Backups', icon: 'backup' },
]
const settingsTabs = settingsTabItems.map((item) => item.name)
const legacySettingsTabs: Record<string, string> = {
  Metadata: 'TMDB',
  Schedules: 'qBittorrent',
  'Backup / Recovery': 'Backups',
  'Root Storage': 'Media',
  'Storage Roots': 'Media',
  'Media Folders': 'Media',
}

export function SettingsPage() {
  const [tab, setTab] = useUrlState('tab', 'Media')
  const normalizedTab = legacySettingsTabs[tab] ?? tab
  const current = settingsTabs.includes(normalizedTab) ? normalizedTab : 'Media'
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
          : current === 'Security' ? <SecuritySettings />
          : <BackupRecoverySettings />}
      </section>
    </div>
  </Page>
}

function SecuritySettings() {
  const [security, setSecurity] = useState<{ default_password_warning: boolean; session_expires_at?: string } | null>(null)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState<Message>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.security().then(setSecurity).catch((reason) => setMessage(failed(reason, 'Could not load security state.'))) }, [])
  const change = async () => {
    if (newPassword.length < 12) { setMessage({ tone: 'error', text: 'New password must contain at least 12 characters.' }); return }
    if (newPassword !== confirmPassword) { setMessage({ tone: 'error', text: 'New password confirmation does not match.' }); return }
    setBusy(true); setMessage(pending('Changing password…'))
    try { await api.changePassword(currentPassword, newPassword); setCurrentPassword(''); setNewPassword(''); setConfirmPassword(''); setSecurity((value) => value ? { ...value, default_password_warning: false } : value); setMessage(ok('Password changed. Other active sessions were revoked.')) }
    catch (reason) { setMessage(failed(reason, 'Could not change password.')) }
    finally { setBusy(false) }
  }
  const tooShort = newPassword.length > 0 && newPassword.length < 12
  const mismatch = confirmPassword.length > 0 && newPassword !== confirmPassword
  return <>
    <SectionHead title="Administrator security"
      description="Medialogue uses a single local administrator account and secure session cookies. There is no self-service recovery, a lost password is reset from the server."
      status={security?.default_password_warning ? 'Default password active' : 'Password changed'}
      statusTone={security?.default_password_warning ? 'amber' : 'green'}
      detail={security?.default_password_warning ? 'change this before exposing the host' : undefined}
      detailTone={security?.default_password_warning ? 'err' : undefined} />
    <div className="settings-form">
      <Field label="Current password" help="Required to prove this session belongs to you.">
        <Secret value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" />
      </Field>
      <div />
      <Field label="New password" error={tooShort ? 'Must contain at least 12 characters.' : undefined}
        help={<><strong>At least 12 characters.</strong> Length matters more than symbols, a passphrase of four uncommon words beats a short complex string.</>}>
        <Secret value={newPassword} onChange={(event) => setNewPassword(event.target.value)} aria-invalid={tooShort} />
      </Field>
      <Field label="Confirm new password" error={mismatch ? 'Does not match the new password.' : undefined} help="Must match exactly.">
        <Secret value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} aria-invalid={mismatch} />
      </Field>
    </div>
    <div className="settings-note"><Icon name="lock" size={15} /><span>Changing the password revokes every other active session immediately. You stay signed in on this device.</span></div>
    <Note message={message} />
    <div className="settings-footer">
      <Button variant="primary" onClick={() => void change()} disabled={busy || !currentPassword || tooShort || mismatch || !newPassword}>{busy ? 'Changing…' : 'Change password'}</Button>
  const confirm = useConfirm()
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
  const [rootEnabled, setRootEnabled] = useState(true)
  const [reviewNewMedia, setReviewNewMedia] = useState(false)
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
      const nextRoots = await api.storageRoots()
      if (generation === rootsLoadGeneration.current) setRoots(nextRoots)
    } catch (reason) {
      if (generation === rootsLoadGeneration.current) setMessage(failed(reason, 'Could not load media folder state.'))
    }
  }
  const load = async () => {
    const generation = ++rootsLoadGeneration.current
    try {
      const [nextRoots, nextMappings, nextClients] = await Promise.all([api.storageRoots(), api.remotePathMappings(), api.downloadClients()])
      if (generation === rootsLoadGeneration.current) setRoots(nextRoots)
      setMappings(nextMappings); setClients(nextClients)
      setMappingRootId((value) => value || nextRoots[0]?.id || '')
    } catch (reason) { setMessage(failed(reason, 'Could not load storage configuration.')) }
  }
  useEffect(() => { void load() }, [])
  useLiveLibraryRefresh(loadRoots, 15000, true)
  useEffect(() => {
    const generation = ++rootsLoadGeneration.current
    api.checkStorageRootHealth().then((nextRoots) => {
      if (generation === rootsLoadGeneration.current) setRoots(nextRoots)
    }).catch(() => undefined)
  }, [])
  useEffect(() => { api.librarySummary().then(setSummary).catch(() => undefined) }, [])
  useEffect(() => {
    api.tmdbConfiguration()
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
    setMessage(null); setSelectedRoot(null); setName(nextRootName('Movies', takenNames())); setPath(''); openBrowser(''); setMediaType('movies'); setAccessMode('read_only'); setRootEnabled(true); setReviewNewMedia(true); setMissingGraceChecks(2); setRootBaseline({ name: '', path: '', mediaType: 'movies', accessMode: 'read_only', enabled: true, reviewNewMedia: true, missingGraceChecks: 2 }); setRootModalOpen(true); setMessage(null)
  }
  const editRoot = (root: StorageRoot) => {
    setMessage(null); setSelectedRoot(root); setName(root.name); setPath(root.resolved_root_path); openBrowser(root.resolved_root_path, { updateDraftPath: false, preservePathOnProblem: true }); setMediaType(root.media_type); setAccessMode(root.access_mode); setRootEnabled(root.enabled); setReviewNewMedia(root.discovery_mode === 'review'); setMissingGraceChecks(root.missing_grace_checks ?? 2); setRootBaseline({ name: root.name, path: root.resolved_root_path, mediaType: root.media_type, accessMode: root.access_mode, enabled: root.enabled, reviewNewMedia: root.discovery_mode === 'review', missingGraceChecks: root.missing_grace_checks ?? 2 }); setRootModalOpen(true); setMessage(null)
  }
  const rootDirty = rootDraftChanged(
    { name, path, mediaType, accessMode, enabled: rootEnabled, reviewNewMedia, missingGraceChecks },
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
      const result = await api.browseFilesystem(target)
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
      const requestedDiscoveryMode = reviewNewMedia ? 'review' : 'automatic'
      const saved = selectedRoot
        ? await api.updateStorageRoot(selectedRoot.id, { name: name.trim(), path: path.trim(), media_type: mediaType, access_mode: accessMode, discovery_mode: requestedDiscoveryMode, enabled: rootEnabled, missing_grace_checks: missingGraceChecks })
        : await api.createStorageRoot({ name: name.trim(), path: path.trim(), media_type: mediaType, access_mode: accessMode, discovery_mode: requestedDiscoveryMode, enabled: rootEnabled, missing_grace_checks: missingGraceChecks })
      // A Vite page can outlive its backend process during development. Older
      // servers ignore unknown request fields, which previously closed the
      // modal as if Review had been persisted when it had not.
      if (saved.discovery_mode !== requestedDiscoveryMode) {
        throw new Error('The backend did not save the new-media setting. Restart the backend and try again.')
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
        if (!rootEnabled) {
          setMessage(ok(`${saved.name} added. It is off, so nothing will be scanned until you turn it on.`))
        } else if (!tmdbReady) {
          setMessage(ok(`${saved.name} added. Configure TMDB, then scan it.`))
        } else {
          try {
            await api.startScan(saved.id)
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
      await api.startScan(root.id)
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
        await api.deleteStorageRoot(root.id, removeEntries)
        if (removeEntries) window.dispatchEvent(new Event('medialogue-review-changed'))
        setRoots((items) => items.filter((item) => item.id !== root.id))
      setRemoving(null)
      setRootModalOpen(false)
      setSelectedRoot(null)
      setSummary(await api.librarySummary())
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
        ? await api.updateRemotePathMapping(selectedMapping.id, payload)
        : await api.createRemotePathMapping({ ...payload, integration_id: mappingClientId || undefined, storage_root_id: mappingRootId || undefined })
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
    try { await api.deleteRemotePathMapping(mapping.id); setMappings((items) => items.filter((item) => item.id !== mapping.id)); setAddingMapping(false); setSelectedMapping(null); setMessage(ok(`${mapping.name} removed.`)) }
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
    try { setRoots(await api.reorderStorageRoots(ids)) }
    // The list already shows the new order, so a failed save must put the
    // stored order back rather than leave the two disagreeing.
    catch { setRoots(await api.storageRoots().catch(() => roots)) }
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
      titleAction={<EnabledButton enabled={rootEnabled} onChange={setRootEnabled} />}
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

        <div className={`settings-form form-cards scope-${mediaType}`}>
          <Field label="Media type" help="Which library the files found here belong to.">
            <Segmented
              ariaLabel="Media type"
              value={mediaType}
              onChange={changeMediaType}
              options={[{ value: 'movies', label: 'Movies', tone: 'movie' }, { value: 'shows', label: 'Shows', tone: 'show' }]}
            />
          </Field>
          <Field label="Name" help="Shown in Medialogue only.">
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Access" help="Read-only is recommended. Read/write allows deletion.">
            <Segmented
              ariaLabel="Access"
              value={accessMode}
              onChange={setAccessMode}
              options={[{ value: 'read_only', label: 'Read-only' }, { value: 'read_write', label: 'Read/write' }]}
            />
          </Field>

          <Field label="New media" help={reviewNewMedia
            ? 'Newly detected titles wait in Review until you approve them.'
            : 'Newly detected titles are added to your library as soon as a scan finds them.'}>
            <Segmented
              ariaLabel="New media"
              value={reviewNewMedia ? 'review' : 'auto'}
              onChange={(mode) => setReviewNewMedia(mode === 'review')}
              options={[{ value: 'review', label: 'Review before adding' }, { value: 'auto', label: 'Add automatically' }]}
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

    <SectionHead title="Remote path mappings" divided
      description="Only needed when qBittorrent reports a path that does not exist from Medialogue's point of view, typically a different container, or a different host entirely."
      action={<Button variant="primary" icon="plus" onClick={beginAddMapping}>Add mapping</Button>} />
    <div className="provider-card-grid remote-mapping-grid">{mappings.map((mapping) => {
      const client = clients.find((item) => item.id === mapping.integration_id)
      const root = roots.find((item) => item.id === mapping.storage_root_id)
      return <button type="button" className={`provider-card ${mapping.enabled ? '' : 'provider-card-disabled'}`} key={mapping.id} onClick={() => editMapping(mapping)}>
        <div className="provider-card-head"><div className="provider-card-icon"><Icon name="arrow" size={20} /></div><Badge tone={mapping.enabled ? 'green' : 'neutral'}>{mapping.enabled ? 'On' : 'Off'}</Badge></div>
        <strong>{mapping.name}</strong><span className="remote-mapping-route"><b>{mapping.remote_prefix}</b><Icon name="arrow" size={13} /><b>{mapping.local_prefix}</b></span>
        <div className="provider-card-meta"><span>{client?.name ?? 'All qBittorrent clients'}</span><span>{root?.name ?? 'Any matching media folder'}</span></div>
      </button>
    })}
      <button type="button" className="provider-card provider-add-card" onClick={beginAddMapping}><Icon name="plus" size={28} /><strong>Add remote path mapping</strong></button>
    </div>
    {!mappings.length && <p className="provider-empty-copy">No mappings configured. Only add one when qBittorrent reports a path that differs from the path visible inside Medialogue.</p>}

    {addingMapping && <Modal wide title={selectedMapping ? selectedMapping.name : 'Add remote path mapping'} eyebrow={selectedMapping ? 'EDIT REMOTE PATH MAPPING' : 'NEW REMOTE PATH MAPPING'} onClose={closeMapping} footer={<>
      <EnabledButton enabled={mappingEnabled} onChange={(next) => updateMappingDraft(() => setMappingEnabled(next))} />
      {selectedMapping && <Button variant="danger" onClick={() => void removeMapping(selectedMapping)} disabled={mappingBusy}>Delete</Button>}
      {(mappingDirty || !selectedMapping) && <span className="footer-state">{mappingDirty ? 'Unsaved changes' : 'Ready to add'}</span>}
      <Button variant="ghost" onClick={closeMapping} disabled={mappingBusy}>Cancel</Button>
      <Button variant="primary" onClick={() => void saveMapping()} disabled={mappingBusy || Boolean(selectedMapping && !mappingDirty)}>{mappingBusy ? 'Saving…' : selectedMapping ? 'Save mapping' : 'Add mapping'}</Button>
    </>}>
      <div className="provider-modal-editor">
        <SectionHead title={selectedMapping ? selectedMapping.name : 'Map a reported path'} description="Translate the path reported by qBittorrent into the equivalent path visible to Medialogue." status={selectedMapping ? mappingEnabled ? 'On' : 'Off' : 'New mapping'} statusTone={mappingEnabled ? 'green' : 'neutral'} />
        <div className="settings-form">
          <Field label="Name" help="Shown in Medialogue only.">
            <Input value={mappingName} onChange={(event) => updateMappingDraft(() => setMappingName(event.target.value))} />
          </Field>
          <Field label="qBittorrent client" help="Which client's reported paths this mapping rewrites. Leave as all clients when every instance uses the same layout.">
            <Select value={mappingClientId} onChange={(event) => updateMappingDraft(() => setMappingClientId(event.target.value))}><option value="">All qBittorrent clients</option>{clients.map((client) => <option value={client.id} key={client.id}>{client.name}</option>)}</Select>
          </Field>
          <Field label="Remote prefix" help="The path prefix exactly as qBittorrent reports it. Copy it from a download's save path.">
            <Input value={remotePrefix} onChange={(event) => updateMappingDraft(() => setRemotePrefix(event.target.value))} placeholder="/downloads/movies" />
          </Field>
          <Field label="Medialogue prefix" help="The equivalent path from Medialogue's point of view.">
            <Input value={localPrefix} onChange={(event) => updateMappingDraft(() => setLocalPrefix(event.target.value))} placeholder="/media/movies" />
          </Field>
          <Field label="Media folder" badge="optional" wide help="Limit the rewritten path to one media folder, or leave unset to try them all.">
            <Select value={mappingRootId} onChange={(event) => updateMappingDraft(() => setMappingRootId(event.target.value))}><option value="">No specific media folder</option>{roots.map((root) => <option value={root.id} key={root.id}>{root.name} · {root.resolved_root_path}</option>)}</Select>
          </Field>
        </div>
        <Note message={message} />
      </div>
    </Modal>}

    {!rootModalOpen && !addingMapping && !removing && <Note message={message} />}
    <div className="settings-note"><Icon name="info" size={15} /><span>Scans and path mappings never move or rename media. Removing a media folder never touches its files; only Medialogue's index of that folder is cleared.</span></div>

  </>
}

function MetadataSettings() {
  const [configuration, setConfiguration] = useState<{ configured: boolean; api_key_configured: boolean; enabled: boolean; health: string; latency_ms?: number; last_error?: string; revision?: number } | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<Message>(null)

  const load = () => api.tmdbConfiguration().then((value) => { setConfiguration(value); setEnabled(value.enabled); setApiKey(''); setDirty(false) })
  useEffect(() => { load().catch((reason) => setMessage(failed(reason, 'Could not load TMDB settings.'))) }, [])

  const save = async () => {
    setSaving(true); setMessage(pending('Saving TMDB settings…'))
    try {
      const value = await api.saveTmdb({ api_key: apiKey || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setApiKey(''); setDirty(false); setMessage(ok('TMDB settings saved.'))
    } catch (reason) { setMessage(failed(reason, 'Could not save TMDB settings.')) } finally { setSaving(false) }
  }

  const healthTone: StatusTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : 'neutral'
  return <>
    <SectionHead title="TMDB"
      description="Identity source for newly discovered titles. When off, discoveries stay unmatched."
      status={!configuration?.configured ? 'Not configured' : configuration.health} statusTone={healthTone}
      action={<EnabledButton enabled={enabled} onChange={(next) => { setEnabled(next); setDirty(true) }} />} />
    <div className="settings-form form-cards">
      <Field label="API key"
        help={<>Your TMDB API Read Access Token. Held server-side. {configuration?.api_key_configured ? 'Leave blank to keep the current one.' : 'Required for automatic matching.'}</>}>
        <Secret value={apiKey} onChange={(event) => { setApiKey(event.target.value); setDirty(true) }} placeholder={configuration?.api_key_configured ? 'Stored server-side · leave blank to preserve' : 'Required for automatic matching'} />
      </Field>
    </div>
    {configuration?.last_error && <Note message={{ tone: 'error', text: configuration.last_error }} />}
    <Note message={message} />
    <SaveFooter dirty={dirty} saving={saving} onSave={() => void save()} />
  </>
}
function PlexSettings() {
  const [configuration, setConfiguration] = useState<import('./types').PlexConfiguration | null>(null)
  const [url, setUrl] = useState('http://plex:32400')
  const [token, setToken] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [message, setMessage] = useState<Message>(null)
  const [working, setWorking] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = () => api.plexConfiguration().then((value) => {
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
      const value = await api.savePlex({ url: url.trim(), token: token || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setToken(''); setDirty(false); setMessage(ok('Plex settings saved.'))
      window.dispatchEvent(new Event('medialogue-plex-configuration-changed'))
    } catch (reason) { setMessage(failed(reason, 'Could not save Plex settings.')) }
    finally { setWorking(false) }
  }
  const syncLibrary = async () => {
    setWorking(true); setMessage(pending('Starting Plex library verification…'))
    try {
      const result = await api.syncPlexLibrary()
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
    <div className="settings-form form-cards">
      <Field label="URL" help={<>Plex Media Server address, including port. Example: <code>http://plex:32400</code></>}>
        <Input placeholder="http://plex:32400" value={url} onChange={(event) => { setUrl(event.target.value); setDirty(true) }} />
      </Field>
      <Field label="API token"
        help={<>Your <code>X-Plex-Token</code>. Held server-side. {configuration?.token_configured ? 'Leave blank to keep the current one.' : 'Required.'}</>}>
        <Secret placeholder={configuration?.token_configured ? 'Stored server-side · leave blank to preserve' : 'Required'} value={token} onChange={(event) => { setToken(event.target.value); setDirty(true) }} />
      </Field>
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
      const items = await api.downloadClients()
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
        ? await api.updateDownloadClient(selectedId, { ...payload(), expected_revision: clients.find((item) => item.id === selectedId)?.revision })
        : await api.createDownloadClient({ ...payload(), password: draft.password })
      setClients((items) => selectedId ? items.map((item) => item.id === value.id ? value : item) : [...items, value])
      setSelectedId(value.id); setDraft(draftFromDownloadClient(value)); setDirty(false); setEditorOpen(false); setMessage(`${value.name} saved. Password is stored server-side and never returned to the browser.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save qBittorrent client.'); setMessage('') }
    finally { setBusy(false) }
  }
  const refresh = async () => {
    if (!selectedId) return
    setBusy(true); setError(''); setMessage('Refreshing qBittorrent health…')
    try {
      const result = await api.refreshDownloadClient(selectedId)
      setClients((items) => items.map((item) => item.id === selectedId ? { ...item, health: result.status, latency_ms: result.latency_ms, last_error: result.message, last_checked_at: new Date().toISOString() } : item))
      setMessage(result.status === 'healthy' ? 'Client health is healthy.' : result.message ?? `qBittorrent status: ${result.status}.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not refresh qBittorrent health.'); setMessage('') }
    finally { setBusy(false) }
  }
  const refreshAll = async () => {
    if (!clients.length) return
    setBusy(true); setError(''); setMessage('Refreshing health for every qBittorrent client…')
    const results = await Promise.allSettled(clients.map((client) => api.refreshDownloadClient(client.id)))
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
      await api.deleteDownloadClient(selectedId)
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
      await api.updateDownloadClient(client.id, downloadClientPayload(next))
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
    <SectionHead title="Download clients" description="qBittorrent instances Medialogue observes and uses for explicit grabs." action={<div className="button-row"><Button variant="secondary" icon="refresh" onClick={() => void refreshAll()} disabled={busy || !clients.length}>{busy ? 'Refreshing…' : 'Refresh health'}</Button><Button variant="primary" icon="plus" onClick={beginAdd}>Add client</Button></div>} />
    {error && !editorOpen && <Note message={{ tone: 'error', text: error }} />}
    {message && !editorOpen && <Note message={{ tone: 'ok', text: message }} />}
    {clients.length > 0 && <div className="provider-card-grid">
      {clients.map((client) => <article className={`provider-card ${client.enabled ? '' : 'provider-card-disabled'}`} role="button" tabIndex={0} key={client.id} onClick={() => selectClient(client.id)} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectClient(client.id) } }}>
        <div className="provider-card-head"><div className={`provider-card-icon ${client.scope === 'movies' ? 'kind-movie' : 'kind-show'}`}><Icon name={client.scope === 'movies' ? 'clapper' : 'tv-screen'} size={20} /></div><button type="button" className={`badge cf-card-toggle ${client.enabled ? 'badge-green' : ''}`} aria-pressed={client.enabled} aria-label={`Turn ${client.name} ${client.enabled ? 'off' : 'on'}`} disabled={togglingId === client.id} onClick={(event) => { event.stopPropagation(); void toggleClient(client) }}><span className="badge-dot" />{togglingId === client.id ? 'Saving…' : client.enabled ? 'On' : 'Off'}</button></div>
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
      {dirty && <span className="footer-state">Unsaved changes</span>}
      <Button variant="ghost" onClick={closeEditor} disabled={busy}>Cancel</Button>
      <Button variant="primary" onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save client'}</Button>
    </>}>
      <div className="qbit-editor provider-modal-editor">
        <div className={`settings-form form-rows scope-${draft.scope}`}>
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
          <Field label="Polling interval" help="How often progress is polled. Does not affect download speed.">
            <Select value={String(draft.pollIntervalSeconds)} onChange={(event) => updateDraft('pollIntervalSeconds', Number(event.target.value))}><option value="5">5 seconds</option><option value="10">10 seconds</option><option value="15">15 seconds</option><option value="30">30 seconds</option><option value="60">1 minute</option><option value="300">5 minutes</option></Select>
          </Field>
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

function indexerBadgePreview(color: string): CSSProperties | undefined {
  const badge = badgeColor(color)
  if (!badge) return undefined
  return { color: badge.text, borderColor: badge.border, background: badge.background }
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
      const [items, clientRows] = await Promise.all([api.indexers(), api.downloadClients()])
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
  const updateDraft = <K extends keyof IndexerDraft>(key: K, value: IndexerDraft[K]) => { setDirty(true); setDraft((current) => ({ ...current, [key]: value })) }

  const save = async () => {
    if (!draft.name.trim() || !draft.torznabUrl.trim()) { setError('Name and Torznab URL are required.'); return }
    if (!selectedId && !draft.apiKey.trim()) { setError('API key is required for a new indexer.'); return }
    if (preset === 'jackett' && isJackettAllUrl(draft.torznabUrl)) { setError('Add Jackett indexers individually. The aggregate “all” endpoint is not supported.'); return }
    // Grabs need somewhere to go. Not enforced when nothing is configured to
    // choose from, or a fresh install could never add its first indexer.
    if (clients.length && !draft.downloadClientIds.length) { setError('Choose at least one download client for this indexer.'); return }
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
        ? await api.updateIndexer(selectedId, { ...base, ...(draft.apiKey.trim() ? { api_key: draft.apiKey.trim() } : {}), expected_revision: selected?.revision })
        : await api.createIndexer({ ...base, color: draft.color || undefined, api_key: draft.apiKey.trim() })
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
        ? await api.testIndexer(selectedId)
        : await api.testIndexer(undefined, { torznab_url: draft.torznabUrl.trim(), api_key: draft.apiKey.trim(), timeout_seconds: draft.timeoutSeconds })
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
    const results = await Promise.allSettled(indexers.map((indexer) => api.testIndexer(indexer.id)))
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
      await api.deleteIndexer(selectedId)
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
      await api.updateIndexer(indexer.id, { enabled: !indexer.enabled })
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
        <div className="provider-card-head"><div className={`provider-card-icon indexer-source-icon indexer-source-${source}`} style={indexerBadgePreview(indexer.color ?? '')}><img src={indexerPresets[source].icon} alt={`${indexerPresets[source].name} icon`} /></div><button type="button" className={`badge cf-card-toggle ${indexer.enabled ? 'badge-green' : ''}`} aria-pressed={indexer.enabled} aria-label={`Turn ${indexer.name} ${indexer.enabled ? 'off' : 'on'}`} disabled={togglingId === indexer.id} onClick={(event) => { event.stopPropagation(); void toggleIndexer(indexer) }}><span className="badge-dot" />{togglingId === indexer.id ? 'Saving…' : indexer.enabled ? 'On' : 'Off'}</button></div>
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
      {dirty && <span className="footer-state">Unsaved changes</span>}
      <Button variant="ghost" onClick={closeEditor} disabled={busy}>Cancel</Button>
      <Button variant="primary" onClick={save} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save indexer'}</Button>
    </>}>
      <div className="qbit-editor provider-modal-editor">
        <div className={`settings-form form-rows ${draft.scope === 'movies' ? 'scope-movies' : draft.scope === 'shows' ? 'scope-shows' : ''}`}>
          <Field label="Display name" help="Shown in Medialogue and on every search result.">
            <Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} placeholder="TorrentLeech" />
          </Field>
          <Field label="Scope" help="Which searches query this indexer.">
            <Segmented
              ariaLabel="Scope"
              value={draft.scope}
              onChange={(next) => updateDraft('scope', next as IndexerScope)}
              options={[{ value: 'both', label: 'Both' }, { value: 'movies', label: 'Movies', tone: 'movie' }, { value: 'shows', label: 'Shows', tone: 'show' }]}
            />
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
          <Field label="Enable RSS" help="Participate in periodic RSS release discovery.">
            <Segmented ariaLabel="Enable RSS" value={draft.enableRss ? 'on' : 'off'} onChange={(next) => updateDraft('enableRss', next === 'on')} options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'On' }]} />
          </Field>
          <Field label="Interactive search" help="Answer user-triggered movie, season, and episode searches.">
            <Segmented ariaLabel="Interactive search" value={draft.enableInteractiveSearch ? 'on' : 'off'} onChange={(next) => updateDraft('enableInteractiveSearch', next === 'on')} options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'On' }]} />
          </Field>
          <Field label="Categories" help="Category IDs sent with searches. Blank lets the indexer decide.">
            <Input value={draft.categories} onChange={(event) => updateDraft('categories', event.target.value)} placeholder="2000, 5000" />
          </Field>
          <Field label="Minimum seeders" help="Results with fewer seeders are rejected. Unknown counts stay.">
            <Input type="number" min="0" value={String(draft.minimumSeeders)} onChange={(event) => updateDraft('minimumSeeders', Math.max(0, Number(event.target.value) || 0))} />
          </Field>
          <Field label="Indexer priority" help="Tie-breaker from 1 (highest) to 50 (lowest). All are still searched.">
            <Input type="number" min="1" max="50" value={String(draft.priority)} onChange={(event) => updateDraft('priority', Math.min(50, Math.max(1, Number(event.target.value) || 25)))} />
          </Field>
          <Field label="MULTI languages" help="What a MULTI-tagged release here actually contains. Comma-separated.">
            <Input value={draft.multiLanguages} onChange={(event) => updateDraft('multiLanguages', event.target.value)} placeholder="English, French" />
          </Field>
          <Field label="Download clients" help="Which download clients receive grabs from this indexer. At least one is required.">
            {clients.length ? <div className="client-choice">
              {/* Checkboxes, not badges: these were styled as the same read-only
                  status pills used elsewhere, so nothing said they were
                  selectable, and clients named after media types read as a
                  second, broken copy of the Scope control above. */}
              {clients.map((client) => {
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
            </div> : <span className="field-help">No download clients are configured yet.</span>}
          </Field>
          <Field label="Badge colour" help="Tints this indexer's badge in search results.">
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
        </div>

        {selected?.lastError && <Note message={{ tone: 'error', text: `Last connection error: ${selected.lastError}` }} />}
        {error && <Note message={{ tone: 'error', text: error }} />}
        {message && <Note message={{ tone: 'ok', text: message }} />}

      </div>
    </Modal>}
  </div>
}

function BackupRecoverySettings() {
  const [capabilities, setCapabilities] = useState<RecoveryCapabilities | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [message, setMessage] = useState<Message>(null)
  const [busy, setBusy] = useState(false)

  const loadCapabilities = async () => {
    try { setCapabilities(await api.recoveryCapabilities()); setMessage(null) }
    catch (reason) { setMessage(failed(reason, 'Could not inspect Recovery Bundle capabilities.')) }
  }
  useEffect(() => { void loadCapabilities() }, [])
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.state)) return
    let alive = true
    const refresh = async () => {
      try { const value = await api.job(job.id); if (alive) setJob(value) } catch {}
    }
    const timer = window.setInterval(() => void refresh(), 1500)
    return () => { alive = false; window.clearInterval(timer) }
  }, [job?.id, job?.state])

  const start = async () => {
    setBusy(true); setMessage(null)
    try {
      const accepted = await api.startRecoveryExport()
      const value = await api.job(accepted.job_id)
      setJob(value)
      setMessage(accepted.warning ? { tone: 'error', text: accepted.warning } : null)
    } catch (reason) { setMessage(failed(reason, 'Could not start Recovery Bundle export.')) }
    finally { setBusy(false) }
  }
  const formatSize = (value: unknown) => {
    const bytes = typeof value === 'number' ? value : Number(value)
    if (!Number.isFinite(bytes) || bytes <= 0) return undefined
    const units = ['B', 'KB', 'MB', 'GB', 'TB']; let amount = bytes; let unit = 0
    while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
    return `${amount >= 100 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
  }
  const summary = job?.summary ?? {}
  const bundleSize = formatSize(summary.bundle_size)
  const sha = typeof summary.bundle_sha256 === 'string' ? summary.bundle_sha256 : undefined
  const expiresAt = typeof summary.expires_at === 'string' ? new Date(summary.expires_at).toLocaleString() : undefined
  const stateTone = job?.state === 'completed' ? 'green' : job?.state === 'failed' ? 'red' : job ? 'amber' : 'neutral'

  return <div className="recovery-settings">
    <SectionHead title="Recovery Bundle"
      description="Exports the database, torrent recovery data, settings, and a readable library list as one ZIP."
      status={capabilities?.supported ? 'Ready' : capabilities ? 'Unavailable' : 'Checking'}
      statusTone={capabilities?.supported ? 'green' : capabilities ? 'red' : 'neutral'} />

    <div className="settings-section">
      <div><h3>What the bundle contains</h3><p>Enough to rebuild this installation on a new host. Your media is <strong>not</strong> included, the bundle describes your library, it does not copy it. Intended for disaster recovery and manual verification, not automatic mass redownload.</p></div>
      <div className="recovery-content-list"><span>database/medialogue.db</span><span>torrent-archive/</span><span>manifests/</span><span>config/application-config-export.json</span><span>inventory/library-inventory.json</span><span>inventory/torrent-archive-inventory.json</span><span>backup-metadata.json</span></div>
    </div>

    <Note message={{ tone: 'error', text: 'The bundle contains a complete SQLite database backup and integration credentials. Treat anyone holding the ZIP as having full access to your Medialogue configuration and database.' }} />

    {capabilities && <div className="settings-section">
      <div><h3>Backup compatibility</h3><p>Medialogue uses SQLite's online backup API to create a consistent database file while the application is running.</p></div>
      <div className="settings-form">
        <Field label="SQLite version" help="SQLite library version used to create the online backup."><Input readOnly value={capabilities.sqliteVersion ?? 'Unknown'} /></Field>
        <Field label="Database file" help="Application-owned database included in the Recovery Bundle."><Input readOnly value={capabilities.databasePath ?? 'Unavailable'} /></Field>
        <Field label="Migration revision" help="Schema revision captured in the bundle. Restore onto this revision or later."><Input readOnly value={capabilities.migrationRevision ?? 'Unknown'} /></Field>
        <Field label="Temporary download retention" help="How long a finished bundle stays downloadable before it is deleted from the server."><Input readOnly value={`${capabilities.retentionHours} hours`} /></Field>
      </div>
    </div>}
    {capabilities?.reasons.length ? <Note message={{ tone: 'error', text: capabilities.reasons.join(' ') }} /> : null}

    {job && <div className="settings-section">
      <div><h3>Latest export</h3><p>{job.error || job.detail || 'Recovery export runs as a background job and is recorded in Events.'}</p></div>
      <div className="recovery-job">
        <div className="button-row"><Badge tone={stateTone}>{job.state}</Badge>{job.stage && <Badge tone="neutral">{job.stage.replaceAll('_', ' ')}</Badge>}</div>
        {job.progress !== undefined && <div className="job-progress-line"><Progress value={job.progress} tone={job.state === 'completed' ? 'green' : 'blue'} /><span>{job.progress}%</span></div>}
        {bundleSize && <span className="muted">Bundle size: {bundleSize}</span>}
        {expiresAt && <span className="muted">Temporary download available until {expiresAt}</span>}
        {sha && <code className="recovery-hash">SHA-256: {sha}</code>}
        {job.state === 'completed' && <Button variant="primary" icon="download" onClick={() => { window.location.href = api.recoveryDownloadUrl(job.id) }}>Download Recovery Bundle</Button>}
      </div>
    </div>}
    <Note message={message} />
    <div className="settings-footer"><Button variant="ghost" icon="refresh" onClick={() => void loadCapabilities()}>Recheck</Button><Button variant="primary" icon="download" disabled={busy || !capabilities?.supported || job?.state === 'running' || job?.state === 'queued'} onClick={() => void start()}>{busy ? 'Starting…' : 'Export Recovery Bundle'}</Button></div>
  </div>
}

// Back returns to wherever you actually came from, a Problem, a search result,
// a download row, and only falls back to the hardcoded list route when this
// page was opened cold (deep link, refresh, new tab) and there is no history to
// return to. The label follows suit so it never promises the wrong destination.
function Page({ title, subtitle, action, back, backTo = '/movies', children }: { title: string; subtitle: string; action?: React.ReactNode; back?: string; backTo?: string; children: React.ReactNode }) {
  const navigate = useNavigate()
  const cameFromApp = typeof window !== 'undefined' && window.history.state?.idx > 0
  const goBack = () => { if (cameFromApp) navigate(-1); else navigate(backTo) }
  return <div className="page"><PageTopbar title={title} subtitle={subtitle} action={action} back={back ? (cameFromApp ? 'Back' : back) : undefined} onBack={goBack} />{children}</div> }
