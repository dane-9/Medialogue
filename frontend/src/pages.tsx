import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Field, Note, SaveFooter, SectionHead, Secret, failed, ok, pending } from './components/settings'
import type { Message, StatusTone } from './components/settings'
import { Icon } from './components/Icon'
import { PageTopbar } from './components/Shell'
import { Badge, Button, EmptyState, Input, Panel, Progress, Select, Stat } from './components/ui'
import { ApiError, api } from './api/client'
import CustomFormatsPageView from './CustomFormatsPage'
import { contextMenuSelection, duplicateLoserIds, normalizeMediaView, problemMatchesFilter, toggleIdSelection } from './lib/uiState'
import { updateUrlParams, useUrlNumber, useUrlState } from './lib/urlState'
import type { EpisodeOrdering, CustomFormat, Download, DownloadClient, IncomingDownload, Indexer, IndexerScope, MediaProfileSettings, Movie, MovieRelease, Problem, QualityDefinition, QualityProfile, ReconciliationEvidence, Show, Season, Episode, EpisodeMedia, TMDBShowLookup, TMDBMovieLookup, DuplicateResolvePreview, StorageRoot, RemotePathMapping, TorrentArchiveItem, EventHistoryItem, Job, RecoveryCapabilities, Tag } from './types'



const statusTone = (status: string) => status === 'Present' || status === 'Verified' || status === 'Completed' || status === 'Archived' ? 'green' : status === 'Missing' || status === 'Pending' || status === 'Multiple versions' || status === 'Downloading' || status === 'Active' ? 'amber' : status === 'Conflict' || status === 'Duplicate' || status === 'Error' ? 'red' : 'neutral'

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

function useLiveLibraryRefresh(refresh: () => void | Promise<void>, pollIntervalMs = 15000) {
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
    const poll = window.setInterval(run, pollIntervalMs)
    const onVisibility = () => { if (document.visibilityState === 'visible') invalidate() }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      stopped = true
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
      window.clearInterval(poll)
      document.removeEventListener('visibilitychange', onVisibility)
      stream.close()
    }
  }, [pollIntervalMs])
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
  const [filter, setFilter] = useState('All movies')
  const [query, setQuery] = useState('')
  const [tagFilter, setTagFilter] = useState(searchParams.get('tag') ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [context, setContext] = useState<{ x: number; y: number } | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)

  const loadMovies = async () => {
    try {
      const items = await api.movies(query, tagFilter)
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
      api.movies(query, tagFilter).then((items) => {
        if (!alive) return
        setMovies(items)
        setSelected((current) => new Set([...current].filter((id) => items.some((movie) => movie.id === id))))
        setError('')
      }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load movies.') }).finally(() => { if (alive) setLoading(false) })
    }, 150)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [query, tagFilter])

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
        setMessage(`${label} queued as job ${result.job_id.slice(0, 8)}…. You can follow it in Jobs.`)
        const [job] = await api.waitForJobs([result.job_id])
        const summary = job?.summary ?? {}
        const requested = Number(summary.requested ?? ids.length)
        const updated = Number(summary.updated ?? 0)
        if (job?.state === 'completed') setMessage(`${label}: ${updated} of ${requested} selected movie${requested === 1 ? '' : 's'} processed. Check Jobs for any per-title failures.`)
        else if (job?.state === 'cancelled') setMessage(`${label} cancelled after processing ${updated} of ${requested} selected movies.`)
        else setMessage(`${label} failed${job?.error ? `: ${job.error}` : '.'} Check Jobs for details.`)
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

  const filtered = useMemo(() => movies.filter((movie) => (filter === 'All movies' || movie.status === filter) && movie.title.toLowerCase().includes(query.toLowerCase())), [movies, filter, query])
  const missing = movies.filter((movie) => movie.status === 'Missing').length
  const review = movies.filter((movie) => movie.status === 'Conflict' || movie.status === 'Duplicate').length
  return <Page title="Movies" subtitle="Your library, exactly where it was downloaded.">
    <div className="stats-row stats-row-three"><Stat label="Movies" value={String(movies.length)} tone="blue" /><Stat label="Missing" value={String(missing)} tone="amber" /><Stat label="Needs review" value={String(review)} tone="red" /></div>
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search movies…" /></div><Select value={filter} onChange={(event) => setFilter(event.target.value)}><option>All movies</option><option>Present</option><option>Missing</option><option>Conflict</option><option>Duplicate</option></Select><Select value={tagFilter} onChange={(event) => updateTagFilter(event.target.value)}><option value="">All tags</option>{tags.map((tag) => <option value={tag.name} key={tag.id}>{tag.name}</option>)}</Select>{selected.size > 0 && <span className="selection-count">{selected.size} selected · right-click for actions</span>}<div className="toolbar-spacer" /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => changeMovieView('cards')}><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => changeMovieView('table')}><Icon name="list" size={16} /></button></div><Button variant="ghost" icon="refresh" onClick={() => void loadMovies()}>Refresh</Button></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    {error && <EmptyState title="Could not load the movie library" detail={error} />}
    {!error && !loading && (view === 'cards' ? <div className="media-grid">{filtered.map((movie) => <MovieCard key={movie.id} movie={movie} selected={selected.has(movie.id)} onToggle={toggleSelected} onContext={openContext} />)}</div> : <MovieTable items={filtered} selected={selected} onToggle={toggleSelected} onContext={openContext} onTagClick={updateTagFilter} />)}
    {!error && !loading && !filtered.length && <EmptyState title="No movies discovered yet" detail={tagFilter ? `No movies match the tag “${tagFilter}”.` : 'Configure a Movie storage root, then press Scan once to initialize it.'} />}
    {context && <><div className="context-dismiss-layer" onClick={() => setContext(null)} onContextMenu={(event) => { event.preventDefault(); setContext(null) }} /><MovieBulkContextMenu position={context} count={selected.size} profiles={profiles} tags={tags} busy={bulkBusy} onRun={runBulk} onCreateTag={createTag} onClear={() => { setSelected(new Set()); setContext(null) }} /></>}
  </Page>
}

function MovieTagChips({ movie, onTagClick }: { movie: Movie; onTagClick?: (tag: string) => void }) {
  if (!movie.tags?.length) return null
  return <div className="tag-chip-row">{movie.tags.map((tag) => <span key={tag.id} className="tag-chip" role={onTagClick ? 'button' : undefined} tabIndex={onTagClick ? 0 : undefined} onClick={(event) => { if (!onTagClick) return; event.preventDefault(); event.stopPropagation(); onTagClick(tag.name) }} onKeyDown={(event) => { if (onTagClick && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onTagClick(tag.name) } }}>{tag.name}</span>)}</div>
}

// The quality mark on the artwork is the card's only statement of what the
// file actually is, so it reads resolution first, then the source or the
// modifier that distinguishes it: "2160p REMUX", "1080p WEB-DL".
const sourceLabels: Record<string, string> = { WEBDL: 'WEB-DL', WEBRIP: 'WEBRIP', BLURAY: 'BLURAY', HDTV: 'HDTV', DVD: 'DVD', SDTV: 'SDTV' }

export function qualityMark(quality?: string, edition?: string): string {
  if (!quality) return ''
  const parts = quality.split('-').map((part) => part.trim()).filter(Boolean)
  const resolution = parts.find((part) => /^\d{3,4}[pi]$/i.test(part))
  const source = parts.find((part) => part !== resolution)
  // An edition such as Remux or Hybrid describes the file more precisely than
  // the source it came from, so it wins the second slot when present.
  const modifier = edition?.split('·')[0].trim()
  const tail = modifier || (source ? sourceLabels[source.toUpperCase()] ?? source.toUpperCase() : '')
  return [resolution?.toLowerCase(), tail?.toUpperCase()].filter(Boolean).join(' ')
}

function MovieCard({ movie, selected, onToggle, onContext }: { movie: Movie; selected: boolean; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void }) {
  const mark = qualityMark(movie.quality, movie.edition)
  return <Link className={`media-card ${selected ? 'media-selected' : ''}`} to={`/movies/${movie.id}`} onContextMenu={(event) => onContext(event, movie.id)} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}><div className={`poster poster-${movie.id}`}><PosterImage reference={movie.poster} title={movie.title} /><span className="poster-title">{movie.title}</span><span className="poster-year">{movie.year}</span>{mark && <span className="poster-mark">{mark}</span>}{selected && <span className="selection-mark"><Icon name="check" size={14} /></span>}</div><div className="media-card-body"><div className="media-card-meta"><Badge tone={statusTone(movie.status)}>{movie.status}</Badge><Badge tone={statusTone(movie.plex)}>Plex {movie.plex}</Badge>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</div><div className="media-card-path"><Icon name="folder" size={13} />{movie.location}</div></div></Link>
}

function MovieTable({ items, selected, onToggle, onContext, onTagClick }: { items: Movie[]; selected: Set<string>; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void; onTagClick: (tag: string) => void }) { return <Panel className="table-panel"><table className="data-table"><thead><tr><th>Title</th><th>State</th><th>Current release</th><th>Plex</th><th>Tags</th><th>Confidence</th><th>Location</th><th /></tr></thead><tbody>{items.map((movie) => <tr key={movie.id} className={selected.has(movie.id) ? 'row-selected' : ''} onContextMenu={(event) => onContext(event, movie.id)} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}><td><Link className="table-title" to={`/movies/${movie.id}`}><span className={`table-poster poster-${movie.id}`}><PosterImage reference={movie.poster} title={movie.title} /></span>{movie.title}<span className="muted">{movie.year}</span>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</Link></td><td><Badge tone={statusTone(movie.status)}>{movie.status}</Badge></td><td>{movie.quality}{movie.edition && <span className="table-sub">{movie.edition}</span>}</td><td><Badge tone={statusTone(movie.plex)}>{movie.plex}</Badge></td><td><MovieTagChips movie={movie} onTagClick={onTagClick} /></td><td><span className="confidence">{movie.confidence}%</span></td><td className="path-cell">{movie.location}</td><td>{selected.has(movie.id) ? <Icon name="check" size={15} /> : <Icon name="chevron" size={15} />}</td></tr>)}</tbody></table></Panel> }

function MovieBulkContextMenu({ position, count, profiles, tags, busy, onRun, onCreateTag, onClear }: { position: { x: number; y: number }; count: number; profiles: QualityProfile[]; tags: Tag[]; busy: boolean; onRun: (action: Parameters<typeof api.bulkMovies>[0]['action'], options?: { profileId?: string | null; tagIds?: string[] }) => Promise<void>; onCreateTag: (name: string) => Promise<Tag>; onClear: () => void }) {
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
    <div className="context-action-grid"><button onClick={() => void onRun('monitor')} disabled={busy}><Icon name="check" size={14} />Monitor</button><button onClick={() => void onRun('unmonitor')} disabled={busy}><Icon name="pause" size={14} />Unmonitor</button><button onClick={() => void onRun('recheck_plex')} disabled={busy}><Icon name="refresh" size={14} />Recheck Plex</button><button onClick={() => void onRun('reevaluate_parser')} disabled={busy}><Icon name="activity" size={14} />Re-evaluate parser</button><button onClick={() => void onRun('reevaluate_custom_formats')} disabled={busy}><Icon name="sliders" size={14} />Re-evaluate CFs</button></div>
    <div className="context-section"><span className="field-label">Quality Profile</span><div className="context-inline"><Select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">No profile</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</Select><Button variant="ghost" disabled={busy} onClick={() => void onRun('change_profile', { profileId: profileId || null })}>Apply</Button></div><small>Per-title score/minimum overrides are preserved.</small></div>
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

function releaseStateTone(state: string) {
  const normalized = state.toLowerCase()
  if (normalized.includes('current') || normalized.includes('present')) return 'green'
  if (normalized.includes('missing') || normalized.includes('incoming') || normalized.includes('degraded')) return 'amber'
  if (normalized.includes('duplicate') || normalized.includes('conflict')) return 'red'
  return 'neutral'
}

function ReleaseEvidenceRow({ release }: { release: MovieRelease }) {
  const directory = release.directories.find((item) => item.exists) ?? release.directories[0]
  const label = [release.quality, release.edition, release.releaseGroup].filter(Boolean).join(' · ') || release.name
  const scoreChanged = release.originalCustomFormatScore !== undefined && release.currentCustomFormatScore !== undefined && release.originalCustomFormatScore !== release.currentCustomFormatScore
  return <div className="release-evidence-row"><div className="release-icon"><Icon name="film" size={18} /></div><div className="release-main"><strong>{label}</strong><span>{directory?.path ?? 'No directory recorded'}{directory && !directory.exists ? ' · path absent' : ''}</span><small>{release.name}</small>{(release.originalCustomFormatScore !== undefined || release.currentCustomFormatScore !== undefined) && <small>CF score · downloaded {release.originalCustomFormatScore ?? 'n/a'} · current {release.currentCustomFormatScore ?? 0}</small>}</div>{scoreChanged && <Badge tone="neutral">Score changed</Badge>}<Badge tone={releaseStateTone(release.state) as 'green' | 'amber' | 'red' | 'neutral'}>{release.state}</Badge>{release.confidence !== undefined && <span className="release-confidence">{Math.round(release.confidence)}%</span>}</div>
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
  if (aggregate?.qbitMediaDisagreement && !evidence.some((item) => item.code === 'QBIT_MEDIA_DISAGREEMENT')) evidence.push({ code: 'QBIT_MEDIA_DISAGREEMENT', title: 'qBittorrent / media disagreement', detail: aggregate.qbitMediaDetail ?? 'qBittorrent reports a completed item but the expected media path is not present.', severity: 'high', source: 'qBittorrent + filesystem' })
  if ((aggregate?.rootOffline || movie.rootHealth === 'offline' || movie.rootHealth === 'unavailable') && !evidence.some((item) => item.code === 'ROOT_OFFLINE')) evidence.push({ code: 'ROOT_OFFLINE', title: 'Storage root offline', detail: `${aggregate?.rootAffectedCount ?? movie.rootAffectedCount ?? 0} media items are affected; absence is not treated as Missing while the root is offline.`, severity: 'high', source: 'Storage root' })
  return evidence
}


function MovieProfilePanel({ resourceId }: { resourceId: string }) {
  const [settings, setSettings] = useState<MediaProfileSettings | null>(null)
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [qualities, setQualities] = useState<QualityDefinition[]>([])
  const [formats, setFormats] = useState<CustomFormat[]>([])
  const [profileId, setProfileId] = useState('')
  const [minimumOverrideId, setMinimumOverrideId] = useState('')
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
    <div className="field-grid"><label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">No profile</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</Select></label><label><span className="field-label">Minimum quality override</span><Select value={minimumOverrideId} onChange={(event) => setMinimumOverrideId(event.target.value)}><option value="">Inherit profile</option>{qualities.map((quality) => <option value={quality.id} key={quality.id}>{quality.name}</option>)}</Select></label></div>
    <div className="settings-note"><Icon name="shield" size={16} /><span>Overrides replace this title's profile score for the selected Custom Format. They never alter the Custom Format definition or another title.</span></div>
    <div className="score-section"><div className="section-title"><div><div className="eyebrow">TITLE OVERRIDES</div><h3>Differences from profile</h3></div><div className="search-client-chooser"><Select value={newOverride} onChange={(event) => setNewOverride(event.target.value)}><option value="">Add override…</option>{available.map((format) => <option value={format.id} key={format.id}>{format.name}</option>)}</Select><Button variant="ghost" icon="plus" disabled={!newOverride} onClick={() => { if (newOverride) { const inherited = effectiveRows.find((item) => item.customFormatId === newOverride)?.profileScore ?? 0; setOverrides((value) => ({ ...value, [newOverride]: inherited })); setNewOverride('') } }}>Add</Button></div></div>
      {Object.entries(overrides).map(([formatId, score]) => { const inherited = effectiveRows.find((item) => item.customFormatId === formatId)?.profileScore ?? 0; return <div className="score-row" key={formatId}><span><strong>{formatById.get(formatId)?.name ?? effectiveRows.find((item) => item.customFormatId === formatId)?.customFormatName ?? 'Custom Format'}</strong><small>Profile {inherited > 0 ? '+' : ''}{inherited} → override {score > 0 ? '+' : ''}{score}</small></span><Input type="number" value={String(score)} onChange={(event) => setOverrides((value) => ({ ...value, [formatId]: Number(event.target.value) || 0 }))} /><strong className={score < 0 ? 'score-negative' : 'score-positive'}>{score > 0 ? '+' : ''}{score}</strong><button className="icon-button" onClick={() => setOverrides((value) => { const next = { ...value }; delete next[formatId]; return next })}><Icon name="close" size={14} /></button></div> })}
      {!Object.keys(overrides).length && !minimumOverrideId && <span className="muted">No per-title overrides. This movie inherits its assigned profile exactly.</span>}
    </div>
    {settings?.minimumQuality && <div className="settings-note"><Icon name="activity" size={16} /><span>Effective minimum: {settings.minimumQuality.name}{settings.minimumQualityOverridden ? ' (title override)' : ' (profile)'}</span></div>}
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    <div className="settings-footer"><Button variant="primary" onClick={() => void save()} disabled={busy}>{busy ? 'Saving…' : 'Save profile settings'}</Button></div>
  </Panel>
}

function MovieTagsPanel({ resourceId, assigned, onChanged }: { resourceId: string; assigned: Tag[]; onChanged: (tags: Tag[]) => void }) {
  const [allTags, setAllTags] = useState<Tag[]>([])
  const [selectedTag, setSelectedTag] = useState('')
  const [newTag, setNewTag] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const load = async () => {
    try { setAllTags(await api.tags()) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load tags.') }
  }
  useEffect(() => { void load() }, [])
  const available = allTags.filter((tag) => !assigned.some((item) => item.id === tag.id))
  const add = async () => {
    if (!selectedTag) return
    setBusy(true); setMessage('')
    try { onChanged(await api.addMovieTag(resourceId, selectedTag)); setSelectedTag(''); setMessage('Tag added.') }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not add tag.') }
    finally { setBusy(false) }
  }
  const remove = async (tagId: string) => {
    setBusy(true); setMessage('')
    try { onChanged(await api.removeMovieTag(resourceId, tagId)); setMessage('Tag removed.') }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not remove tag.') }
    finally { setBusy(false) }
  }
  const create = async () => {
    if (!newTag.trim()) return
    setBusy(true); setMessage('')
    try {
      const row = await api.createTag(newTag.trim())
      setAllTags((items) => [...items, row].sort((a, b) => a.name.localeCompare(b.name)))
      onChanged(await api.addMovieTag(resourceId, row.id))
      setNewTag('')
      setMessage(`Created and added ${row.name}.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not create tag.') }
    finally { setBusy(false) }
  }
  return <Panel title="Tags" eyebrow="MOVIE ORGANIZATION">
    {assigned.length ? <div className="tag-management-list">{assigned.map((tag) => <div className="tag-management-row" key={tag.id}><Link className="tag-chip" to={`/movies?tag=${encodeURIComponent(tag.name)}`}>{tag.name}</Link><button className="icon-button" aria-label={`Remove ${tag.name}`} disabled={busy} onClick={() => void remove(tag.id)}><Icon name="close" size={13} /></button></div>)}</div> : <span className="muted">No tags assigned to this movie.</span>}
    <div className="context-inline tag-add-row"><Select value={selectedTag} onChange={(event) => setSelectedTag(event.target.value)}><option value="">Add existing tag…</option>{available.map((tag) => <option value={tag.id} key={tag.id}>{tag.name}</option>)}</Select><Button variant="ghost" disabled={busy || !selectedTag} onClick={() => void add()}>Add</Button></div>
    <div className="context-inline"><Input value={newTag} onChange={(event) => setNewTag(event.target.value)} placeholder="Create a new tag…" onKeyDown={(event) => { if (event.key === 'Enter') void create() }} /><Button variant="ghost" disabled={busy || !newTag.trim()} onClick={() => void create()}>Create + add</Button></div>
    <div className="settings-note"><Icon name="sliders" size={16} /><span>Tags are Movies-only in v1. Clicking a tag filters the Movies library.</span></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
  </Panel>
}

export function MovieDetailPage({ id }: { id: string }) {
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
      setMessage(`Plex recheck queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Jobs.`)
      const [job] = await api.waitForJobs([accepted.job_id])
      await load()
      const summary = job?.summary ?? {}
      const count = (key: string) => Number(summary[key] ?? 0)
      if (job?.state === 'completed') setMessage(`Plex recheck complete: ${count('matched_releases')} matched, ${count('not_found_releases')} not in Plex, ${count('multiple_version_releases')} multiple-version matches, ${count('conflict_releases')} conflicts.`)
      else if (job?.state === 'cancelled') setMessage('Plex recheck cancelled. No further Plex verification was performed.')
      else setMessage(`Plex recheck failed${job?.error ? `: ${job.error}` : '.'} Check Jobs for details.`)
    }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start Plex recheck.') }
    finally { setBusy(false) }
  }
  const refreshEvidence = async () => {
    setBusy(true); setMessage('Refreshing reconciliation evidence…')
    try {
      const refresh = await api.reconcileMovie(id)
      const jobIds = [...new Set([...(refresh.job_ids ?? []), ...(refresh.active_job_ids ?? [])].map(String))]
      const jobs = jobIds.length ? await api.waitForJobs(jobIds) : []
      await load()
      const failures = jobs.filter((job) => job.state !== 'completed')
      if (failures.length) setMessage(`Reconciliation finished with ${failures.length} failed, cancelled, or interrupted job${failures.length === 1 ? '' : 's'}. Review Jobs for details.`)
      else if (refresh.uninitialized_root_ids?.length) setMessage(`Reconciliation refresh complete. ${refresh.uninitialized_root_ids.length} storage root${refresh.uninitialized_root_ids.length === 1 ? ' was' : 's were'} skipped until initialized.`)
      else if (!jobs.length) setMessage('No reconciliation job was needed. No filesystem changes were made.')
      else setMessage('Reconciliation refresh complete. No filesystem changes were made.')
    } catch (reason) {
      // Older servers do not expose the optional rescan action; a GET still refreshes persisted evidence.
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 405)) {
        try { setMovie(await api.movie(id)); setMessage('State refreshed from the server. Reconciliation action is not enabled on this server.') }
        catch (fallbackReason) { setMessage(fallbackReason instanceof Error ? fallbackReason.message : 'Could not refresh state.') }
      } else setMessage(reason instanceof Error ? reason.message : 'Could not refresh reconciliation evidence.')
    } finally { setBusy(false) }
  }
  if (error) return <Page title="Movie unavailable" subtitle={error}><EmptyState title="Could not load this movie" detail={error} /></Page>
  if (!movie) return <Page title="Loading movie" subtitle="Retrieving current and historical release state."><EmptyState title="Loading…" detail="Reading the persisted movie record." /></Page>
  const releases = movie.releasesDetail ?? []
  const evidence = evidenceFromMovie(movie)
  const currentReleases = releases.filter((release) => ['current', 'present', 'duplicate', 'conflict'].includes(release.state.toLowerCase()))
  const historyReleases = releases.filter((release) => ['replaced', 'removed', 'missing', 'degraded'].includes(release.state.toLowerCase()))
  const history = movie.recentEvents ?? []
  const currentSummary = <div className="release-row"><div className="release-icon"><Icon name="film" size={18} /></div><div className="release-main"><strong>{movie.quality}{movie.edition ? ` · ${movie.edition}` : ''}</strong><span>{movie.location}</span></div><Badge tone={statusTone(movie.status)}>{movie.status}</Badge></div>
  return <Page title={movie.title} subtitle={`${movie.year} · ${movie.tmdbId ? `TMDB ${movie.tmdbId}` : `Internal ID ${movie.id}`}`} back="Back to Movies" action={<><Button variant="ghost" icon="refresh" onClick={refreshEvidence} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh evidence'}</Button><Button variant="ghost" icon="refresh" onClick={recheckPlex} disabled={busy}>{busy ? 'Checking Plex…' : 'Recheck Plex'}</Button><Button variant="primary" icon="search">Interactive search</Button></>}>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    {(movie.rootHealth === 'offline' || movie.rootHealth === 'unavailable' || movie.reconciliation?.rootOffline) && <div className="reconciliation-banner reconciliation-banner-red"><Icon name="alert" size={17} /><div><strong>Storage Root Offline</strong><span>{movie.reconciliation?.rootAffectedCount ?? movie.rootAffectedCount ?? 0} media affected. Missing grace is held until the root is reachable.</span></div></div>}
    <div className="detail-layout"><div><Panel className="detail-hero"><div className="detail-poster"><PosterImage reference={movie.poster} title={movie.title} /><span className="poster-title">{movie.title}</span><span className="poster-year">{movie.year}</span></div><div className="detail-intro"><div className="eyebrow">MOVIE {movie.tmdbId ? `· TMDB ${movie.tmdbId}` : ''}</div><h2>{movie.title} <span className="detail-year">({movie.year})</span></h2><div className="badge-row"><Badge tone={statusTone(movie.status)}>{movie.status}</Badge><Badge tone={statusTone(movie.plex)}>Plex {movie.plex}</Badge><Badge tone="blue">{movie.confidence}% match</Badge>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</div>{movie.tags?.length ? <div className="tag-chip-row detail-tag-row">{movie.tags.map((tag) => <Link key={tag.id} className="tag-chip" to={`/movies?tag=${encodeURIComponent(tag.name)}`}>{tag.name}</Link>)}</div> : null}<p className="detail-description">{movie.overview ?? 'The filesystem remains the source of truth. Reconciliation preserves old paths and release evidence without moving or deleting media.'}</p><div className="detail-actions"><Button variant="ghost" icon="external">Change match</Button></div></div></Panel>{movie.incoming && <IncomingReplacement incoming={movie.incoming} />}<Panel title="Current releases" eyebrow="REGISTERED MEDIA">{currentReleases.length ? currentReleases.map((release) => <ReleaseEvidenceRow key={release.id || release.name} release={release} />) : currentSummary}</Panel><MovieProfilePanel resourceId={id} /><MovieTagsPanel resourceId={id} assigned={movie.tags ?? []} onChanged={(tags) => setMovie((current) => current ? { ...current, tags } : current)} /><Panel title="Release history" eyebrow="PRESERVED EVIDENCE">{history.length ? history.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{event.type} · {formatEvidenceDate(event.createdAt)}</span></div><Badge tone={event.type.includes('replaced') ? 'purple' : event.type.includes('duplicate') || event.type.includes('conflict') ? 'red' : 'neutral'}>{event.type.replaceAll('.', ' ')}</Badge></div>) : historyReleases.map((release) => <div className="history-row" key={release.id || release.name}><span className="history-line" /><div><strong>{release.name}</strong><span>{release.state} · first observed {formatEvidenceDate(release.firstSeenAt)}</span></div><Badge tone={releaseStateTone(release.state) as 'green' | 'amber' | 'red' | 'neutral'}>Release</Badge></div>)}{!history.length && !historyReleases.length && <div className="history-empty">No persisted release events yet. New replacement and reappearance events will appear here.</div>}</Panel><TorrentHistoryPanel items={movie.torrentHistory} /></div><aside className="detail-side"><Panel title="At a glance" eyebrow="STATUS"><DetailFact label="Library state" value={movie.status} tone={statusTone(movie.status)} /><DetailFact label="Plex verification" value={movie.plex} tone={statusTone(movie.plex)} /><DetailFact label="Storage root" value={movie.storageRoot ?? 'Unknown root'} /><DetailFact label="Last observed" value={formatEvidenceDate(movie.lastObservedAt)} /></Panel><Panel title="Problems" eyebrow={evidence.length ? `${evidence.length} NEED ATTENTION` : 'NEEDS ATTENTION'}>{evidence.length ? evidence.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}><div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div><div><strong>{item.title}</strong><span>{item.detail}</span></div></div>) : <div className="problem-mini"><div className="problem-icon"><Icon name="check" size={14} /></div><div><strong>No unresolved problems</strong><span>Identity, qBittorrent, Plex, and path evidence agree.</span></div></div>}</Panel></aside></div>
  </Page>
}

function DetailFact({ label, value, tone }: { label: string; value: string; tone?: string }) { return <div className="detail-fact"><span>{label}</span>{tone ? <Badge tone={tone as 'green' | 'amber' | 'red' | 'neutral'}>{value}</Badge> : <strong>{value}</strong>}</div> }

export function ShowsPage() {
  const [items, setItems] = useState<Show[]>([])
  const [view, setView] = useState<'cards' | 'table'>(() => normalizeMediaView(window.localStorage.getItem('medialogue.shows.view')))
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [countSpecials, setCountSpecials] = useState(true)
  const [specialsBusy, setSpecialsBusy] = useState(false)
  const load = async (foreground = true) => {
    if (foreground) setLoading(true)
    try { setItems(await api.shows(query)); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load shows.') }
    finally { if (foreground) setLoading(false) }
  }
  useLiveLibraryRefresh(() => load(false))
  useEffect(() => { const timer = window.setTimeout(() => { void load() }, 150); return () => window.clearTimeout(timer) }, [query])
  useEffect(() => { api.specialsCounting().then((value) => setCountSpecials(value.count_specials)).catch(() => undefined) }, [])

  // A hard switch, not a view filter: it rewrites the Counted flag on Season 0
  // for every show. Monitoring is left alone, so nothing stops being searched.
  const toggleSpecials = async () => {
    const next = !countSpecials
    if (!next && !window.confirm('Stop counting Specials on every show? They will be excluded from episode totals, and any per-show Counted choice you made by hand is replaced. Monitoring is not affected.')) return
    setSpecialsBusy(true)
    try {
      const result = await api.setSpecialsCounting(next)
      setCountSpecials(result.count_specials)
      setError('')
      await load(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not change Specials counting.') }
    finally { setSpecialsBusy(false) }
  }
  const present = items.filter((show) => show.status === 'Present').length
  const missingEpisodes = items.reduce((sum, show) => sum + (show.episodesMissing ?? Math.max(0, show.episodesTotal - show.episodesPresent)), 0)
  const totalEpisodes = items.reduce((sum, show) => sum + show.episodesTotal, 0)
  return <Page title="Shows" subtitle="Track seasons and episodes without reorganizing your files.">
    <div className="stats-row"><Stat label="Shows" value={String(items.length)} detail={`${present} fully present`} tone="blue" /><Stat label="Episodes" value={String(totalEpisodes)} detail={`${items.reduce((sum, show) => sum + show.episodesPresent, 0)} present`} tone="green" /><Stat label="Missing" value={String(missingEpisodes)} detail="Episode-level inventory" tone="amber" /><Stat label="Needs review" value={String(items.filter((show) => show.status === 'Conflict' || (show.problemCount ?? 0) > 0).length)} detail="Conflicts and mapping issues" tone="red" /></div>
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search shows…" /></div><div className="specials-toggle" title={countSpecials ? 'Specials count toward episode totals on every show' : 'Specials are excluded from episode totals. Monitoring is unaffected.'}><span className="specials-toggle-label">Count specials</span><button type="button" className="toggle" aria-pressed={countSpecials} aria-label="Count Specials toward episode totals on every show" disabled={specialsBusy} onClick={() => void toggleSpecials()}><span /></button><span className="toggle-label">{specialsBusy ? 'Applying…' : countSpecials ? 'Yes' : 'No'}</span></div><div className="toolbar-spacer" /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => { setView('cards'); window.localStorage.setItem('medialogue.shows.view', 'cards') }}><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => { setView('table'); window.localStorage.setItem('medialogue.shows.view', 'table') }}><Icon name="list" size={16} /></button></div><Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button></div>
    {error && <EmptyState title="Could not load Shows" detail={error} />}
    {!error && !loading && (view === 'cards' ? <div className="media-grid show-grid">{items.map((show) => <ShowCard key={show.id} show={show} />)}</div> : <Panel className="table-panel"><table className="data-table"><thead><tr><th>Show</th><th>Episodes</th><th>Status</th><th>Plex</th><th>Seasons</th><th /></tr></thead><tbody>{items.map((show) => <tr key={show.id}><td><Link className="table-title" to={`/shows/${show.id}`}><span className={`table-poster poster-${show.id}`}><PosterImage reference={show.poster} title={show.title} /></span>{show.title}<span className="muted">{show.year || ''}</span></Link></td><td>{show.episodesPresent} / {show.episodesTotal}</td><td><Badge tone={statusTone(show.status)}>{show.status}</Badge></td><td><Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge></td><td>{show.seasons}</td><td><Icon name="chevron" size={15} /></td></tr>)}</tbody></table></Panel>)}
    {!error && !loading && !items.length && <EmptyState title="No Shows yet" detail="Add a Show from TMDB or scan a configured Show storage root." />}
  </Page>
}

function ShowCard({ show }: { show: Show }) {
  const percent = show.episodesTotal ? Math.round((show.episodesPresent / show.episodesTotal) * 100) : 0
  return <Link className="media-card" to={`/shows/${show.id}`}><div className={`poster poster-${show.id}`}><PosterImage reference={show.poster} title={show.title} /><span className="poster-title">{show.title}</span><span className="poster-year">{show.year || ''}</span><span className="poster-mark">{show.seasons === 1 ? '1 SEASON' : `${show.seasons} SEASONS`}</span></div><div className="media-card-body"><div className="media-card-meta"><Badge tone={statusTone(show.status)}>{show.status}</Badge><Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge></div><div className="episode-progress"><div><span>Episodes</span><strong>{show.episodesPresent} / {show.episodesTotal}</strong></div><Progress value={percent} tone={percent === 100 ? 'green' : 'amber'} /></div><div className="media-card-path"><Icon name="tv" size={13} />{(show.problemCount ?? 0) === 1 ? '1 problem' : `${show.problemCount ?? 0} problems`}</div></div></Link>
}

export function ShowDetailPage({ id }: { id: string }) {
  const [show, setShow] = useState<Show | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
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
      setMessage(`TMDB metadata refresh queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Jobs.`)
      const [job] = await api.waitForJobs([accepted.job_id])
      await load()
      if (job?.state === 'completed') {
        const summary = job.summary ?? {}
        setMessage(`TMDB metadata refreshed: ${Number(summary.seasons ?? 0)} season${Number(summary.seasons ?? 0) === 1 ? '' : 's'} and ${Number(summary.episodes ?? 0)} episode${Number(summary.episodes ?? 0) === 1 ? '' : 's'} updated. Existing media mappings were preserved.`)
      } else if (job?.state === 'cancelled') setMessage('TMDB metadata refresh cancelled.')
      else setMessage(`TMDB metadata refresh failed${job?.error ? `: ${job.error}` : '.'} Check Jobs for details.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Metadata refresh failed.') }
    finally { setBusy(false) }
  }
  const recheckPlex = async () => {
    setBusy(true); setMessage('Starting Plex recheck…')
    try {
      const accepted = await api.recheckShowPlex(id)
      setMessage(`Plex recheck queued as job ${accepted.job_id.slice(0, 8)}…. You can follow it in Jobs.`)
      const [job] = await api.waitForJobs([accepted.job_id])
      await load()
      const summary = job?.summary ?? {}
      const count = (key: string) => Number(summary[key] ?? 0)
      if (job?.state === 'completed') setMessage(`Plex recheck complete: ${count('matched_releases')} matched, ${count('not_found_releases')} not in Plex, ${count('conflict_releases')} conflicts.`)
      else if (job?.state === 'cancelled') setMessage('Plex recheck cancelled. No further Plex verification was performed.')
      else setMessage(`Plex recheck failed${job?.error ? `: ${job.error}` : '.'} Check Jobs for details.`)
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

  // Switching renumbers the show's episodes, so it is always an explicit act —
  // never something a background metadata refresh does on its own.
  const chooseOrdering = async (option: EpisodeOrdering) => {
    if (option.selected || !show) return
    if (!window.confirm(`Switch this show to "${option.name}"? Episodes keep their identity but are renumbered, so files already matched may need a re-scan to line up with the new numbering.`)) return
    setOrderingBusy(true)
    try {
      await api.updateShow(id, { tmdb_episode_group_id: option.id ?? '', expected_revision: show.revision })
      setOrderings(null)
      await load()
      setMessage(`Episode ordering switched to ${option.name}. Re-scan this show's storage root if file matches look off.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not switch episode ordering.') }
    finally { setOrderingBusy(false) }
  }

  const setSeasonCounted = async (season: Season, counted: boolean) => { try { await api.updateSeason(season.id, { counted, expected_revision: season.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update season counting.') } }
  const setEpisodeMonitored = async (episode: Episode, monitored: boolean) => { try { await api.updateEpisode(episode.id, { monitored, expected_revision: episode.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update episode monitoring.') } }
  const searchSeason = async (season: Season) => { try { const result = await api.startSeasonSearch(season.id); setMessage(`Season search started · job ${result.job_id.slice(0, 8)}…`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start season search.') } }
  const searchEpisode = async (episode: Episode) => { try { const result = await api.startEpisodeSearch(episode.id); setMessage(`Episode search started · job ${result.job_id.slice(0, 8)}…`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start episode search.') } }
  const editMapping = (media: EpisodeMedia, season: Season) => { const selected = season.episodes.filter((episode) => media.mappedEpisodeNumbers.includes(episode.episodeNumber)).map((episode) => episode.id); setMappingEpisodeIds(selected); setMappingEditor({ media, season }) }
  const saveMapping = async () => { if (!mappingEditor || !mappingEpisodeIds.length) return; setBusy(true); try { await api.correctEpisodeMapping(mappingEditor.media.mediaFileId, mappingEpisodeIds); setMessage('Episode mapping corrected. The media file and path were not changed.'); setMappingEditor(null); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not correct episode mapping.') } finally { setBusy(false) } }
  if (error) return <Page title="Show unavailable" subtitle={error} back="Back to Shows" backTo="/shows"><EmptyState title="Could not load this Show" detail={error} /></Page>
  if (!show) return <Page title="Loading Show" subtitle="Retrieving seasons and episode inventory." back="Back to Shows" backTo="/shows"><EmptyState title="Loading…" detail="Reading the persisted Show record." /></Page>
  const seasons = show.seasonDetail ?? []
  return <Page title={show.title} subtitle={`${show.year || 'Year unknown'} · ${show.tmdbId ? `TMDB ${show.tmdbId}` : show.id}${show.tvdbId ? ` · TVDB ${show.tvdbId}` : ''}`} back="Back to Shows" backTo="/shows" action={<><Button variant="ghost" icon="refresh" onClick={() => void refreshMetadata()} disabled={busy}>{busy ? 'Working…' : 'Refresh metadata'}</Button><Button variant="ghost" icon="refresh" onClick={() => void recheckPlex()} disabled={busy}>{busy ? 'Working…' : 'Recheck Plex'}</Button></>}>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    {mappingEditor && <Panel title="Correct episode mapping" eyebrow="LOGICAL MAPPING ONLY"><div className="settings-note"><Icon name="shield" size={15} /><span>This changes only Medialogue's episode mapping. The file is not renamed, moved, copied, or modified.</span></div><div className="mapping-path">{mappingEditor.media.path}</div><div className="mapping-grid">{mappingEditor.season.episodes.map((episode) => <label className="inline-check" key={episode.id}><input type="checkbox" checked={mappingEpisodeIds.includes(episode.id)} onChange={(event) => setMappingEpisodeIds((current) => event.target.checked ? [...current, episode.id] : current.filter((item) => item !== episode.id))} />S{String(episode.seasonNumber).padStart(2, '0')}E{String(episode.episodeNumber).padStart(2, '0')} · {episode.title || 'Untitled episode'}</label>)}</div><div className="settings-footer"><Button variant="ghost" onClick={() => setMappingEditor(null)}>Cancel</Button><Button variant="primary" onClick={() => void saveMapping()} disabled={busy || !mappingEpisodeIds.length}>{busy ? 'Saving…' : 'Save mapping'}</Button></div></Panel>}
    <Panel className="detail-hero"><div className={`detail-poster poster-${show.id}`}><PosterImage reference={show.poster} title={show.title} /><span className="poster-title">{show.title}</span><span className="poster-year">{show.year || ''}</span></div><div className="detail-intro"><div className="eyebrow">SHOW {show.tmdbId ? `· TMDB ${show.tmdbId}` : ''}</div><h2>{show.title} {show.year ? <span className="detail-year">({show.year})</span> : null}</h2><div className="badge-row"><Badge tone={statusTone(show.status)}>{show.status}</Badge><Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge><Badge tone={show.monitored === false ? 'neutral' : 'blue'}>{show.monitored === false ? 'Unmonitored' : 'Monitored'}</Badge></div><p className="detail-description">{show.overview ?? 'Episode presence is tracked independently while every file stays at its existing path.'}</p></div></Panel>
    <div className="detail-layout"><div><Panel title="Episode ordering" eyebrow="TMDB STRUCTURE" action={orderings ? undefined : <Button variant="ghost" onClick={() => void loadOrderings()} disabled={orderingBusy}>{orderingBusy ? 'Loading…' : 'Show orderings'}</Button>}>
      {orderings
        ? <div className="ordering-list">
            {orderings.map((option) => <button
              className={`ordering-option ${option.selected ? 'selected' : ''}`}
              key={option.id ?? 'default'}
              disabled={orderingBusy}
              onClick={() => void chooseOrdering(option)}
            >
              <span className="ordering-copy">
                <strong>{option.name}</strong>
                <span>{option.season_count} season{option.season_count === 1 ? '' : 's'} · {option.episode_count} episode{option.episode_count === 1 ? '' : 's'}{option.network ? ` · ${option.network}` : ''}</span>
                {option.description && <small>{option.description}</small>}
              </span>
              <Badge tone={option.selected ? 'green' : 'neutral'}>{option.selected ? 'In use' : option.type_label}</Badge>
            </button>)}
            <p className="cf-modal-note">TMDB stores alternate orderings for many shows. They contain the same episodes arranged differently, so switching renumbers what you already have rather than replacing it.</p>
          </div>
        : <div className="ordering-summary">
            <span className="muted">Using {show.tmdbEpisodeGroupId ? 'a custom TMDB episode group' : "TMDB's default season structure"}.</span>
            <small>If this show&rsquo;s seasons do not match how your files are numbered, another ordering probably does.</small>
          </div>}
    </Panel>
    <Panel title="Seasons & Episodes" eyebrow={`${show.episodesPresent} / ${show.episodesTotal} PRESENT`}>{seasons.map((season) => { const open = expanded[season.id] ?? false; return <div className="season-block" key={season.id}><div className="release-evidence-row"><button className="icon-button" onClick={() => setExpanded((value) => ({ ...value, [season.id]: !open }))}><Icon name="chevron" size={15} style={{ transform: open ? 'rotate(90deg)' : undefined }} /></button><div className="release-main"><strong>{season.title || `Season ${season.seasonNumber}`}</strong><span>{season.presentCount} / {season.episodeCount} present · {season.missingCount} missing</span></div><Badge tone={season.missingCount ? 'amber' : 'green'}>{season.missingCount ? 'Incomplete' : 'Complete'}</Badge><Button variant="ghost" onClick={() => void searchSeason(season)}>Search season</Button><label className="inline-check" title="Keep searching for missing episodes in this season."><input type="checkbox" checked={season.monitored} onChange={(event) => void setSeasonMonitored(season, event.target.checked)} />Monitored</label><label className="inline-check" title="Include this season in the show's episode totals. Independent of monitoring."><input type="checkbox" checked={season.counted} onChange={(event) => void setSeasonCounted(season, event.target.checked)} />Counted</label></div>{open && <div className="episode-list">{season.episodes.map((episode) => <div className="history-row" key={episode.id}><span className="history-line" /><div><strong>S{String(episode.seasonNumber).padStart(2, '0')}E{String(episode.episodeNumber).padStart(2, '0')} · {episode.title || 'Untitled episode'}</strong><span>{episode.quality || 'No media'}{episode.media[0]?.path ? ` · ${episode.media[0].path}` : ''}</span><div className="badge-row compact">{episode.media[0]?.releaseScope === 'season_pack' && <Badge tone="purple">Season pack</Badge>}{episode.media[0]?.mappedEpisodeNumbers.length > 1 && <Badge tone="blue">Multi-episode · {episode.media[0].mappedEpisodeNumbers.map((number) => `E${String(number).padStart(2, '0')}`).join(' + ')}</Badge>}{episode.media[0]?.manualMapping && <Badge tone="neutral">Manual mapping</Badge>}</div></div><Badge tone={statusTone(episode.status)}>{episode.status}</Badge><Badge tone={statusTone(episode.plex)}>Plex {episode.plex}</Badge><label className="inline-check"><input type="checkbox" checked={episode.monitored} onChange={(event) => void setEpisodeMonitored(episode, event.target.checked)} />Monitor</label>{episode.media[0] && <Button variant="ghost" onClick={() => editMapping(episode.media[0], season)}>Map</Button>}<Button variant="ghost" onClick={() => void searchEpisode(episode)}>Search</Button></div>)}</div>}</div> })}{!seasons.length && <div className="history-empty">No season metadata exists yet. Refresh TMDB metadata or scan a Show root.</div>}</Panel><ShowProfilePanel resourceId={id} /><Panel title="Recent history" eyebrow="EVENTS">{show.recentEvents?.length ? show.recentEvents.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{event.type} · {formatEvidenceDate(event.createdAt)}</span></div></div>) : <div className="history-empty">No Show or episode events recorded yet.</div>}</Panel></div><aside className="detail-side"><Panel title="At a glance" eyebrow="STATUS"><DetailFact label="Library state" value={show.status} tone={statusTone(show.status)} /><DetailFact label="Plex verification" value={show.plex} tone={statusTone(show.plex)} /><DetailFact label="Episodes present" value={`${show.episodesPresent} / ${show.episodesTotal}`} /><DetailFact label="Last observed" value={formatEvidenceDate(show.lastObservedAt)} /></Panel><Panel title="Problems" eyebrow={`${show.problemCount ?? 0} NEED ATTENTION`}>{show.problems?.length ? show.problems.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}><div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div><div><strong>{item.title}</strong><span>{item.detail}</span></div></div>) : <div className="problem-mini"><div className="problem-icon"><Icon name="check" size={14} /></div><div><strong>No unresolved problems</strong><span>Mapped episode files are consistent with current evidence.</span></div></div>}</Panel></aside></div>
  </Page>
}

function ShowProfilePanel({ resourceId }: { resourceId: string }) {
  const [settings, setSettings] = useState<MediaProfileSettings | null>(null)
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [qualities, setQualities] = useState<QualityDefinition[]>([])
  const [profileId, setProfileId] = useState('')
  const [minimumOverrideId, setMinimumOverrideId] = useState('')
  const [message, setMessage] = useState('')
  const load = async () => { try { const [value, profileRows, qualityRows] = await Promise.all([api.showProfileSettings(resourceId), api.qualityProfiles(), api.qualityDefinitions()]); setSettings(value); setProfiles(profileRows); setQualities(qualityRows); setProfileId(value.qualityProfileId ?? ''); setMinimumOverrideId(value.minimumQualityOverridden ? value.minimumQuality?.id ?? '' : '') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load Show profile.') } }
  useEffect(() => { void load() }, [resourceId])
  const save = async () => { try { const value = await api.saveShowProfileSettings(resourceId, { quality_profile_id: profileId || null, minimum_quality_definition_override_id: minimumOverrideId || null, custom_format_score_overrides: Object.fromEntries((settings?.customFormatScores ?? []).filter((item) => item.overrideScore !== undefined).map((item) => [item.customFormatId, item.overrideScore ?? 0])), expected_revision: settings?.revision ?? 0 }); setSettings(value); setMessage('Show Quality Profile saved.') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save Show profile.') } }
  return <Panel title="Quality Profile" eyebrow="SHOW SEARCH SCORING"><div className="field-grid"><label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">No profile</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</Select></label><label><span className="field-label">Minimum quality override</span><Select value={minimumOverrideId} onChange={(event) => setMinimumOverrideId(event.target.value)}><option value="">Inherit profile</option>{qualities.map((quality) => <option value={quality.id} key={quality.id}>{quality.name}</option>)}</Select></label></div>{message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}<div className="settings-footer"><Button variant="primary" onClick={() => void save()}>Save profile settings</Button></div></Panel>
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


export function EventHistoryPage() {
  const [events, setEvents] = useState<EventHistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(0)
  const [page, setPage] = useUrlNumber('page', 1)
  const [eventType, setEventType] = useUrlState('type')
  const [severity, setSeverity] = useUrlState('severity')
  const [entityType, setEntityType] = useUrlState('entity')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const pageSize = 100

  const load = async (targetPage = page) => {
    setLoading(true)
    try {
      const payload = await api.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, page: targetPage, pageSize })
      setEvents(payload.items)
      setTotal(payload.total)
      setPages(payload.pages)
      setPage(payload.page)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load event history.')
    } finally { setLoading(false) }
  }

  useEffect(() => { setPage(1) }, [eventType, severity, entityType])

  useEffect(() => {
    let alive = true
    const refresh = () => api.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, page, pageSize }).then((payload) => {
      if (alive) { setEvents(payload.items); setTotal(payload.total); setPages(payload.pages); setError('') }
    }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load event history.') }).finally(() => { if (alive) setLoading(false) })
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
  }, [eventType, severity, entityType, page])

  const removeEvent = async (item: EventHistoryItem) => {
    if (!window.confirm(`Delete this history event?\n\n${item.message}\n\nThis only removes the history row.`)) return
    try { await api.deleteEvent(item.id); await load(page) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete event.') }
  }

  const clearHistory = async () => {
    const label = eventType || severity || entityType ? 'all events matching the current filters' : 'ALL event history'
    if (!window.confirm(`Delete ${label}?\n\nThis does not change current media, torrent, problem, or integration state.`)) return
    try {
      const result = await api.clearEvents({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined })
      setPage(1)
      await load(1)
      setError(result.deleted ? '' : 'No matching history events were present.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not clear event history.') }
  }

  return <Page title="Event History" subtitle="Durable state changes and decisions. High-frequency progress stays live-only and does not flood this history." action={<div className="page-actions"><Button variant="ghost" icon="refresh" onClick={() => void load()}>Refresh</Button><Button variant="danger" onClick={() => void clearHistory()}>Clear history</Button></div>}>
    <div className="toolbar event-toolbar">
      <Select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option><option value="info">Info</option><option value="warning">Warning</option><option value="error">Error</option></Select>
      <Select value={entityType} onChange={(event) => setEntityType(event.target.value)}><option value="">All entities</option><option value="movie">Movies</option><option value="show">Shows</option><option value="episode">Episodes</option><option value="movie_release">Movie releases</option><option value="show_release">Show releases</option><option value="torrent">Torrents</option><option value="storage_root">Storage roots</option><option value="download_client">Download clients</option><option value="tag">Tags</option><option value="bulk_operation">Bulk operations</option></Select>
      <Input value={eventType} onChange={(event) => setEventType(event.target.value)} placeholder="Exact event type, e.g. release.replaced" />
      <div className="toolbar-spacer" /><span className="muted">{total} durable events · page {pages ? page : 0} of {pages}</span>
    </div>
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {!loading && <Panel className="table-panel event-history-panel"><table className="data-table event-history-table"><thead><tr><th>When</th><th>Severity</th><th>Event</th><th>Entity</th><th>Message</th><th /></tr></thead><tbody>{events.map((item) => <tr key={item.id}><td className="event-time">{new Date(item.createdAt).toLocaleString()}</td><td><Badge tone={item.severity === 'error' ? 'red' : item.severity === 'warning' ? 'amber' : 'neutral'}>{item.severity}</Badge></td><td><code>{item.eventType}</code></td><td><span className="event-entity">{item.entityType}{item.entityId ? ` · ${item.entityId.slice(0, 8)}…` : ''}</span></td><td><strong>{item.message}</strong></td><td><div className="row-actions">{Object.keys(item.details).length > 0 && <details className="event-details"><summary>Details</summary><pre>{JSON.stringify(item.details, null, 2)}</pre></details>}<Button variant="ghost" onClick={() => void removeEvent(item)}>Delete</Button></div></td></tr>)}</tbody></table>{!events.length && <div className="history-empty">No durable events match the selected filters.</div>}</Panel>}
    {!loading && pages > 1 && <div className="pagination-bar"><Button variant="ghost" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</Button><span>Page {page} of {pages} · {total} events</span><Button variant="ghost" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>Next</Button></div>}
    {loading && <EmptyState icon="clock" title="Loading event history" detail="Reading persisted state changes from PostgreSQL." />}
  </Page>
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

function tmdbReasonLabel(reason?: string) {
  if (reason === 'not_found') return 'No exact title/year match was returned.'
  if (reason === 'ambiguous') return 'More than one exact title/year candidate remained.'
  if (reason === 'not_configured') return 'TMDB is not configured.'
  if (reason === 'unavailable') return 'TMDB could not be reached.'
  return reason ? reason.replaceAll('_', ' ') : 'TMDB did not establish a unique identity.'
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

function ProblemTMDBCandidate({ candidate, selected = false, onSelect }: { candidate: Record<string, unknown>; selected?: boolean; onSelect?: () => void }) {
  const title = problemText(candidate.title) ?? 'Untitled candidate'
  const year = problemText(candidate.year)
  const poster = problemText(candidate.poster_path ?? candidate.poster_ref)
  const overview = problemText(candidate.overview)
  const content = <><div className="tmdb-candidate-poster">{poster ? <img src={tmdbPosterUrl(poster)} alt={`${title} poster`} loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = 'none' }} /> : <Icon name="film" size={22} />}</div><div className="tmdb-candidate-copy"><strong>{problemIdentity(title, year) ?? title}</strong><span>TMDB {problemText(candidate.tmdb_id) ?? 'unknown'}{problemText(candidate.original_title) && problemText(candidate.original_title) !== title ? ` · original: ${problemText(candidate.original_title)}` : ''}</span>{overview && <small>{overview}</small>}</div>{onSelect && <span className="tmdb-candidate-state">{selected ? 'Selected' : 'Select'}</span>}</>
  return onSelect ? <button type="button" className={`problem-candidate tmdb-problem-candidate ${selected ? 'selected' : ''}`} onClick={onSelect}>{content}</button> : <div className="problem-candidate tmdb-problem-candidate">{content}</div>
}

function ProblemEvidenceDetails({ problem, selectedTmdbId, onSelectTmdbCandidate }: { problem: Problem; selectedTmdbId?: number; onSelectTmdbCandidate?: (candidate: Record<string, unknown>) => void }) {
  const details = problem.details ?? {}
  if (problem.code === 'EPISODE_CONTAINER_MISMATCH') {
    const memberEpisodes = Array.isArray(details.member_episode_numbers) ? details.member_episode_numbers.map(String) : []
    const containerEpisodes = Array.isArray(details.container_episode_numbers) ? details.container_episode_numbers.map(String) : []
    const containerSeasons = Array.isArray(details.container_seasons) ? details.container_seasons.map(String) : []
    const containerSeasonLabel = containerSeasons.length > 1
      ? `Seasons ${containerSeasons[0]}–${containerSeasons[containerSeasons.length - 1]}`
      : `Season ${containerSeasons[0] ?? problemText(details.container_season) ?? '?'}`
    return <div className="problem-evidence-block">
      <div className="problem-evidence-note">The member filename controls episode identity. The conflicting folder or torrent is retained only as diagnostic context and is not allowed to remap the file.</div>
      <div className="problem-evidence-grid">
        <div className="problem-evidence-side"><span className="eyebrow">MEMBER FILENAME</span><strong>{problemText(details.filename) ?? 'Unknown media file'}</strong><code>{problemText(details.path) ?? 'Path not recorded'}</code><span>Season {problemText(details.member_season) ?? '?'}{memberEpisodes.length ? ` · episode${memberEpisodes.length === 1 ? '' : 's'} ${memberEpisodes.join(', ')}` : ''}</span></div>
        <div className="problem-evidence-side"><span className="eyebrow">CONTAINER CONTEXT</span><strong>{problemText(details.container_name) ?? 'Unknown container'}</strong><span>{problemText(details.container_source) ?? 'folder/torrent'} · {containerSeasonLabel}{containerEpisodes.length ? ` · episode${containerEpisodes.length === 1 ? '' : 's'} ${containerEpisodes.join(', ')}` : ''}</span></div>
      </div>
    </div>
  }
  if (problem.code === 'DUPLICATE_EPISODE_RELEASE') {
    const files = Array.isArray(details.media_files) ? details.media_files.map(problemRecord) : []
    const preferredId = problemText(details.preferred_media_file_id)
    return <div className="problem-evidence-block">
      <div className="problem-evidence-note">Only distinct paths verified on disk count as physical duplicates. Catalogue rows in a missing-file grace period are shown for diagnosis but cannot be selected.</div>
      <div className="problem-candidate-list">{files.map((file, index) => {
        const id = problemText(file.media_file_id) ?? `${problem.id}-file-${index}`
        const physical = file.physical_exists === true
        const preferred = id === preferredId
        return <div className={`problem-candidate ${preferred ? 'selected' : ''}`} key={id}>
          <strong>{problemText(file.filename) ?? 'Unknown episode file'}{preferred ? ' · preferred' : ''}</strong>
          <code>{problemText(file.path) ?? 'Path not recorded'}</code>
          <span>{problemText(file.release_name) ? `Release: ${problemText(file.release_name)} · ` : ''}{physical ? 'Verified on disk' : file.missing_since ? `Not observed since ${problemText(file.missing_since)}` : 'Not present on disk'}{file.manual_mapping === true ? ' · manual mapping' : ''}</span>
        </div>
      })}</div>
    </div>
  }

  if (problem.code === 'TORRENT_PATH_NOT_FOUND' || problem.code === 'TORRENT_REMOVED_EXTERNALLY') {
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
    return <div className="problem-evidence-block"><div className="problem-evidence-note">This warning predates detailed path evidence. Recheck it to collect the current qBittorrent path, translated local path, and mapped filesystem state.</div></div>
  }

  if (problem.code === 'PLEX_IDENTITY_MISMATCH') {
    const localIdentity = problemText(details.local_identity) ?? problemIdentity(details.local_title ?? details.medialogue_title, details.local_year ?? details.medialogue_year)
    const plexIdentity = problemText(details.plex_identity) ?? problemIdentity(details.plex_title, details.plex_year)
    const localPath = problemText(details.local_path ?? details.medialogue_path)
    const plexPath = problemText(details.plex_path ?? details.plex_reported_path)
    const differences = Array.isArray(details.differences) ? details.differences.map((value) => problemText(value)).filter((value): value is string => Boolean(value)) : []
    const conflicts = Array.isArray(details.conflicts) ? details.conflicts.map(problemRecord) : []
    if (problem.entityType === 'show' && conflicts.length > 0) return <div className="problem-evidence-block">
      <div className="problem-evidence-note">Plex show titles are advisory. This Problem exists only because Plex mapped the same physical file to different season/episode numbers.</div>
      <div className="problem-candidate-list">{conflicts.map((conflict, index) => <div className="problem-candidate" key={`${problem.id}-plex-${index}`}><strong>{problemText(conflict.local_episode) ?? 'Medialogue episode'} → {problemText(conflict.plex_episode) ?? 'Plex episode'}</strong><span>{problemText(conflict.local_show_title) ?? 'Show'} · Medialogue: {problemText(conflict.local_path) ?? 'unknown path'}</span><span>Plex metadata: {problemText(conflict.plex_show_title) ?? 'unknown show title'}{problemText(conflict.plex_episode_title) ? ` · ${problemText(conflict.plex_episode_title)}` : ''} · {problemText(conflict.plex_path) ?? 'unknown path'}</span><span>Different: {Array.isArray(conflict.differences) ? conflict.differences.map(readableEvidenceValue).join(', ') : 'episode numbering'}</span></div>)}</div>
    </div>
    return <div className="problem-evidence-block">
      <div className="problem-evidence-grid">
        <div className="problem-evidence-side"><span className="eyebrow">MEDIALOGUE</span><strong>{localIdentity ?? 'Local identity not stored'}</strong><code>{localPath ?? 'Local path not recorded'}</code></div>
        <div className="problem-evidence-side"><span className="eyebrow">PLEX</span><strong>{plexIdentity ?? 'Plex identity not stored'}</strong><code>{plexPath ?? 'Plex path not recorded'}</code></div>
      </div>
      {differences.length > 0 && <div className="problem-difference"><strong>Different:</strong> {differences.join(', ')}</div>}
    </div>
  }

  if (problem.code === 'TMDB_IDENTITY_UNRESOLVED' || problem.code === 'TMDB_SHOW_IDENTITY_UNRESOLVED') {
    const parsed = parserIdentity(details)
    const reason = problemText(details.tmdb_reason)
    const candidates = Array.isArray(details.tmdb_candidates) ? details.tmdb_candidates.map(problemRecord) : []
    const queries = Array.isArray(details.tmdb_queries) ? details.tmdb_queries.map((value) => problemText(value)).filter((value): value is string => Boolean(value)) : []
    return <div className="problem-evidence-block">
      <div className="problem-evidence-grid">
        <div className="problem-evidence-side"><span className="eyebrow">PARSER CANDIDATE</span><strong>{parsed.identity ?? 'No usable title/year extracted'}</strong><code>{problemText(details.path) ?? problem.subject}</code></div>
        <div className="problem-evidence-side"><span className="eyebrow">TMDB RESULT</span><strong>{tmdbReasonLabel(reason)}</strong><span>{queries.length ? `Queries tried: ${queries.join(' · ')}` : 'Recheck evidence to record the exact TMDB queries/candidates with the updated resolver.'}</span></div>
      </div>
      {candidates.length > 0 && <div className="problem-candidate-list"><span className="eyebrow">CANDIDATES RETURNED BY TMDB</span>{candidates.map((candidate, index) => <ProblemTMDBCandidate key={`${problem.id}-tmdb-${problemText(candidate.tmdb_id) ?? index}`} candidate={candidate} selected={selectedTmdbId === Number(candidate.tmdb_id)} onSelect={onSelectTmdbCandidate ? () => onSelectTmdbCandidate(candidate) : undefined} />)}</div>}
    </div>
  }

  if (problem.code === 'LOW_CONFIDENCE_MATCH') {
    const parsed = parserIdentity(details)
    const confidenceRaw = typeof details.confidence === 'number' ? details.confidence : Number(details.confidence)
    const confidence = Number.isFinite(confidenceRaw) ? `${Math.round((confidenceRaw <= 1 ? confidenceRaw * 100 : confidenceRaw) * 10) / 10}%` : 'unknown'
    return <div className="problem-evidence-block">
      <div className="problem-evidence-grid">
        <div className="problem-evidence-side"><span className="eyebrow">PARSER RESULT</span><strong>{parsed.identity ?? 'No usable title/year extracted'}</strong><code>{problemText(details.path) ?? problem.subject}</code></div>
        <div className="problem-evidence-side"><span className="eyebrow">WHY IT WAS BLOCKED</span><strong>Identity confidence {confidence}</strong><span>{parsed.warnings.length ? `Parser warnings: ${parsed.warnings.join(', ')}` : 'The title/year evidence did not reach the automatic-match threshold.'}</span></div>
      </div>
    </div>
  }

  const entries = Object.entries(details).filter(([key]) => key !== 'parse')
  if (!entries.length) return null
  return <div className="problem-evidence-block problem-evidence-facts">{entries.map(([key, value]) => <div className="problem-evidence-fact" key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{readableEvidenceValue(value)}</strong></div>)}</div>
}

function TMDBCandidateCard({ match, selected, onSelect }: { match: TMDBCandidate; selected: boolean; onSelect: () => void }) {
  return <button type="button" className={`tmdb-candidate-card ${selected ? 'selected' : ''}`} onClick={onSelect}>
    <div className="tmdb-candidate-poster">{match.posterRef ? <img src={tmdbPosterUrl(match.posterRef)} alt={`${match.title} poster`} loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = 'none' }} /> : <Icon name="film" size={22} />}</div>
    <div className="tmdb-candidate-copy"><strong>{match.title}{match.year ? ` (${match.year})` : ''}</strong><span>TMDB {match.tmdbId}{match.originalTitle && match.originalTitle !== match.title ? ` · original: ${match.originalTitle}` : ''}</span>{match.overview && <small>{match.overview}</small>}</div>
    <span className="tmdb-candidate-state">{selected ? 'Selected' : 'Select'}</span>
  </button>
}

function TMDBMatchPicker({ kind, query, matches, selected, loading, onQueryChange, onSearch, onSelect, onApply }: { kind: 'Movie' | 'Show'; query: string; matches: TMDBCandidate[]; selected?: TMDBCandidate; loading: boolean; onQueryChange: (value: string) => void; onSearch: () => void; onSelect: (match: TMDBCandidate) => void; onApply: () => void }) {
  return <div className="resolution-block"><div className="eyebrow">MANUAL {kind.toUpperCase()} MATCH</div><p>Search TMDB, select the exact {kind.toLowerCase()}, then apply the match. The filesystem is not renamed or moved.</p><div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder={`Search TMDB ${kind}s…`} onKeyDown={(event) => { if (event.key === 'Enter') onSearch() }} /></div><Button variant="ghost" onClick={onSearch} disabled={loading || !query.trim()}>Search</Button></div>{matches.length > 0 && <div className="tmdb-candidate-grid">{matches.map((match) => <TMDBCandidateCard key={match.tmdbId} match={match} selected={selected?.tmdbId === match.tmdbId} onSelect={() => onSelect(match)} />)}</div>}{selected && <div className="tmdb-selection-bar"><span>Selected: <strong>{selected.title}{selected.year ? ` (${selected.year})` : ''}</strong> · TMDB {selected.tmdbId}</span><Button variant="primary" onClick={onApply} disabled={loading}>Apply selected {kind}</Button></div>}</div>
}

function LegacyProblemsPage() {
  const [items, setItems] = useState<Problem[]>([])
  const [selected, setSelected] = useUrlState('problem')
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [duplicatePreview, setDuplicatePreview] = useState<DuplicateResolvePreview | null>(null)
  const [winnerReleaseId, setWinnerReleaseId] = useState('')
  const [deleteMedia, setDeleteMedia] = useState(false)
  const [removeTorrents, setRemoveTorrents] = useState(false)
  const [tmdbQuery, setTmdbQuery] = useState('')
  const [tmdbMatches, setTmdbMatches] = useState<TMDBMovieLookup[]>([])
  const [tmdbShowMatches, setTmdbShowMatches] = useState<TMDBShowLookup[]>([])
  const [selectedTmdbMatch, setSelectedTmdbMatch] = useState<TMDBMovieLookup | undefined>()
  const [selectedTmdbShowMatch, setSelectedTmdbShowMatch] = useState<TMDBShowLookup | undefined>()
  const [duplicateMovie, setDuplicateMovie] = useState<Movie | null>(null)
  const [reasonFilter, setReasonFilter] = useUrlState('reason', 'all')
  const [severityFilter, setSeverityFilter] = useUrlState('severity', 'all')
  const [page, setPage] = useUrlNumber('page', 1)
  const [pages, setPages] = useState(0)
  const [total, setTotal] = useState(0)
  const [openTotal, setOpenTotal] = useState(0)
  const loadGeneration = useRef(0)
  const reloadCurrent = useRef<() => void>(() => undefined)
  const pageSize = 100

  const load = async (targetPage = page, preserveMessage = false) => {
    const generation = ++loadGeneration.current
    setLoading(true)
    try {
      const [payload, count] = await Promise.all([
        api.problemsPage({ status: 'open', page: targetPage, pageSize, category: reasonFilter, severity: severityFilter }),
        api.problemCount('open'),
      ])
      if (generation !== loadGeneration.current) return
      setItems(payload.items)
      setPage(payload.page)
      setPages(payload.pages)
      setTotal(payload.total)
      setOpenTotal(count)
      if (!preserveMessage) setMessage('')
      setLoaded(true)
    }
    catch (reason) {
      if (generation === loadGeneration.current) {
        setMessage(reason instanceof Error ? reason.message : 'Could not load live problems.')
        setLoaded(true)
      }
    }
    finally { if (generation === loadGeneration.current) setLoading(false) }
  }
  reloadCurrent.current = () => { void load(page, true) }
  useEffect(() => { void load() }, [page, reasonFilter, severityFilter])
  useEffect(() => {
    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    let timer: number | undefined
    const invalidate = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      timer = window.setTimeout(() => reloadCurrent.current(), 150)
    }
    stream.addEventListener('problem.created', invalidate)
    stream.addEventListener('problem.updated', invalidate)
    stream.addEventListener('problem.resolved', invalidate)
    stream.addEventListener('problem.deleted', invalidate)
    return () => {
      if (timer !== undefined) window.clearTimeout(timer)
      stream.close()
    }
  }, [])
  const sourceProblems = items
  const visible = sourceProblems
  const current = visible.find((problem) => problem.id === selected) ?? visible[0]
  const selectTmdbCandidate = (candidate: Record<string, unknown>) => {
    const match = problemTMDBCandidateLookup(candidate)
    if (!match) return
    if (current?.availableActions?.includes('confirm_movie_match')) setSelectedTmdbMatch(match as TMDBMovieLookup)
    if (current?.availableActions?.includes('confirm_show_match')) setSelectedTmdbShowMatch(match as TMDBShowLookup)
  }
  useEffect(() => {
    setDuplicatePreview(null)
    setWinnerReleaseId('')
    setDeleteMedia(false)
    setRemoveTorrents(false)
    setTmdbMatches([])
    setTmdbShowMatches([])
    setSelectedTmdbMatch(undefined)
    setSelectedTmdbShowMatch(undefined)
    setTmdbQuery('')
    setDuplicateMovie(null)
  }, [current?.id])

  useEffect(() => {
    if (current?.code !== 'DUPLICATE_PHYSICAL_RELEASE' || !current.entityId) return
    let alive = true
    api.movie(current.entityId).then((movie) => { if (alive) setDuplicateMovie(movie) }).catch(() => { if (alive) setDuplicateMovie(null) })
    return () => { alive = false }
  }, [current?.code, current?.entityId])

  const reEvaluate = async () => {
    setLoading(true); setMessage('Refreshing reconciliation evidence…')
    try {
      const refresh = await api.reconcileAll()
      const jobs = await api.waitForJobs([...new Set([...refresh.jobIds, ...refresh.activeJobIds])])
      await load(page, true)
      const failures = jobs.filter((job) => job.state !== 'completed')
      if (failures.length) setMessage(`Reconciliation finished with ${failures.length} failed, cancelled, or interrupted job${failures.length === 1 ? '' : 's'}. Review Jobs for details.`)
      else if (refresh.uninitializedRootIds.length) setMessage(`Reconciliation refresh complete. ${refresh.uninitializedRootIds.length} new storage root${refresh.uninitializedRootIds.length === 1 ? ' was' : 's were'} skipped until you press Scan once in Storage settings.`)
      else if (refresh.skippedRootIds.length && !refresh.activeJobIds.length) setMessage('Problems refreshed. A storage-root scan was already running and could not be tracked from this request.')
      else setMessage('Reconciliation refresh complete. No filesystem changes were made.')
    }
    catch (reason) {
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 405)) { await load(); setMessage('Problems refreshed. This server does not expose a bulk reconciliation action.') }
      else setMessage(reason instanceof Error ? reason.message : 'Could not refresh problems.')
    }
    finally { setLoading(false) }
  }

  const releaseIds = current?.code === 'DUPLICATE_PHYSICAL_RELEASE' && Array.isArray(current.details?.release_ids)
    ? current.details.release_ids.map((value) => String(value))
    : []
  const loserIds = duplicateLoserIds(releaseIds, winnerReleaseId)

  const previewDuplicate = async () => {
    if (!current?.entityId || !winnerReleaseId || !loserIds.length) return
    setLoading(true); setMessage('')
    try {
      setDuplicatePreview(await api.previewMovieDuplicate(current.entityId, {
        winner_release_id: winnerReleaseId,
        losing_release_ids: loserIds,
        delete_media: deleteMedia,
        remove_torrents: removeTorrents,
      }))
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not preview duplicate resolution.') }
    finally { setLoading(false) }
  }

  const commitDuplicate = async () => {
    if (!current?.entityId || !duplicatePreview) return
    setLoading(true); setMessage('')
    try {
      const accepted = await api.resolveMovieDuplicate(current.entityId, duplicatePreview.confirmationToken)
      setDuplicatePreview(null)
      const [job] = await api.waitForJobs([accepted.job_id])
      await load()
      if (!job || job.state !== 'completed') {
        setMessage(`Duplicate resolution ${job?.state ?? 'failed'}. Review Jobs for details.`)
      } else {
        const summary = job.summary ?? {}
        const deleted = Array.isArray(summary.deleted_directories) ? summary.deleted_directories : []
        const warnings = Array.isArray(summary.warnings) ? summary.warnings : []
        setMessage(Boolean(summary.duplicate_resolved)
          ? `Duplicate resolved. ${deleted.length ? `${deleted.length} losing director${deleted.length === 1 ? 'y' : 'ies'} deleted.` : 'No media was deleted.'}${warnings.length ? ` ${warnings.length} warning${warnings.length === 1 ? '' : 's'} recorded.` : ''}`
          : `Preferred release recorded. The duplicate remains open until the losing copy is actually gone.${warnings.length ? ` ${warnings.length} warning${warnings.length === 1 ? '' : 's'} recorded.` : ''}`)
      }
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not commit duplicate resolution.') }
    finally { setLoading(false) }
  }

  const searchTmdb = async () => {
    if (!tmdbQuery.trim()) return
    setLoading(true); setMessage('')
    try { setTmdbMatches(await api.lookupMovies(tmdbQuery.trim())); setSelectedTmdbMatch(undefined) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'TMDB lookup failed.') }
    finally { setLoading(false) }
  }

  const confirmMovie = async (match: TMDBMovieLookup) => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      await api.resolveProblem(current.id, 'confirm_movie_match', { tmdb_id: match.tmdbId })
      await load()
      setSelectedTmdbMatch(undefined)
      setMessage(`Manually matched to ${match.title}${match.year ? ` (${match.year})` : ''}. No media was renamed or moved.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not apply the manual Movie match.') }
    finally { setLoading(false) }
  }

  const searchTmdbShows = async () => {
    if (!tmdbQuery.trim()) return
    setLoading(true); setMessage('')
    try { setTmdbShowMatches(await api.lookupShows(tmdbQuery.trim())); setSelectedTmdbShowMatch(undefined) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'TMDB Show lookup failed.') }
    finally { setLoading(false) }
  }

  const confirmShow = async (match: TMDBShowLookup) => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      await api.resolveProblem(current.id, 'confirm_show_match', { tmdb_id: match.tmdbId })
      await load()
      setSelectedTmdbShowMatch(undefined)
      setMessage(`Manually matched to ${match.title}${match.year ? ` (${match.year})` : ''}. No media was renamed or moved.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not apply the manual Show match.') }
    finally { setLoading(false) }
  }

  const chooseEpisodeWinner = async (mediaFileId: string) => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      await api.resolveProblem(current.id, 'choose_episode_winner', { winner_media_file_id: mediaFileId })
      await load()
      setMessage('Preferred episode file recorded. The physical duplicate remains flagged until the losing file is actually gone.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not select the preferred episode file.') }
    finally { setLoading(false) }
  }

  const recheckProblem = async () => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      const result = await api.resolveProblem(current.id, 'recheck')
      const parentJobId = typeof result.resolution?.recheck_parent_job_id === 'string' ? result.resolution.recheck_parent_job_id : ''
      const [job] = parentJobId ? await api.waitForJobs([parentJobId]) : []
      await load(page, true)
      const summary = job?.summary ?? {}
      const directErrors = Array.isArray(summary.direct_errors) ? summary.direct_errors.map(String) : []
      const qbitErrors = Array.isArray(summary.qbit_errors) ? summary.qbit_errors.map(String) : []
      const errors = [...directErrors, ...qbitErrors]
      setMessage(job?.state === 'completed'
        ? errors.length ? `Evidence recheck completed with warnings: ${errors.join('; ')}`
        : summary.condition_cleared === true ? 'Problem cleared: the condition is no longer present.'
        : 'Evidence recheck complete: the condition is still present.'
        : job?.state === 'cancelled' ? 'Evidence recheck cancelled.'
        : `Evidence recheck failed${job?.error ? `: ${job.error}` : '.'} Review Jobs for details.`)
    }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not request a recheck.') }
    finally { setLoading(false) }
  }

  const dismissProblem = async () => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      await api.resolveProblem(current.id, 'dismiss')
      setSelected('')
      await load(page, true)
      setMessage('Problem dismissed after manual review.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not dismiss Problem.') }
    finally { setLoading(false) }
  }

  const deleteCurrentProblem = async () => {
    if (!current) return
    if (!window.confirm(`Delete this Problem record?\n\n${current.title}\n\nNo media or torrent data will be changed. The Problem may be recreated if the same condition is observed again.`)) return
    setLoading(true); setMessage('')
    try {
      await api.deleteProblem(current.id)
      setSelected('')
      const targetPage = visible.length === 1 && page > 1 ? page - 1 : page
      if (targetPage !== page) setPage(targetPage)
      else await load(targetPage)
      setMessage('Problem record deleted. Media and torrent data were untouched.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not delete Problem.') }
    finally { setLoading(false) }
  }

  const clearOpenProblems = async () => {
    const filtered = reasonFilter !== 'all' || severityFilter !== 'all'
    const label = filtered ? `${total} Problems matching the current filters` : `all ${openTotal} open Problems`
    if (!window.confirm(`Delete ${label}?\n\nThis only clears Problem records. Reconciliation can recreate a Problem later if the underlying condition still exists.`)) return
    setLoading(true); setMessage('')
    try {
      const result = await api.clearProblems({ status: 'open', category: reasonFilter, severity: severityFilter })
      setSelected('')
      setPage(1)
      await load(1)
      setMessage(`${result.deleted} Problem record${result.deleted === 1 ? '' : 's'} deleted.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not clear Problems.') }
    finally { setLoading(false) }
  }

  return <Page title="Problems" subtitle="A single queue for identity conflicts, duplicates, root outages, and low-confidence matches.">
    <div className="problem-summary"><div className="problem-summary-icon"><Icon name="alert" size={22} /></div><div><strong>{!loaded ? 'Loading Problems…' : `${openTotal} open Problem${openTotal === 1 ? '' : 's'}`}</strong><span>Deleting a Problem removes only its record. Media and torrents stay untouched.</span></div><div className="page-actions"><Button variant="ghost" icon="refresh" onClick={() => void reEvaluate()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh evidence'}</Button><Button variant="danger" onClick={() => void clearOpenProblems()} disabled={loading || !total}>Clear {reasonFilter !== 'all' || severityFilter !== 'all' ? 'filtered' : 'all open'}</Button></div></div>
    <div className="toolbar"><Select value={reasonFilter} onChange={(event) => { setReasonFilter(event.target.value); setSelected(''); setPage(1) }}><option value="all">All problem types</option><option value="duplicates">Duplicates</option><option value="identity">Identity / matching</option><option value="paths">Paths / storage</option><option value="PLEX_IDENTITY_MISMATCH">Plex conflicts</option></Select><Select value={severityFilter} onChange={(event) => { setSeverityFilter(event.target.value); setSelected(''); setPage(1) }}><option value="all">All priorities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></Select><span className="muted">Showing {visible.length} of {total} matching · {openTotal} open total · page {pages ? page : 0} of {pages}</span></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    <div className="problem-layout"><Panel className="problem-list-panel"><div className="problem-filter"><span className="eyebrow">OPEN PROBLEMS</span><span className="muted">{total} matching</span></div>{visible.map((problem, index) => <button key={problem.id || `${problem.code}-${index}`} className={`problem-row ${current?.id === problem.id ? 'selected' : ''}`} onClick={() => setSelected(problem.id)}><div className={`problem-severity severity-${problem.severity}`}><Icon name="alert" size={15} /></div><div className="problem-row-copy"><strong>{problem.title}</strong><span>{problem.subject}</span><small>{problem.created}</small></div><Icon name="chevron" size={16} /></button>)}</Panel>
      <Panel className="problem-detail-panel" title={current?.title ?? 'Select a problem'} eyebrow={current?.code ?? 'REVIEW QUEUE'}>{current ? <>
        <div className="issue-banner"><Badge tone={current.severity === 'high' ? 'red' : current.severity === 'low' ? 'neutral' : 'amber'}>{current.severity} priority</Badge><span>{current.code}</span></div>
        <p className="issue-detail">{current.detail}</p>
        <div className="compare-card"><div><span className="eyebrow">AFFECTED MEDIA</span><strong>{current.subject}</strong><span>{current.entityType ? `${current.entityType} · ` : ''}{current.entityId ?? 'Persisted reconciliation evidence'}</span></div><Icon name="arrow" size={20} /><div><span className="eyebrow">PROBLEM SOURCE</span><strong>{current.code === 'PLEX_IDENTITY_MISMATCH' ? 'Plex compared with Medialogue' : current.code === 'DUPLICATE_EPISODE_RELEASE' ? 'Verified episode paths on disk' : current.code === 'EPISODE_CONTAINER_MISMATCH' ? 'Member filename compared with its container' : current.code === 'TORRENT_PATH_NOT_FOUND' || current.code === 'TORRENT_REMOVED_EXTERNALLY' ? 'qBittorrent compared with mapped filesystem state' : current.code.includes('DUPLICATE') ? 'Physical filesystem evidence' : current.code.startsWith('TMDB_') ? 'Parser candidate compared with TMDB' : 'Reconciliation observation'}</strong><span>{current.code === 'DUPLICATE_EPISODE_RELEASE' ? 'A duplicate requires two distinct file paths that are both verified on disk.' : current.code === 'EPISODE_CONTAINER_MISMATCH' ? 'Explicit episode markers in the member filename take priority over folder and torrent naming.' : current.code === 'TORRENT_PATH_NOT_FOUND' || current.code === 'TORRENT_REMOVED_EXTERNALLY' ? 'The qBittorrent and local paths below show the current evidence behind this warning.' : 'Review the evidence below before taking action.'}</span></div></div>
        <ProblemEvidenceDetails problem={current} selectedTmdbId={selectedTmdbMatch?.tmdbId ?? selectedTmdbShowMatch?.tmdbId} onSelectTmdbCandidate={selectTmdbCandidate} />

        {current.code === 'DUPLICATE_PHYSICAL_RELEASE' && releaseIds.length > 1 && <div className="resolution-block"><div className="eyebrow">DUPLICATE RESOLVER</div><p>Select the copy to keep. Medialogue will not delete the loser unless you explicitly request deletion and review a fresh inventory first.</p><div className="history-list">{releaseIds.map((releaseId) => { const release = duplicateMovie?.releasesDetail?.find((item) => item.id === releaseId); const path = release?.directories?.find((directory) => directory.exists)?.path ?? release?.directories?.[0]?.path; return <label className="inline-check" key={releaseId}><input type="radio" name="duplicate-winner" checked={winnerReleaseId === releaseId} onChange={() => { setWinnerReleaseId(releaseId); setDuplicatePreview(null) }} /><span><strong>{release?.name ?? `Release ${releaseId}`}</strong><small>{[release?.quality, release?.edition, release?.releaseGroup].filter(Boolean).join(' · ') || 'Release details unavailable'}{path ? ` · ${path}` : ''}</small></span></label> })}</div><label className="inline-check"><input type="checkbox" checked={deleteMedia} onChange={(event) => { setDeleteMedia(event.target.checked); setDuplicatePreview(null) }} />Delete the losing media director{loserIds.length === 1 ? 'y' : 'ies'} after preview</label><label className="inline-check"><input type="checkbox" checked={removeTorrents} onChange={(event) => { setRemoveTorrents(event.target.checked); setDuplicatePreview(null) }} />Remove losing torrent(s) from qBittorrent; archived .torrent files remain</label><div className="detail-actions"><Button variant="primary" disabled={!winnerReleaseId || loading} onClick={() => void previewDuplicate()}>Preview resolution</Button></div></div>}

        {duplicatePreview && <div className="resolution-block destructive-preview"><div className="eyebrow">FRESH DESTRUCTIVE PREVIEW</div><strong>{duplicatePreview.movieTitle}</strong><div className="settings-note"><Icon name="shield" size={15} /><span>Torrent backups are retained. The confirmation expires at {new Date(duplicatePreview.expiresAt).toLocaleTimeString()}.</span></div><div className="compare-card"><div><span className="eyebrow">KEEP</span><strong>{duplicatePreview.winner.releaseName}</strong><span>{[duplicatePreview.winner.quality, duplicatePreview.winner.edition, duplicatePreview.winner.releaseGroup].filter(Boolean).join(' · ')}</span></div><Icon name="arrow" size={20} /><div><span className="eyebrow">LOSING COPY</span><strong>{duplicatePreview.losers.map((item) => item.releaseName).join(' / ')}</strong><span>{deleteMedia ? 'Entire associated media directory will be deleted.' : 'Media will remain untouched; duplicate stays open.'}</span></div></div>{duplicatePreview.losers.flatMap((release) => release.directories).map((directory) => <div className="duplicate-directory-preview" key={directory.directoryId}><strong>{directory.path}</strong><span>{directory.storageRoot} · {directory.accessMode} · {directory.files.length} files inventoried</span>{deleteMedia && <div className="expert-code">{directory.files.map((file) => file.relativePath).join('\n') || '(directory is already empty)'}</div>}</div>)}{duplicatePreview.warnings.map((warning) => <div className="settings-note" key={warning}><Icon name="alert" size={15} /><span>{warning}</span></div>)}<div className="detail-actions"><Button variant="ghost" onClick={() => setDuplicatePreview(null)}>Cancel</Button><Button variant={deleteMedia || removeTorrents ? 'danger' : 'primary'} disabled={loading} onClick={() => void commitDuplicate()}>{deleteMedia || removeTorrents ? 'Commit destructive resolution' : 'Record preferred copy'}</Button></div></div>}

        {current.availableActions?.includes('confirm_movie_match') && <TMDBMatchPicker kind="Movie" query={tmdbQuery} matches={tmdbMatches} selected={selectedTmdbMatch} loading={loading} onQueryChange={setTmdbQuery} onSearch={() => void searchTmdb()} onSelect={(match) => setSelectedTmdbMatch(match as TMDBMovieLookup)} onApply={() => { if (selectedTmdbMatch) void confirmMovie(selectedTmdbMatch) }} />}

        {current.availableActions?.includes('confirm_show_match') && <TMDBMatchPicker kind="Show" query={tmdbQuery} matches={tmdbShowMatches} selected={selectedTmdbShowMatch} loading={loading} onQueryChange={setTmdbQuery} onSearch={() => void searchTmdbShows()} onSelect={(match) => setSelectedTmdbShowMatch(match as TMDBShowLookup)} onApply={() => { if (selectedTmdbShowMatch) void confirmShow(selectedTmdbShowMatch) }} />}

        {current.code === 'PATH_MAPPING_FAILED' && <div className="resolution-block"><div className="eyebrow">PATH MAPPING</div><p>Add or adjust the qBittorrent remote path mapping under Settings → Storage Roots, then recheck this Problem. Medialogue will never guess a filesystem translation.</p></div>}

        {current.availableActions?.includes('choose_episode_winner') && Array.isArray(current.details?.media_file_ids) && <div className="resolution-block"><div className="eyebrow">EPISODE DUPLICATE</div><p>Choose the file Medialogue should protect as the authoritative mapping. No file is deleted; remove the unwanted copy yourself, then recheck.</p><div className="detail-actions">{current.details.media_file_ids.map((id) => { const file = Array.isArray(current.details?.media_files) ? current.details.media_files.map(problemRecord).find((item) => problemText(item.media_file_id) === String(id)) : undefined; const label = problemText(file?.path) ?? problemText(file?.filename); const selectedFile = problemText(current.details?.preferred_media_file_id) === String(id); return <Button variant={selectedFile ? 'primary' : 'ghost'} key={String(id)} onClick={() => void chooseEpisodeWinner(String(id))} disabled={loading || selectedFile || !label}>{selectedFile ? 'Preferred: ' : label ? 'Prefer ' : ''}{label ?? 'Recheck to load file details'}</Button> })}</div></div>}

        <div className="detail-actions">{current.availableActions?.includes('recheck') && <Button variant="ghost" icon="refresh" onClick={() => void recheckProblem()} disabled={loading}>Recheck evidence</Button>}{current.availableActions?.includes('dismiss') && <Button variant="ghost" onClick={() => void dismissProblem()} disabled={loading}>Dismiss</Button>}<Button variant="danger" onClick={() => void deleteCurrentProblem()} disabled={loading}>Delete warning record</Button></div>
      </> : <EmptyState icon="alert" title="No unresolved problems on this page" detail={total ? 'Use the page controls to continue through the queue.' : 'The live reconciliation queue is clear.'} />}</Panel></div>
    {pages > 1 && <div className="pagination-bar"><Button variant="ghost" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</Button><span>Page {page} of {pages} · {total} matching Problems</span><Button variant="ghost" disabled={page >= pages || loading} onClick={() => setPage((value) => Math.min(pages, value + 1))}>Next</Button></div>}
  </Page>
}

const problemWorkflowLabels: Record<Problem['workflow'], string> = {
  manual: 'Fix manually',
  choice: 'Confirm identity',
  config: 'Fix settings',
  waiting: 'Recheck',
}

function problemCandidateFromEvidence(candidate: Record<string, unknown>): TMDBCandidate | undefined {
  return problemTMDBCandidateLookup(candidate)
}

function ProblemKeyDetails({ problem, duplicateMovie }: { problem: Problem; duplicateMovie: Movie | null }) {
  const details = problem.details ?? {}
  const rows: Array<[string, string, string?]> = []
  if (problem.code === 'DUPLICATE_PHYSICAL_RELEASE' && duplicateMovie?.releasesDetail?.length) {
    duplicateMovie.releasesDetail.filter((release) => release.state === 'duplicate' || release.state === 'current').forEach((release, index) => {
      const directory = release.directories.find((item) => item.exists) ?? release.directories[0]
      rows.push([`Copy ${index + 1}`, directory?.path ?? release.name, [release.quality, release.edition, release.releaseGroup].filter(Boolean).join(' · ')])
    })
  } else if (problem.code === 'DUPLICATE_EPISODE_RELEASE' && Array.isArray(details.media_files)) {
    details.media_files.map(problemRecord).forEach((file, index) => rows.push([
      `File ${index + 1}`,
      problemText(file.path) ?? problemText(file.filename) ?? 'Path not recorded',
      file.physical_exists === true ? 'Verified on disk' : 'Not currently verified on disk',
    ]))
  } else if (problem.code === 'PLEX_IDENTITY_MISMATCH' && Array.isArray(details.conflicts)) {
    const conflict = problemRecord(details.conflicts[0])
    rows.push(
      ['Medialogue says', problemText(conflict.local_episode) ?? problemText(details.local_identity) ?? problem.subject, problemText(conflict.local_path)],
      ['Plex says', problemText(conflict.plex_episode) ?? problemText(details.plex_identity) ?? 'Conflicting Plex identity', problemText(conflict.plex_path)],
    )
  } else {
    const path = problemText(details.path ?? details.local_path ?? details.reported_path ?? details.resolved_path)
    if (path) rows.push(['Path', path])
    const parsed = parserIdentity(details)
    const parserWarnings = problem.code === 'TMDB_SHOW_IDENTITY_UNRESOLVED'
      ? parsed.warnings.filter((warning) => !['quality_not_detected', 'release_group_inferred_nogroup'].includes(warning))
      : parsed.warnings
    if (parsed.identity) rows.push(['Parsed as', parsed.identity, parserWarnings.length ? parserWarnings.join(' · ') : undefined])
    const qbitPath = problemText(details.qbittorrent_path ?? details.remote_path)
    if (qbitPath && qbitPath !== path) rows.push(['qBittorrent path', qbitPath])
  }
  if (!rows.length) rows.push(['Affected media', problem.subject, problem.entityType ? `${problem.entityType} evidence` : undefined])
  return <section className="problems-key-details">{rows.map(([label, value, note], index) => <div className="problems-detail-row" key={`${label}-${index}`}><span>{label}</span><div><strong>{value}</strong>{note && <small>{note}</small>}</div></div>)}</section>
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
  return <section className="problems-identity-resolver">
    <div className="problems-resolver-head"><div><div className="eyebrow">POSSIBLE MATCHES</div><p>Choose the identity Medialogue should use. Media will not be renamed or moved.</p></div>{onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}</div>
    {matches.length ? <div className="problems-identity-grid">{matches.slice(0, 6).map((match) => <button type="button" className={`problems-identity-card ${selected?.tmdbId === match.tmdbId ? 'selected' : ''}`} key={match.tmdbId} onClick={() => onSelect(match)} aria-pressed={selected?.tmdbId === match.tmdbId}>
      <span className="problems-identity-poster">{match.posterRef ? <img src={tmdbPosterUrl(match.posterRef)} alt={`${match.title} poster`} loading="lazy" referrerPolicy="no-referrer" /> : <Icon name={problem.entityType === 'show' ? 'tv' : 'film'} size={26} />}</span>
      <span className="problems-identity-copy"><span className="problems-identity-title"><strong>{match.title}</strong>{match.year && <span>{match.year}</span>}</span>{match.overview && <span className="problems-identity-overview">{match.overview}</span>}<span className="problems-identity-credits"><span><strong>{problem.entityType === 'show' ? 'Creator' : 'Director'}:</strong> {match.director ?? 'Not listed'}</span><span><strong>Cast:</strong> {match.cast?.length ? match.cast.join(' · ') : 'Not listed'}</span></span><span className="problems-identity-meta">TMDB {match.tmdbId}</span></span>
      <span className="problems-identity-radio" aria-hidden="true" />
    </button>)}</div> : <div className="problems-candidate-empty">{loading ? 'Loading TMDB suggestions…' : 'Search TMDB to load possible matches.'}</div>}
    <div className="problems-tmdb-search"><div><strong>Not one of these?</strong><span>Search TMDB directly</span></div><Input value={query} onChange={(event) => onQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') onSearch() }} placeholder="Title or title + year" /><Button variant="ghost" icon="search" onClick={onSearch} disabled={loading || !query.trim()}>{loading ? 'Searching…' : 'Search TMDB'}</Button></div>
    <div className="problems-identity-submit"><Button variant="primary" onClick={onApply} disabled={loading || !selected}>Use selected match</Button></div>
  </section>
}

export function ProblemsPage() {
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
  const [loaded, setLoaded] = useState(false)
  const [message, setMessage] = useState('')
  const [duplicateMovie, setDuplicateMovie] = useState<Movie | null>(null)
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
      setItems(payload.items); setTotal(payload.total); setPages(payload.pages); setPage(payload.page); setSummary(nextSummary); setLoaded(true)
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
    setDuplicateMovie(null); setSelectedTmdb(undefined); setEditingPlexIdentity(false); setTmdbMatches([])
    if (!current) return
    if (current.code === 'DUPLICATE_PHYSICAL_RELEASE' && current.entityId) void api.movie(current.entityId).then(setDuplicateMovie).catch(() => undefined)
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
      setMessage(`Matched to ${selectedTmdb.title}${selectedTmdb.year ? ` (${selectedTmdb.year})` : ''}.${followup ? ' Metadata import and file reconciliation are continuing in Jobs.' : ''}`)
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
      setMessage('Evidence recheck finished. Resolved Problems were removed from the queue.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not recheck this Problem.') }
    finally { setLoading(false) }
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
    if (!window.confirm(prompt)) return
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
    if (!plexConfiguration?.url || !plexRatingKey) { setMessage('The Plex item link is not available in this Problem’s stored evidence. Recheck it to collect the current Plex item.'); return }
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

  const workflowTabs: Array<{ value: string; label: string }> = [
    { value: 'all', label: 'Needs attention' }, { value: 'manual', label: 'Fix manually' }, { value: 'choice', label: 'Confirm identity' }, { value: 'config', label: 'Configuration' }, { value: 'waiting', label: 'Recheck' },
  ]

  return <Page title="Problems" subtitle="See what is wrong, exactly where it is, and what to do next." action={<div className="page-actions"><Button variant="ghost" onClick={() => updateProblemView({ problemStatus: queueStatus === 'dismissed' ? null : 'dismissed', problem: null, page: null })}>{queueStatus === 'dismissed' ? 'Back to active' : `Suppressed${summary.suppressed ? ` (${summary.suppressed})` : ''}`}</Button><Button variant="ghost" icon="refresh" onClick={() => void recheckAll()} disabled={loading || !summary.open}>{loading ? 'Working…' : 'Check all again'}</Button></div>}>
    <div className={`problems-summary ${queueStatus === 'dismissed' ? 'suppressed' : ''}`}><div className="problems-summary-icon"><Icon name={queueStatus === 'dismissed' ? 'shield' : 'alert'} size={20} /></div><div><strong>{!loaded ? 'Loading Problems…' : queueStatus === 'dismissed' ? `${summary.suppressed} suppressed Problem${summary.suppressed === 1 ? '' : 's'}` : `${summary.open} Problem${summary.open === 1 ? '' : 's'} need attention`}</strong><span>{queueStatus === 'dismissed' ? 'Suppressed fingerprints stay hidden from the active queue until an administrator restores them.' : 'Work through the queue. A Problem disappears as soon as its evidence confirms it is solved.'}</span></div></div>
    {queueStatus === 'open' && <nav className="problems-workflow-tabs" aria-label="Problem workflow filters">{workflowTabs.map((tab) => <button type="button" className={workflow === tab.value ? 'active' : ''} onClick={() => updateProblemView({ workflow: tab.value === 'all' ? null : tab.value, problem: null, page: null })} key={tab.value}>{tab.label}<span>{tab.value === 'all' ? summary.open : summary.workflows[tab.value] ?? 0}</span></button>)}</nav>}
    <div className="problems-toolbar"><label className="problems-search"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search issues, media, paths…" /></label><Select value={reasonFilter} onChange={(event) => updateProblemView({ reason: event.target.value === 'all' ? null : event.target.value, problem: null, page: null })}><option value="all">All issue types</option><option value="duplicates">Duplicates</option><option value="identity">Identity / matching</option><option value="paths">Paths / storage</option><option value="PLEX_IDENTITY_MISMATCH">Plex conflicts</option></Select><Select value={severityFilter} onChange={(event) => updateProblemView({ severity: event.target.value === 'all' ? null : event.target.value, problem: null, page: null })}><option value="all">All priorities</option><option value="high">High priority</option><option value="medium">Medium priority</option><option value="low">Low priority</option></Select><span>Showing {visible.length} of {total}</span></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    <section className={`problems-layout ${selected ? 'show-detail' : ''}`}>
      <div className="problems-list"><div className="problems-list-head"><span>{queueStatus === 'dismissed' ? 'Suppressed Problems' : 'Problems'}</span><span>{visible.length} shown</span></div>{visible.length ? visible.map((problem) => <button type="button" className={`problems-row ${current?.id === problem.id ? 'selected' : ''}`} onClick={() => setSelected(problem.id)} key={problem.id}><span className={`problems-severity severity-${problem.severity}`}><Icon name="alert" size={15} /></span><span className="problems-row-copy"><strong>{problem.title}</strong><span>{problem.subject}</span><small><b className={`problems-state state-${problem.workflow}`}>{problemWorkflowLabels[problem.workflow]}</b>{problem.created}</small></span><Icon name="chevron" size={15} /></button>) : <EmptyState icon={queueStatus === 'dismissed' ? 'shield' : 'check'} title={queueStatus === 'dismissed' ? 'No suppressed Problems' : 'No Problems match these filters'} detail={queueStatus === 'dismissed' ? 'Admin suppressions will appear here.' : 'Change a filter or enjoy the quiet queue.'} />}</div>
      <article className="problems-detail">{current ? <><header className="problems-detail-head"><button className="icon-button problems-back-button" type="button" onClick={() => setSelected('')} aria-label="Back to Problems"><Icon name="chevron" size={16} style={{ transform: 'rotate(180deg)' }} /></button><div><div className="eyebrow">{queueStatus === 'dismissed' ? 'SUPPRESSED' : problemWorkflowLabels[current.workflow].toUpperCase()}</div><h2>{current.title}</h2></div><details className="problems-admin-menu"><summary className="icon-button" aria-label="Administrative actions"><Icon name="menu" size={16} /></summary><div>{queueStatus === 'dismissed' ? <button onClick={() => void adminAction('restore')}>Restore to active queue</button> : <button onClick={() => void adminAction('dismiss')}>Suppress as debug/admin exception</button>}<button className="danger" onClick={() => void adminAction('delete')}>Delete problem record</button><p>Administrative actions do not fix the underlying media condition.</p></div></details></header><div className="problems-detail-body"><div className="problems-lead"><p>{current.detail}</p><div><span className={`problems-priority priority-${current.severity}`}>{current.severity} priority</span><span className={`problems-state state-${current.workflow}`}>{problemWorkflowLabels[current.workflow]}</span></div></div>
        <ProblemKeyDetails problem={current} duplicateMovie={duplicateMovie} />
        {queueStatus === 'open' && current.workflow === 'manual' && <div className="problems-resolution"><p>Delete the unwanted copy from your drive, then recheck. Medialogue will not delete media or choose a preferred copy for you.</p><Button variant="primary" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && current.workflow === 'config' && <div className="problems-resolution"><p>Correct the relevant storage or path-mapping configuration, then recheck this Problem.</p><Button variant="primary" icon="settings" onClick={() => navigate('/settings?tab=Storage%20Roots')}>Open Storage settings</Button><Button variant="ghost" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && current.code === 'PLEX_IDENTITY_MISMATCH' && !editingPlexIdentity && <div className="problems-resolution"><p>Which system needs correcting?</p><Button variant="primary" icon="external" onClick={openPlex}>Medialogue is correct — open Plex</Button>{current.availableActions?.some((action) => action === 'confirm_movie_match' || action === 'confirm_show_match') && <Button variant="ghost" onClick={beginPlexIdentityChange}>Medialogue is wrong — change identity</Button>}<Button variant="ghost" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && current.workflow === 'waiting' && current.code !== 'PLEX_IDENTITY_MISMATCH' && <div className="problems-resolution"><p>Recheck the current external and filesystem evidence. If the condition is gone, this Problem will leave the queue.</p><Button variant="primary" icon="refresh" onClick={() => void recheckCurrent()} disabled={loading}>Recheck</Button></div>}
        {queueStatus === 'open' && (current.workflow === 'choice' || editingPlexIdentity) && <ProblemsIdentityPicker problem={current} matches={tmdbMatches} selected={selectedTmdb} query={tmdbQuery} loading={loading} onQuery={setTmdbQuery} onSearch={() => void runTmdbSearch()} onSelect={setSelectedTmdb} onApply={() => void applyTmdbMatch()} onCancel={editingPlexIdentity ? () => setEditingPlexIdentity(false) : undefined} />}
        <details className="problems-evidence"><summary><Icon name="chevron" size={15} /><strong>Evidence & technical details</strong><span>For troubleshooting</span></summary><ProblemEvidenceDetails problem={current} /></details>
      </div></> : <EmptyState icon="alert" title="Select a Problem" detail="Choose an item from the queue to review its evidence and next action." />}</article>
    </section>
    {pages > 1 && <div className="pagination-bar"><Button variant="ghost" disabled={page <= 1 || loading} onClick={() => void load(page - 1)}>Previous</Button><span>Page {page} of {pages}</span><Button variant="ghost" disabled={page >= pages || loading} onClick={() => void load(page + 1)}>Next</Button></div>}
  </Page>
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
        setMessage(`Restore ${job?.state ?? 'failed'}. Review Jobs for details.`)
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
      if (!job || job.state !== 'completed') setMessage(`Archive retry ${job?.state ?? 'failed'}. Review Jobs for details.`)
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

  return <Page title="Torrent Archive" subtitle="Recovery evidence retained independently from live qBittorrent state." action={<Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button>}>
    <div className="archive-callout"><div className="archive-callout-icon"><Icon name="shield" size={22} /></div><div><strong>{health.status === 'healthy' ? 'Archive mount is healthy' : 'Archive mount needs attention'}</strong><span>{health.archived ?? items.filter((item) => item.archiveState === 'archived').length} archived · {health.missing_or_failed ?? items.filter((item) => item.archiveState !== 'archived').length} pending/failed · retention: indefinite · {health.path ?? '/torrent-archive'}</span></div><Badge tone={archiveTone}>{health.status === 'healthy' ? 'Protected' : health.status ?? 'Unknown'}</Badge></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    {restoreItem && <Panel title="Restore archived torrent" eyebrow="EXPLICIT QBITTORRENT RESTORE"><div className="settings-form"><label><span className="field-label">Torrent</span><Input value={restoreItem.torrentName} readOnly /></label><label><span className="field-label">qBittorrent client</span><Select value={restoreClientId} onChange={(event) => setRestoreClientId(event.target.value)}><option value="">Select client…</option>{eligibleClients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</Select></label><label><span className="field-label">qBittorrent save path</span><Input value={restorePath} onChange={(event) => setRestorePath(event.target.value)} placeholder="Enter the destination path explicitly" /></label></div><div className="settings-note"><Icon name="shield" size={15} /><span>The destination must resolve inside a configured {restoreItem.mediaType ?? 'media'} storage root. Medialogue does not move or import the downloaded data.</span></div><div className="settings-footer"><Button variant="ghost" onClick={() => setRestoreItem(null)}>Cancel</Button><Button variant="primary" icon="download" onClick={() => void restore()} disabled={busy || !restoreClientId || !restorePath.trim()}>{busy ? 'Submitting…' : 'Restore to qBittorrent'}</Button></div></Panel>}
    <Panel className="table-panel" title="Archived torrents" eyebrow="RECOVERY INVENTORY" action={<div className="search-field compact"><Icon name="search" size={14} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter archive…" /></div>}>
      {error ? <EmptyState title="Could not load torrent archive" detail={error} /> : !loading && !items.length ? <EmptyState icon="archive" title="No torrent recovery records yet" detail="Relevant qBittorrent torrents will be archived automatically when they are observed." /> : <table className="data-table"><thead><tr><th>Release</th><th>Media</th><th>Info hash</th><th>Source</th><th>Size</th><th>Archive</th><th>qBit</th><th /></tr></thead><tbody>{items.map((torrent) => <tr key={torrent.id}><td><strong>{torrent.releaseName ?? torrent.torrentName}</strong>{torrent.quality && <span className="table-sub">{torrent.quality}{torrent.edition ? ` · ${torrent.edition}` : ''}</span>}</td><td>{torrent.mediaTitle ?? 'Unassociated'}{torrent.tmdbId && <span className="table-sub">TMDB {torrent.tmdbId}</span>}</td><td className="mono" title={torrent.infoHash}>{shortHash(torrent.infoHash)}</td><td>{torrent.originalDownloadClient ?? 'Unknown client'}</td><td>{formatSize(torrent.totalSize)}</td><td><Badge tone={torrent.archiveState === 'archived' ? 'green' : torrent.archiveState === 'failed' ? 'red' : 'amber'}>{torrent.archiveState.replaceAll('_', ' ')}</Badge></td><td><Badge tone={torrent.qbitPresent ? 'green' : 'neutral'}>{torrent.qbitPresent ? 'Present' : 'Removed'}</Badge></td><td><div className="button-row">{torrent.archiveState === 'archived' ? <Button variant="ghost" icon="download" onClick={() => beginRestore(torrent)}>Restore</Button> : <Button variant="ghost" icon="refresh" onClick={() => void retryArchive(torrent)} disabled={busy}>Retry</Button>}</div></td></tr>)}</tbody></table>}
    </Panel>
  </Page>
}

export function CustomFormatsPage() { return <CustomFormatsPageView /> }


// The rail is grouped because nine destinations need the structure, and the
// old General tab is gone: it held no settings at all, yet it was the tab you
// landed on, so opening Settings showed you nothing you could change. Its
// safeguard notice now lives on Storage Roots, beside the access controls it
// describes.
const settingsGroups: Array<{ group: string; items: Array<{ name: string; icon: Parameters<typeof Icon>[0]['name'] }> }> = [
  { group: 'Connections', items: [
    { name: 'Plex', icon: 'server' },
    { name: 'qBittorrent', icon: 'server' },
    { name: 'Indexers', icon: 'server' },
    { name: 'Metadata', icon: 'search' },
  ] },
  { group: 'Library', items: [
    { name: 'Storage Roots', icon: 'folder' },
    { name: 'Schedules', icon: 'clock' },
  ] },
  { group: 'System', items: [
    { name: 'Security', icon: 'shield' },
    { name: 'Backup / Recovery', icon: 'shield' },
  ] },
]
const settingsTabs = settingsGroups.flatMap((section) => section.items.map((item) => item.name))

export function SettingsPage() {
  const [tab, setTab] = useUrlState('tab', 'Plex')
  const [searchParams] = useSearchParams()
  const current = settingsTabs.includes(tab) ? tab : 'Plex'
  return <Page title="Settings" subtitle="Configure integrations, storage boundaries, and safe operating defaults.">
    {searchParams.get('setup') === '1' && <div className="setup-return"><Icon name="activity" size={16} /><span>You are configuring first-run setup.</span><Link to="/setup">Back to setup checklist</Link></div>}
    <div className="split split-narrow">
      <nav className="settings-nav">{settingsGroups.map((section) => <div key={section.group}>
        <div className="nav-group">{section.group}</div>
        {section.items.map((item) => <button className={current === item.name ? 'active' : ''} onClick={() => setTab(item.name)} key={item.name}><Icon name={item.icon} size={16} />{item.name}<Icon name="chevron" size={14} /></button>)}
      </div>)}</nav>
      <section className="panel settings-panel">
        {current === 'Storage Roots' ? <StorageSettings />
          : current === 'Metadata' ? <MetadataSettings />
          : current === 'Plex' ? <PlexSettings />
          : current === 'qBittorrent' ? <QBittorrentSettings />
          : current === 'Indexers' ? <IndexerSettings />
          : current === 'Schedules' ? <ScheduleSettings />
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
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.security().then(setSecurity).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'Could not load security state.')) }, [])
  const change = async () => {
    if (newPassword.length < 12) { setMessage('New password must contain at least 12 characters.'); return }
    if (newPassword !== confirmPassword) { setMessage('New password confirmation does not match.'); return }
    setBusy(true); setMessage('Changing password…')
    try { await api.changePassword(currentPassword, newPassword); setCurrentPassword(''); setNewPassword(''); setConfirmPassword(''); setSecurity((value) => value ? { ...value, default_password_warning: false } : value); setMessage('Password changed. Other active sessions were revoked.') }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not change password.') }
    finally { setBusy(false) }
  }
  const tooShort = newPassword.length > 0 && newPassword.length < 12
  const mismatch = confirmPassword.length > 0 && newPassword !== confirmPassword
  return <>
    <SectionHead icon="shield" title="Administrator security"
      description="Medialogue uses a single local administrator account and secure session cookies. There is no self-service recovery — a lost password is reset from the server."
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
        help={<><strong>At least 12 characters.</strong> Length matters more than symbols — a passphrase of four uncommon words beats a short complex string.</>}>
        <Secret value={newPassword} onChange={(event) => setNewPassword(event.target.value)} aria-invalid={tooShort} />
      </Field>
      <Field label="Confirm new password" error={mismatch ? 'Does not match the new password.' : undefined} help="Must match exactly.">
        <Secret value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} aria-invalid={mismatch} />
      </Field>
    </div>
    <div className="settings-note"><Icon name="shield" size={15} /><span>Changing the password revokes every other active session immediately. You stay signed in on this device.</span></div>
    {message && <Note message={{ tone: message.includes('Could not') || message.includes('must') || message.includes('does not match') ? 'error' : message.endsWith('…') ? 'busy' : 'ok', text: message }} />}
    <div className="settings-footer">
      <Button variant="primary" onClick={() => void change()} disabled={busy || !currentPassword || tooShort || mismatch || !newPassword}>{busy ? 'Changing…' : 'Change password'}</Button>
    </div>
  </>
}
function ScheduleSettings() {
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [message, setMessage] = useState('')
  const load = () => api.downloadClients().then(setClients).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'Could not load polling settings.'))
  useEffect(() => { void load() }, [])
  const updateInterval = async (client: DownloadClient, seconds: number) => {
    try {
      const updated = await api.updateDownloadClient(client.id, { name: client.name, url: client.url, username: client.username, scope: client.scope, category: client.category, tags: client.tags, enabled: client.enabled, poll_interval_seconds: seconds, expected_revision: client.revision })
      setClients((items) => items.map((item) => item.id === updated.id ? updated : item)); setMessage(`${client.name} polling interval updated.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update polling interval.') }
  }
  return <>
    <SectionHead icon="clock" title="Schedules"
      description="How often each qBittorrent instance is polled for incoming downloads and completions. Polling is lightweight and set per instance."
      autosave />
    {clients.length ? <div className="root-list">{clients.map((client) => <div className="root-row" key={client.id}><div className="root-icon"><Icon name="download" size={16} /></div><div><strong>{client.name}</strong><span>{client.scope === 'movies' ? 'Movies' : 'Shows'} · {client.url}</span></div><Select value={String(client.pollIntervalSeconds ?? 30)} onChange={(event) => void updateInterval(client, Number(event.target.value))}><option value="5">Every 5 seconds</option><option value="10">Every 10 seconds</option><option value="15">Every 15 seconds</option><option value="30">Every 30 seconds</option><option value="60">Every minute</option><option value="300">Every 5 minutes</option></Select></div>)}</div> : <EmptyState icon="download" title="No polling schedules yet" detail="Add a qBittorrent client first; each client owns its actual reconciliation interval." />}
    <div className="settings-note"><Icon name="clock" size={15} /><span>A shorter interval detects completions sooner and costs one lightweight Web UI call per instance per tick. It has no effect on download speed.</span></div>
    <SectionHead icon="folder" title="Full library scans" divided
      description="Full storage-root scans are deliberately manual. Medialogue never creates a scheduled scan just because a root exists, so a fresh install stays idle and no unexpected large NAS scan can start on its own."
      status="Manual by design" statusTone="green" />
    {message && <Note message={{ tone: message.includes('Could not') ? 'error' : 'ok', text: message }} />}
  </>
}
function StorageSettings() {
  const [roots, setRoots] = useState<StorageRoot[]>([])
  const [mappings, setMappings] = useState<RemotePathMapping[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [message, setMessage] = useState('')
  const [adding, setAdding] = useState(false)
  const [addingMapping, setAddingMapping] = useState(false)
  const [name, setName] = useState('Movies')
  const [path, setPath] = useState('/media/movies')
  const [mediaType, setMediaType] = useState<'movies' | 'shows'>('movies')
  const [accessMode, setAccessMode] = useState<'read_only' | 'read_write'>('read_only')
  const [mappingName, setMappingName] = useState('qBittorrent path')
  const [mappingClientId, setMappingClientId] = useState('')
  const [remotePrefix, setRemotePrefix] = useState('/downloads')
  const [localPrefix, setLocalPrefix] = useState('/media')
  const [mappingRootId, setMappingRootId] = useState('')

  const load = async () => {
    try {
      const [nextRoots, nextMappings, nextClients] = await Promise.all([api.storageRoots(), api.remotePathMappings(), api.downloadClients()])
      setRoots(nextRoots); setMappings(nextMappings); setClients(nextClients)
      setMappingRootId((value) => value || nextRoots[0]?.id || '')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load storage configuration.') }
  }
  useEffect(() => { void load() }, [])
  const scan = async (root: StorageRoot) => {
    try {
      const initializing = !root.last_scan_at
      const job = await api.startScan(root.id)
      setMessage(`${initializing ? 'Initialization scan' : 'Scan'} queued: ${job.job_id}`)
      void api.waitForJobs([job.job_id]).then(async ([finished]) => {
        await load()
        if (finished?.state === 'completed') setMessage(initializing ? `${root.name} initialized. Automatic reconciliation may now use this root.` : `${root.name} scan completed.`)
        else if (finished) setMessage(`${initializing ? 'Initialization scan' : 'Scan'} ${finished.state}. The root ${initializing ? 'remains uninitialized' : 'was not fully refreshed'}.`)
      }).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'Could not track scan completion.'))
    }
    catch (reason) {
      // Scanning is gated on TMDB because it establishes the identity of
      // everything discovered. Point at the fix rather than restating the code.
      if (reason instanceof ApiError && reason.code === 'TMDB_NOT_CONFIGURED') {
        setMessage('Configure TMDB before scanning — Medialogue cannot identify discovered media without it. Settings → Metadata.')
      } else setMessage(reason instanceof Error ? reason.message : 'Could not start scan.')
    }
  }
  const addRoot = async () => {
    try {
      const created = await api.createStorageRoot({ name, path, media_type: mediaType, access_mode: accessMode })
      setRoots((items) => [...items, created]); setAdding(false); setMappingRootId((value) => value || created.id); setMessage(`${created.name} added. Press Initialize & scan once before Medialogue will include this root in automatic reconciliation.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not add root.') }
  }
  const removeRoot = async (root: StorageRoot) => {
    if (!window.confirm(`Remove storage root “${root.name}”?\n\n${root.resolved_root_path}\n\nThis removes only the configured root. Existing media files are never deleted, and previously observed paths remain in Medialogue as detached history.`)) return
    try {
      await api.deleteStorageRoot(root.id)
      setRoots((items) => items.filter((item) => item.id !== root.id))
      setMappings((items) => items.map((item) => item.storage_root_id === root.id ? { ...item, storage_root_id: undefined } : item))
      setMappingRootId((value) => value === root.id ? '' : value)
      setMessage(`${root.name} removed. Media files were untouched.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not remove storage root.') }
  }
  const addMapping = async () => {
    if (!remotePrefix.trim() || !localPrefix.trim()) { setMessage('Remote and local prefixes are required.'); return }
    try {
      const created = await api.createRemotePathMapping({
        name: mappingName.trim() || 'qBittorrent path',
        integration_type: 'qbittorrent',
        integration_id: mappingClientId || undefined,
        remote_prefix: remotePrefix.trim(),
        local_prefix: localPrefix.trim(),
        storage_root_id: mappingRootId || undefined,
        enabled: true,
      })
      setMappings((items) => [...items, created]); setAddingMapping(false); setMessage('Remote path mapping added. Recheck the affected Problem after the next qBittorrent observation.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not add remote path mapping.') }
  }
  const removeMapping = async (mapping: RemotePathMapping) => {
    try { await api.deleteRemotePathMapping(mapping.id); setMappings((items) => items.filter((item) => item.id !== mapping.id)); setMessage(`${mapping.name} removed.`) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not remove remote path mapping.') }
  }

  const rootRows = roots.map((root) => {
    const initialized = Boolean(root.last_scan_at)
    const health = (root.last_health ?? 'unchecked').toLowerCase()
    const offline = initialized && (health === 'offline' || health === 'unavailable')
    const affected = root.affected_media_count ?? root.media_affected ?? 0
    return <div className={`root-row ${offline ? 'root-row-offline' : ''}`} key={root.id}>
      <div className="root-icon"><Icon name="folder" size={17} /></div>
      <div><strong>{root.name}</strong><span>{root.resolved_root_path}</span>
        {!initialized && <small className="root-outage-copy">Not initialized · Medialogue will not scan or reconcile this root automatically</small>}
        {offline && <small className="root-outage-copy">Storage Root Offline · {affected} media affected</small>}
      </div>
      <Badge tone={!initialized ? 'amber' : health === 'available' || health === 'healthy' ? 'green' : offline ? 'red' : health === 'degraded' ? 'amber' : 'neutral'}>{!initialized ? 'Not initialized' : root.last_health ?? 'Unchecked'}</Badge>
      <Badge tone="neutral">{root.access_mode === 'read_only' ? 'Read-only' : 'Read/write'}</Badge>
      <span className="root-items">{root.media_type}{root.missing_media_count !== undefined ? ` · ${root.missing_media_count} missing` : ''}</span>
      <Button variant={initialized ? 'ghost' : 'primary'} icon="play" onClick={() => scan(root)}>{initialized ? 'Scan now' : 'Initialize & scan'}</Button>
      <Button variant="danger" onClick={() => void removeRoot(root)}>Remove</Button>
    </div>
  })

  return <>
    <SectionHead icon="folder" title="Storage roots"
      description="The paths Medialogue is allowed to read. Media is never moved or renamed — a root only tells Medialogue where to look. A new root stays idle until you scan it once."
      action={<Button variant={adding ? 'ghost' : 'primary'} icon="plus" onClick={() => setAdding((value) => !value)}>{adding ? 'Cancel' : 'Add root'}</Button>} />
    <div className="root-list">{rootRows}{!roots.length && <EmptyState title="No storage roots configured" detail="Add an explicit container-visible Movie or Show root. It will remain idle until you initialize it with its first scan." />}</div>

    {adding && <>
      <SectionHead icon="plus" title="Add a storage root" divided
        description="Adding a root does not scan it. You initialize the first scan explicitly, so a fresh install never starts a large NAS walk on its own." />
      <div className="settings-form">
        <Field label="Name" help="Shown in Medialogue only. Has no effect on the filesystem.">
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Container path" help={<>The path <strong>as Medialogue sees it</strong>. Inside Docker this is the container-side mount, not the host path. Example: <code>/media/movies</code></>}>
          <Input value={path} onChange={(event) => setPath(event.target.value)} />
        </Field>
        <Field label="Media type" help="Which library titles discovered under this root belong to.">
          <Select value={mediaType} onChange={(event) => setMediaType(event.target.value as 'movies' | 'shows')}><option value="movies">Movies</option><option value="shows">Shows</option></Select>
        </Field>
        <Field label="Access" help={<><strong>Read-only</strong> detects and reports but can never delete. <strong>Read/write</strong> additionally permits deletion, and only after an explicit preview and confirmation.</>}>
          <Select value={accessMode} onChange={(event) => setAccessMode(event.target.value as 'read_only' | 'read_write')}><option value="read_only">Read-only — detection only</option><option value="read_write">Read/write — allow explicit confirmed deletion</option></Select>
        </Field>
      </div>
      <div className="settings-footer"><Button variant="primary" onClick={addRoot}>Save root</Button></div>
    </>}

    <SectionHead icon="arrow" title="Remote path mappings" divided
      description="Only needed when qBittorrent reports a path that does not exist from Medialogue's point of view — typically a different container, or a different host entirely."
      action={<Button variant="ghost" icon="plus" onClick={() => setAddingMapping((value) => !value)}>{addingMapping ? 'Cancel' : 'Add mapping'}</Button>} />
    <div className="root-list">{mappings.map((mapping) => {
      const client = clients.find((item) => item.id === mapping.integration_id)
      const root = roots.find((item) => item.id === mapping.storage_root_id)
      return <div className="root-row" key={mapping.id}>
        <div className="root-icon"><Icon name="arrow" size={17} /></div>
        <div><strong>{mapping.name}</strong><span>{mapping.remote_prefix} → {mapping.local_prefix}</span><small>{client?.name ?? 'All qBittorrent clients'} · {root ? `root: ${root.name}` : 'all matching roots'}</small></div>
        <Badge tone={mapping.enabled ? 'green' : 'neutral'}>{mapping.enabled ? 'Enabled' : 'Disabled'}</Badge>
        <span className="root-items">qBittorrent</span>
        <Button variant="ghost" onClick={() => void removeMapping(mapping)}>Remove</Button>
      </div>
    })}{!mappings.length && <EmptyState title="No remote path mappings" detail="Only add one when qBittorrent reports a path that differs from the media path visible inside Medialogue." />}</div>

    {addingMapping && <>
    <div className="settings-form">
      <Field label="Name" help="Shown in Medialogue only.">
        <Input value={mappingName} onChange={(event) => setMappingName(event.target.value)} />
      </Field>
      <Field label="qBittorrent client" help="Which client's reported paths this mapping rewrites. Leave as all clients when every instance uses the same layout.">
        <Select value={mappingClientId} onChange={(event) => setMappingClientId(event.target.value)}><option value="">All qBittorrent clients</option>{clients.map((client) => <option value={client.id} key={client.id}>{client.name}</option>)}</Select>
      </Field>
      <Field label="Remote prefix reported by qBittorrent" help="The path prefix exactly as qBittorrent reports it. Copy it from a download's save path. Do not map a parent path that also contains libraries Medialogue cannot reach.">
        <Input value={remotePrefix} onChange={(event) => setRemotePrefix(event.target.value)} placeholder="/downloads/movies" />
      </Field>
      <Field label="Local/container prefix" help="What that prefix corresponds to from Medialogue's point of view.">
        <Input value={localPrefix} onChange={(event) => setLocalPrefix(event.target.value)} placeholder="/media/movies" />
      </Field>
      <Field label="Storage root" badge="optional" wide help="Optionally pin the rewritten path to one root. Leave unset to let Medialogue resolve it against every matching root.">
        <Select value={mappingRootId} onChange={(event) => setMappingRootId(event.target.value)}><option value="">No explicit root</option>{roots.map((root) => <option value={root.id} key={root.id}>{root.name} · {root.resolved_root_path}</option>)}</Select>
      </Field>
    </div>
    <div className="settings-footer"><Button variant="primary" onClick={addMapping}>Save mapping</Button></div>
    </>}

    {message && <Note message={{ tone: message.includes('Could not') || message.includes('failed') ? 'error' : 'ok', text: message }} />}
    <div className="settings-note"><Icon name="shield" size={15} /><span>Scans and path mappings never move or rename media. Removing a root never touches the files on it — only Medialogue's index of that root is cleared.</span></div>
  </>
}

function MetadataSettings() {
  const [configuration, setConfiguration] = useState<{ configured: boolean; api_key_configured: boolean; enabled: boolean; health: string; latency_ms?: number; last_error?: string; revision?: number } | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<Message>(null)
  const [tested, setTested] = useState<{ text: string; tone: 'ok' | 'err' } | null>(null)

  const load = () => api.tmdbConfiguration().then((value) => { setConfiguration(value); setEnabled(value.enabled); setApiKey(''); setDirty(false) })
  useEffect(() => { load().catch((reason) => setMessage(failed(reason, 'Could not load TMDB settings.'))) }, [])

  const save = async () => {
    setSaving(true); setMessage(pending('Saving TMDB settings…'))
    try {
      const value = await api.saveTmdb({ api_key: apiKey || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setApiKey(''); setDirty(false); setMessage(ok('TMDB settings saved.'))
    } catch (reason) { setMessage(failed(reason, 'Could not save TMDB settings.')) } finally { setSaving(false) }
  }
  const test = async () => {
    setTested(null); setMessage(pending('Testing TMDB connection…'))
    try {
      const value = await api.testTmdb({ api_key: apiKey || undefined })
      const reachable = value.status === 'healthy'
      setTested({ tone: reachable ? 'ok' : 'err', text: reachable ? `tested just now${value.latency_ms ? ` · ${value.latency_ms} ms` : ''}` : value.message ?? value.status })
      setMessage(null)
    } catch (reason) { setTested({ tone: 'err', text: 'test failed' }); setMessage(failed(reason, 'Could not test TMDB.')) }
  }

  const healthTone: StatusTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : 'neutral'
  return <>
    <SectionHead icon="search" title="TMDB metadata"
      description="TMDB is the primary identity source for newly discovered titles. Local filename parsing alone never creates a matched title — without TMDB, discoveries stay unmatched."
      status={!configuration?.configured ? 'Not configured' : configuration.health} statusTone={healthTone}
      detail={tested?.text} detailTone={tested?.tone}
      action={<Button variant="secondary" onClick={() => void test()} disabled={saving}>Test connection</Button>} />
    <div className="settings-form">
      <Field label="API key" badge={configuration?.api_key_configured ? 'stored' : undefined}
        help={<>Your TMDB API Read Access Token. Held server-side and never sent back to the browser. {configuration?.api_key_configured ? 'Leave blank to keep the current one.' : 'Required for automatic matching.'}</>}>
        <Secret value={apiKey} onChange={(event) => { setApiKey(event.target.value); setDirty(true) }} placeholder={configuration?.api_key_configured ? 'Stored server-side · leave blank to preserve' : 'Required for automatic matching'} />
      </Field>
      <Field label="Enabled" help="When off, no new title is matched automatically and discoveries queue as Problems for manual matching. Existing matches are kept.">
        <div className="setting-control">
          <button type="button" aria-pressed={enabled} className="toggle" onClick={() => { setEnabled((value) => !value); setDirty(true) }}><span /></button>
          <span className="toggle-label">{enabled ? 'Enabled' : 'Disabled'}</span>
        </div>
      </Field>
    </div>
    {configuration?.last_error && <Note message={{ tone: 'error', text: configuration.last_error }} />}
    <Note message={message} />
    <SaveFooter dirty={dirty} saving={saving} onSave={() => void save()} onRevert={() => void load()} />
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
  const [tested, setTested] = useState<{ text: string; tone: 'ok' | 'err' } | null>(null)

  const load = () => api.plexConfiguration().then((value) => {
    setConfiguration(value)
    if (value.url) setUrl(value.url)
    setEnabled(value.enabled)
    setToken(''); setDirty(false)
  })
  useEffect(() => { load().catch((reason) => setMessage(failed(reason, 'Could not load Plex settings.'))).finally(() => setLoading(false)) }, [])

  const test = async () => {
    setWorking(true); setTested(null); setMessage(pending('Testing Plex connection…'))
    try {
      const result = await api.testPlex({ url: url || undefined, token: token || undefined })
      const reachable = result.status === 'healthy'
      setTested({ tone: reachable ? 'ok' : 'err', text: reachable ? `tested just now${result.latency_ms ? ` · ${result.latency_ms} ms` : ''}` : result.message ?? result.status })
      setMessage(null)
    } catch (reason) { setTested({ tone: 'err', text: 'test failed' }); setMessage(failed(reason, 'Could not test Plex.')) }
    finally { setWorking(false) }
  }
  const save = async () => {
    if (!url.trim()) { setMessage({ tone: 'error', text: 'Enter a Plex server URL.' }); return }
    setWorking(true); setMessage(pending('Saving Plex settings…'))
    try {
      const value = await api.savePlex({ url: url.trim(), token: token || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setToken(''); setDirty(false); setMessage(ok('Plex settings saved.'))
    } catch (reason) { setMessage(failed(reason, 'Could not save Plex settings.')) }
    finally { setWorking(false) }
  }
  const refreshHealth = async () => {
    setWorking(true); setMessage(pending('Refreshing Plex health…'))
    try {
      const result = await api.refreshPlexHealth()
      setConfiguration((value) => value ? { ...value, health: result.status, machine_identifier: result.machine_identifier, latency_ms: result.latency_ms, last_error: result.message } : value)
      setMessage(result.status === 'healthy' ? ok('Plex health is healthy.') : { tone: 'error', text: result.message ?? `Plex status: ${result.status}.` })
    } catch (reason) { setMessage(failed(reason, 'Could not refresh Plex health.')) }
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

  const healthTone: StatusTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : configuration?.health === 'degraded' ? 'amber' : 'neutral'
  const live = configuration?.configured && configuration.enabled
  return <>
    <SectionHead icon="tv" title="Plex connection"
      description="Read-only verification. Medialogue checks exact paths first, then storage-root-relative paths across Docker mount prefixes, and only then falls back to matching on title and year."
      status={loading ? 'Loading' : !configuration?.configured ? 'Not configured' : configuration.health} statusTone={healthTone}
      detail={tested?.text} detailTone={tested?.tone}
      action={<Button variant="secondary" onClick={() => void test()} disabled={working}>{working ? 'Working…' : 'Test connection'}</Button>} />
    <div className="settings-form">
      <Field label="URL" help={<>Base address of your Plex Media Server, including port. Example: <code>http://plex:32400</code></>}>
        <Input placeholder="http://plex:32400" value={url} onChange={(event) => { setUrl(event.target.value); setDirty(true) }} />
      </Field>
      <Field label="API token" badge={configuration?.token_configured ? 'stored' : undefined}
        help={<>Your <code>X-Plex-Token</code>. Held server-side and never sent back to the browser. {configuration?.token_configured ? 'Leave blank to keep the current one.' : 'Required.'}</>}>
        <Secret placeholder={configuration?.token_configured ? 'Stored server-side · leave blank to preserve' : 'Required'} value={token} onChange={(event) => { setToken(event.target.value); setDirty(true) }} />
      </Field>
      <Field label="Enabled" wide help="When off, Plex is never contacted and Plex state on every title reads as unknown. Nothing inside Plex is modified either way.">
        <div className="setting-control">
          <button type="button" aria-pressed={enabled} className="toggle" onClick={() => { setEnabled((value) => !value); setDirty(true) }}><span /></button>
          <span className="toggle-label">{enabled ? 'Enabled' : 'Disabled'}</span>
        </div>
      </Field>
    </div>
    {configuration?.last_error && <Note message={{ tone: 'error', text: configuration.last_error }} />}
    <Note message={message} />
    <div className="settings-note"><Icon name="tv" size={15} /><span>A manual storage-root scan automatically queues a read-only Plex verification for that root. “Sync library verification” checks everything immediately without asking Plex to rescan.</span></div>
    <SaveFooter dirty={dirty} saving={working} onSave={() => void save()} onRevert={() => void load()}>
      {live && <Button variant="ghost" onClick={() => void refreshHealth()} disabled={working}>Refresh health</Button>}
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
  }
}

function QBittorrentSettings() {
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [selectedId, setSelectedId] = useUrlState('client')
  const [draft, setDraft] = useState<DownloadClientDraft>(emptyDownloadClient)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)

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
    setMessage(''); setDirty(false)
  }
  const updateDraft = <K extends keyof DownloadClientDraft>(key: K, value: DownloadClientDraft[K]) => { setDirty(true); setDraft((current) => ({ ...current, [key]: value })) }
  const payload = () => ({
    name: draft.name.trim(),
    url: draft.url.trim(),
    username: draft.username.trim() || undefined,
    ...(draft.password ? { password: draft.password } : {}),
    scope: draft.scope,
    category: draft.category.trim() || undefined,
    tags: draft.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
    enabled: draft.enabled,
    poll_interval_seconds: draft.pollIntervalSeconds,
  })

  const save = async () => {
    if (!draft.name.trim() || !draft.url.trim()) { setError('Name and URL are required.'); return }
    setBusy(true); setError(''); setMessage('Saving qBittorrent client…')
    try {
      const value = selectedId ? await api.updateDownloadClient(selectedId, { ...payload(), expected_revision: clients.find((item) => item.id === selectedId)?.revision }) : await api.createDownloadClient(payload())
      setClients((items) => selectedId ? items.map((item) => item.id === value.id ? value : item) : [...items, value])
      setSelectedId(value.id); setDraft(draftFromDownloadClient(value)); setDirty(false); setMessage(`${value.name} saved. Password is stored server-side and never returned to the browser.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save qBittorrent client.'); setMessage('') }
    finally { setBusy(false) }
  }
  const test = async () => {
    if (!draft.url.trim()) { setError('URL is required.'); return }
    if (!selectedId && !draft.password) { setError('Password is required to test a new client.'); return }
    setBusy(true); setError(''); setMessage('Testing qBittorrent connection…')
    try {
      const testConfiguration = {
        url: draft.url.trim(),
        username: draft.username,
        ...(draft.password ? { password: draft.password } : {}),
      }
      const result = await api.testDownloadClient(selectedId ?? undefined, testConfiguration)
      setMessage(result.status === 'healthy' ? `qBittorrent is reachable${result.version ? ` · ${result.version}` : ''}${result.latency_ms ? ` · ${result.latency_ms} ms` : ''}.` : result.message ?? `qBittorrent status: ${result.status}.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not test qBittorrent.'); setMessage('') }
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
  const remove = async () => {
    if (!selectedId || !window.confirm('Remove this qBittorrent client configuration? Existing torrent history is preserved.')) return
    setBusy(true); setError('')
    try {
      await api.deleteDownloadClient(selectedId)
      const remaining = clients.filter((item) => item.id !== selectedId)
      setClients(remaining); const next = remaining[0]
      setSelectedId(next?.id ?? ''); setDraft(next ? draftFromDownloadClient(next) : emptyDownloadClient); setMessage('Client configuration removed; torrent history was not changed.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove qBittorrent client.') }
    finally { setBusy(false) }
  }
  const selected = clients.find((item) => item.id === selectedId)
  const healthTone: StatusTone = selected?.health === 'healthy' ? 'green' : selected?.health === 'unavailable' || selected?.health === 'offline' ? 'red' : selected?.health === 'degraded' ? 'amber' : 'neutral'
  const connectionDetail = selected ? `${selected.health}${selected.latency_ms !== undefined ? ` · ${selected.latency_ms} ms` : ''}${selected.last_success_at ? ` · last success ${formatEvidenceDate(selected.last_success_at)}` : selected.last_checked_at ? ` · checked ${formatEvidenceDate(selected.last_checked_at)}` : ''}` : undefined
  return <div className="qbit-settings">
    <div className="split split-flush">
      <div className="qbit-client-list">
        <div className="qbit-list-head"><span className="eyebrow">DOWNLOAD CLIENTS</span><Button variant="ghost" icon="plus" onClick={() => { setSelectedId(''); setDraft(emptyDownloadClient); setDirty(false); setMessage(''); setError('') }}>Add client</Button></div>
        {clients.map((client) => <button className={`qbit-client-item ${selectedId === client.id ? 'selected' : ''}`} key={client.id} onClick={() => selectClient(client.id)}><span className={`health-dot ${client.health === 'healthy' ? 'green' : client.health === 'unavailable' ? 'red' : 'amber'}`} /><span><strong>{client.name}</strong><small>{client.scope === 'movies' ? 'Movies' : 'Shows'} · {client.url}</small></span><Badge tone={client.enabled ? 'green' : 'neutral'}>{client.enabled ? 'On' : 'Off'}</Badge></button>)}
        {!clients.length && !loading && <EmptyState icon="download" title="No qBittorrent clients" detail="Add a client to observe downloads." />}
      </div>

      <div className="qbit-editor">
        <SectionHead icon="download" title={selected ? selected.name : 'Add qBittorrent client'}
          description="Medialogue reads every torrent in this client. It only ever adds torrents you explicitly grab, and never moves or renames what is already there."
          status={loading ? 'Loading' : selected ? selected.health : 'New client'} statusTone={selected ? healthTone : 'neutral'}
          detail={connectionDetail} detailTone={selected?.health === 'healthy' ? 'ok' : selected?.last_error ? 'err' : undefined}
          action={<Button variant="secondary" onClick={test} disabled={busy}>{busy ? 'Working…' : 'Test connection'}</Button>} />

        <div className="settings-form">
          <Field label="Display name" help="Shown in Medialogue only. Has no effect on qBittorrent.">
            <Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} placeholder="qbit-movies-1" />
          </Field>
          <Field label="URL" help={<>Base address of the qBittorrent Web UI, including port. Example: <code>http://qbittorrent:8080</code></>}>
            <Input value={draft.url} onChange={(event) => updateDraft('url', event.target.value)} placeholder="http://qbittorrent:8080" />
          </Field>
          <Field label="Username" help="Web UI account. Leave blank if this host bypasses authentication for your network.">
            <Input value={draft.username} onChange={(event) => updateDraft('username', event.target.value)} autoComplete="off" />
          </Field>
          <Field label="Password" badge={selected?.password_configured ? 'stored' : undefined}
            help={selected?.password_configured ? 'Held server-side and never sent back to the browser. Leave blank to keep the current one.' : 'Held server-side and never sent back to the browser.'}>
            <Secret value={draft.password} onChange={(event) => updateDraft('password', event.target.value)} placeholder={selected?.password_configured ? 'Stored server-side · leave blank to preserve' : 'Required for authenticated clients'} />
          </Field>
          <Field label="Scope" help="Which library this client receives grabs for. Observation is unaffected — every client is always read.">
            <Select value={draft.scope} onChange={(event) => updateDraft('scope', event.target.value as DownloadClientDraft['scope'])}><option value="movies">Movies</option><option value="shows">Shows</option></Select>
          </Field>
          <Field label="Category" badge="optional" help={<>Category assigned to torrents Medialogue adds. It does <strong>not</strong> filter what Medialogue observes — every torrent in this client is read regardless.</>}>
            <Input value={draft.category} onChange={(event) => updateDraft('category', event.target.value)} placeholder="media" />
          </Field>
          <Field label="Tags" badge="optional" help="qBittorrent tags applied to torrents Medialogue adds. Comma-separated.">
            <Input value={draft.tags} onChange={(event) => updateDraft('tags', event.target.value)} placeholder="movies, managed" />
          </Field>
          <Field label="Polling interval" help={<>How often this client is polled for progress and completions. A shorter interval notices completions sooner and costs one lightweight Web UI call per tick. It has no effect on download speed.</>}>
            <Select value={String(draft.pollIntervalSeconds)} onChange={(event) => updateDraft('pollIntervalSeconds', Number(event.target.value))}><option value="5">5 seconds</option><option value="10">10 seconds</option><option value="15">15 seconds</option><option value="30">30 seconds</option><option value="60">1 minute</option><option value="300">5 minutes</option></Select>
          </Field>
          <Field label="Enabled" wide help="When off, this client is neither polled nor offered as a grab target. Existing torrents are left untouched.">
            <div className="setting-control">
              <button type="button" aria-pressed={draft.enabled} className="toggle" onClick={() => updateDraft('enabled', !draft.enabled)}><span /></button>
              <span className="toggle-label">{draft.enabled ? 'Enabled' : 'Disabled'}</span>
            </div>
          </Field>
        </div>

        {selected?.last_error && <Note message={{ tone: 'error', text: `Last connection error: ${selected.last_error}` }} />}
        {error && <Note message={{ tone: 'error', text: error }} />}
        {message && <Note message={{ tone: 'ok', text: message }} />}

        <SaveFooter dirty={dirty} saving={busy} onSave={save} onRevert={() => void loadClients(true)}>
          {selected && <Button variant="ghost" onClick={refresh} disabled={busy}>Refresh health</Button>}
          {selected && <Button variant="danger" onClick={remove} disabled={busy}>Delete</Button>}
        </SaveFooter>
      </div>
    </div>
  </div>
}

type IndexerDraft = {
  name: string
  torznabUrl: string
  apiKey: string
  scope: IndexerScope
  enabled: boolean
  timeoutSeconds: number
}

const emptyIndexer: IndexerDraft = {
  name: '',
  torznabUrl: '',
  apiKey: '',
  scope: 'both',
  enabled: true,
  timeoutSeconds: 15,
}

function draftFromIndexer(indexer: Indexer): IndexerDraft {
  return {
    name: indexer.name,
    torznabUrl: indexer.torznabUrl,
    apiKey: '',
    scope: indexer.scope,
    enabled: indexer.enabled,
    timeoutSeconds: indexer.timeoutSeconds || 15,
  }
}

function IndexerSettings() {
  const [indexers, setIndexers] = useState<Indexer[]>([])
  const [selectedId, setSelectedId] = useUrlState('indexer')
  const [draft, setDraft] = useState<IndexerDraft>(emptyIndexer)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [dirty, setDirty] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const items = await api.indexers()
      setIndexers(items)
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
    setDirty(false)
    setMessage('')
    setError('')
  }
  const updateDraft = <K extends keyof IndexerDraft>(key: K, value: IndexerDraft[K]) => { setDirty(true); setDraft((current) => ({ ...current, [key]: value })) }

  const save = async () => {
    if (!draft.name.trim() || !draft.torznabUrl.trim()) { setError('Name and Torznab URL are required.'); return }
    if (!selectedId && !draft.apiKey.trim()) { setError('API key is required for a new indexer.'); return }
    setBusy(true); setError(''); setMessage('Saving indexer…')
    try {
      const base = {
        name: draft.name.trim(),
        torznab_url: draft.torznabUrl.trim(),
        scope: draft.scope,
        enabled: draft.enabled,
        timeout_seconds: draft.timeoutSeconds,
      }
      const value = selectedId
        ? await api.updateIndexer(selectedId, { ...base, ...(draft.apiKey.trim() ? { api_key: draft.apiKey.trim() } : {}), expected_revision: selected?.revision })
        : await api.createIndexer({ ...base, api_key: draft.apiKey.trim() })
      setIndexers((items) => selectedId ? items.map((item) => item.id === value.id ? value : item) : [...items, value].sort((a, b) => a.name.localeCompare(b.name)))
      setSelectedId(value.id)
      setDraft(draftFromIndexer(value))
      setMessage(`${value.name} saved. The API key is stored server-side and never returned to the browser.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save indexer.')
      setMessage('')
    } finally { setBusy(false) }
  }

  const test = async () => {
    if (!selectedId && (!draft.torznabUrl.trim() || !draft.apiKey.trim())) { setError('Torznab URL and API key are required to test a new indexer.'); return }
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

  const remove = async () => {
    if (!selectedId || !window.confirm('Remove this indexer configuration? Existing search history and selected-result evidence are preserved where applicable.')) return
    setBusy(true); setError('')
    try {
      await api.deleteIndexer(selectedId)
      const remaining = indexers.filter((item) => item.id !== selectedId)
      setIndexers(remaining)
      const next = remaining[0]
      setSelectedId(next?.id ?? '')
      setDraft(next ? draftFromIndexer(next) : emptyIndexer)
      setMessage('Indexer configuration removed.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove indexer.') }
    finally { setBusy(false) }
  }

  const healthTone: StatusTone = selected?.health === 'healthy' ? 'green' : selected?.health === 'unavailable' || selected?.health === 'offline' ? 'red' : selected?.health === 'degraded' ? 'amber' : 'neutral'
  const indexerDetail = selected ? `${selected.health}${selected.latencyMs !== undefined ? ` · ${selected.latencyMs} ms` : ''}` : undefined
  return <div className="qbit-settings indexer-settings">
    <div className="split split-flush">
      <div className="qbit-client-list">
        <div className="qbit-list-head"><span className="eyebrow">INDEXERS</span><Button variant="ghost" icon="plus" onClick={() => { setSelectedId(''); setDraft(emptyIndexer); setDirty(false); setMessage(''); setError('') }}>Add indexer</Button></div>
        {indexers.map((indexer) => <button className={`qbit-client-item ${selectedId === indexer.id ? 'selected' : ''}`} key={indexer.id} onClick={() => selectIndexer(indexer.id)}><span className={`health-dot ${indexer.health === 'healthy' ? 'green' : indexer.health === 'unavailable' ? 'red' : 'amber'}`} /><span><strong>{indexer.name}</strong><small>{indexer.torznabUrl}</small></span><Badge tone={indexer.enabled ? 'green' : 'neutral'}>{indexer.enabled ? 'On' : 'Off'}</Badge></button>)}
        {!indexers.length && !loading && <EmptyState icon="search" title="No indexers configured" detail="Add a Torznab endpoint to run interactive searches." />}
      </div>

      <div className="qbit-editor">
        <SectionHead icon="search" title={selected ? selected.name : 'Add indexer'}
          description="A single Torznab endpoint. Medialogue does not import Prowlarr configuration automatically — each endpoint is added here by hand."
          status={loading ? 'Loading' : selected ? selected.health : 'New indexer'} statusTone={selected ? healthTone : 'neutral'}
          detail={indexerDetail} detailTone={selected?.health === 'healthy' ? 'ok' : selected?.lastError ? 'err' : undefined}
          action={<Button variant="secondary" onClick={test} disabled={busy}>{busy ? 'Working…' : 'Test connection'}</Button>} />

        <div className="settings-form">
          <Field label="Display name" help="Shown in Medialogue and on every search result from this indexer.">
            <Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} placeholder="TorrentLeech" />
          </Field>
          <Field label="Scope" help="Which searches query this indexer. A movie search never contacts a Shows-only indexer.">
            <Select value={draft.scope} onChange={(event) => updateDraft('scope', event.target.value as IndexerScope)}><option value="both">Movies + Shows</option><option value="movies">Movies</option><option value="shows">Shows</option></Select>
          </Field>
          <Field label="Torznab URL" wide help={<>Full Torznab endpoint, <strong>including the trailing <code>/api</code></strong>. In Prowlarr this is the per-indexer URL, not the base address. Example: <code>https://prowlarr.host/1/api</code></>}>
            <Input value={draft.torznabUrl} onChange={(event) => updateDraft('torznabUrl', event.target.value)} placeholder="https://prowlarr.host/1/api" />
          </Field>
          <Field label="API key" badge={selected?.apiKeyConfigured ? 'stored' : undefined}
            help={selected?.apiKeyConfigured ? 'The indexer API key from Prowlarr. Held server-side and never sent back to the browser. Leave blank to keep the current one.' : 'The indexer API key from Prowlarr. Held server-side and never sent back to the browser.'}>
            <Secret value={draft.apiKey} onChange={(event) => updateDraft('apiKey', event.target.value)} placeholder={selected?.apiKeyConfigured ? 'Stored server-side · leave blank to preserve' : 'Required'} />
          </Field>
          <Field label="Timeout" help="How long to wait for a search response before this indexer is skipped. The remaining indexers still return their results.">
            <Select value={String(draft.timeoutSeconds)} onChange={(event) => updateDraft('timeoutSeconds', Number(event.target.value))}><option value="10">10 seconds</option><option value="15">15 seconds</option><option value="20">20 seconds</option><option value="30">30 seconds</option></Select>
          </Field>
          <Field label="Enabled" wide help="When off, this indexer is excluded from every search. Its configuration is kept.">
            <div className="setting-control">
              <button type="button" aria-pressed={draft.enabled} className="toggle" onClick={() => updateDraft('enabled', !draft.enabled)}><span /></button>
              <span className="toggle-label">{draft.enabled ? 'Enabled' : 'Disabled'}</span>
            </div>
          </Field>
        </div>

        {selected?.lastError && <Note message={{ tone: 'error', text: `Last connection error: ${selected.lastError}` }} />}
        {error && <Note message={{ tone: 'error', text: error }} />}
        {message && <Note message={{ tone: 'ok', text: message }} />}

        <SaveFooter dirty={dirty} saving={busy} onSave={save} onRevert={() => void load()}>
          {selected && <Button variant="danger" onClick={remove} disabled={busy}>Delete</Button>}
        </SaveFooter>
      </div>
    </div>
  </div>
}

function BackupRecoverySettings() {
  const [capabilities, setCapabilities] = useState<RecoveryCapabilities | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const loadCapabilities = async () => {
    try { setCapabilities(await api.recoveryCapabilities()); setMessage('') }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not inspect Recovery Bundle capabilities.') }
  }
  useEffect(() => { void loadCapabilities() }, [])
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.state)) return
    let alive = true
    const refresh = async () => {
      try { const value = await api.job(job.id); if (alive) setJob(value) } catch { /* Jobs remain recoverable from the global drawer. */ }
    }
    const timer = window.setInterval(() => void refresh(), 1500)
    return () => { alive = false; window.clearInterval(timer) }
  }, [job?.id, job?.state])

  const start = async () => {
    setBusy(true); setMessage('')
    try {
      const accepted = await api.startRecoveryExport()
      const value = await api.job(accepted.job_id)
      setJob(value)
      setMessage(accepted.warning)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start Recovery Bundle export.') }
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
    <SectionHead icon="shield" title="Recovery Bundle"
      description="Exports the database, torrent recovery evidence, application configuration, and a human-readable library inventory as a single ZIP."
      status={capabilities?.supported ? 'Ready' : capabilities ? 'Unavailable' : 'Checking'}
      statusTone={capabilities?.supported ? 'green' : capabilities ? 'red' : 'neutral'} />

    <div className="settings-section">
      <div><h3>What the bundle contains</h3><p>Enough to rebuild this installation on a new host. Your media is <strong>not</strong> included — the bundle describes your library, it does not copy it. Intended for disaster recovery and manual verification, not automatic mass redownload.</p></div>
      <div className="recovery-content-list"><span>database/physical-base-backup/</span><span>torrent-archive/</span><span>manifests/</span><span>config/application-config-export.json</span><span>inventory/library-inventory.json</span><span>inventory/torrent-archive-inventory.json</span><span>backup-metadata.json</span></div>
    </div>

    <Note message={{ tone: 'error', text: 'The bundle contains a physical PostgreSQL base backup and integration credentials. Treat anyone holding the ZIP as having full access to your Medialogue configuration and database.' }} />

    {capabilities && <div className="settings-section">
      <div><h3>Backup compatibility</h3><p>Medialogue uses PostgreSQL-supported physical backup tooling. It never recursively copies a live PGDATA directory.</p></div>
      <div className="settings-form">
        <Field label="PostgreSQL server" help="Detected server version the bundle will be restorable against."><Input readOnly value={capabilities.postgresServerVersion ?? capabilities.databaseBackend} /></Field>
        <Field label="pg_basebackup" help="Version of the tool used to take the physical backup. Must be present for export to run."><Input readOnly value={capabilities.pgBasebackupVersion ?? 'Unavailable'} /></Field>
        <Field label="Migration revision" help="Schema revision captured in the bundle. Restore onto this revision or later."><Input readOnly value={capabilities.migrationRevision ?? 'Unknown'} /></Field>
        <Field label="Temporary download retention" help="How long a finished bundle stays downloadable before it is deleted from the server."><Input readOnly value={`${capabilities.retentionHours} hours`} /></Field>
      </div>
    </div>}
    {capabilities?.reasons.length ? <Note message={{ tone: 'error', text: capabilities.reasons.join(' ') }} /> : null}

    {job && <div className="settings-section">
      <div><h3>Latest export</h3><p>{job.error || job.detail || 'Recovery export runs as a background job and is recorded in Event History.'}</p></div>
      <div className="recovery-job">
        <div className="button-row"><Badge tone={stateTone}>{job.state}</Badge>{job.stage && <Badge tone="neutral">{job.stage.replaceAll('_', ' ')}</Badge>}</div>
        {job.progress !== undefined && <div className="job-progress-line"><Progress value={job.progress} tone={job.state === 'completed' ? 'green' : 'blue'} /><span>{job.progress}%</span></div>}
        {bundleSize && <span className="muted">Bundle size: {bundleSize}</span>}
        {expiresAt && <span className="muted">Temporary download available until {expiresAt}</span>}
        {sha && <code className="recovery-hash">SHA-256: {sha}</code>}
        {job.state === 'completed' && <Button variant="primary" icon="download" onClick={() => { window.location.href = api.recoveryDownloadUrl(job.id) }}>Download Recovery Bundle</Button>}
      </div>
    </div>}
    {message && <Note message={{ tone: message.includes('Could not') || message.includes('failed') ? 'error' : message.endsWith('…') ? 'busy' : 'ok', text: message }} />}
    <div className="settings-footer"><Button variant="ghost" icon="refresh" onClick={() => void loadCapabilities()}>Recheck</Button><Button variant="primary" icon="download" disabled={busy || !capabilities?.supported || job?.state === 'running' || job?.state === 'queued'} onClick={() => void start()}>{busy ? 'Starting…' : 'Export Recovery Bundle'}</Button></div>
  </div>
}


// Back returns to wherever you actually came from — a Problem, a search result,
// a download row — and only falls back to the hardcoded list route when this
// page was opened cold (deep link, refresh, new tab) and there is no history to
// return to. The label follows suit so it never promises the wrong destination.
function Page({ title, subtitle, action, back, backTo = '/movies', children }: { title: string; subtitle: string; action?: React.ReactNode; back?: string; backTo?: string; children: React.ReactNode }) {
  const navigate = useNavigate()
  const cameFromApp = typeof window !== 'undefined' && window.history.state?.idx > 0
  const goBack = () => { if (cameFromApp) navigate(-1); else navigate(backTo) }
  return <div className="page"><PageTopbar title={title} subtitle={subtitle} action={action} back={back ? (cameFromApp ? 'Back' : back) : undefined} onBack={goBack} />{children}</div> }
