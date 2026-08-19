import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Icon } from './components/Icon'
import { Badge, Button, EmptyState, Input, Panel, Progress, Select, Stat } from './components/ui'
import { ApiError, api } from './api/client'
import CustomFormatsPageView from './CustomFormatsPage'
import { contextMenuSelection, duplicateLoserIds, normalizeMediaView, problemMatchesFilter, searchResultNeedsWarning, toggleIdSelection } from './lib/uiState'
import type { CustomFormat, Download, DownloadClient, IncomingDownload, Indexer, IndexerScope, InteractiveSearchJob, InteractiveSearchResult, MediaProfileSettings, Movie, MovieRelease, Problem, QualityDefinition, QualityProfile, ReconciliationEvidence, Show, Season, Episode, EpisodeMedia, TMDBShowLookup, TMDBMovieLookup, DuplicateResolvePreview, StorageRoot, RemotePathMapping, TorrentArchiveItem, EventHistoryItem, Job, RecoveryCapabilities, Tag } from './types'



const statusTone = (status: string) => status === 'Present' || status === 'Verified' || status === 'Completed' || status === 'Archived' ? 'green' : status === 'Missing' || status === 'Pending' || status === 'Downloading' || status === 'Active' ? 'amber' : status === 'Conflict' || status === 'Duplicate' || status === 'Error' ? 'red' : 'neutral'

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
      setMessage(`${label}: ${result.updated} of ${result.requested} selected movie${result.requested === 1 ? '' : 's'} updated.`)
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
  const present = movies.filter((movie) => movie.status === 'Present').length
  const missing = movies.filter((movie) => movie.status === 'Missing').length
  const review = movies.filter((movie) => movie.status === 'Conflict' || movie.status === 'Duplicate').length
  return <Page title="Movies" subtitle="Your library, exactly where it was downloaded." action={<Button variant="primary" icon="plus">Add movie</Button>}>
    <div className="stats-row"><Stat label="Movies" value={String(movies.length)} detail="Registered titles" tone="blue" /><Stat label="Present" value={String(present)} detail="Observed on disk" tone="green" /><Stat label="Missing" value={String(missing)} detail="History preserved" tone="amber" /><Stat label="Needs review" value={String(review)} detail="Conflicts and duplicates" tone="red" /></div>
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search movies…" /></div><Select value={filter} onChange={(event) => setFilter(event.target.value)}><option>All movies</option><option>Present</option><option>Missing</option><option>Conflict</option><option>Duplicate</option></Select><Select value={tagFilter} onChange={(event) => updateTagFilter(event.target.value)}><option value="">All tags</option>{tags.map((tag) => <option value={tag.name} key={tag.id}>{tag.name}</option>)}</Select>{selected.size > 0 && <span className="selection-count">{selected.size} selected · right-click for actions</span>}<div className="toolbar-spacer" /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => changeMovieView('cards')}><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => changeMovieView('table')}><Icon name="list" size={16} /></button></div><Button variant="ghost" icon="refresh" onClick={() => void loadMovies()}>Refresh</Button></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    {error && <EmptyState title="Could not load the movie library" detail={error} />}
    {!error && !loading && (view === 'cards' ? <div className="media-grid">{filtered.map((movie) => <MovieCard key={movie.id} movie={movie} selected={selected.has(movie.id)} onToggle={toggleSelected} onContext={openContext} onTagClick={updateTagFilter} />)}</div> : <MovieTable items={filtered} selected={selected} onToggle={toggleSelected} onContext={openContext} onTagClick={updateTagFilter} />)}
    {!error && !loading && !filtered.length && <EmptyState title="No movies discovered yet" detail={tagFilter ? `No movies match the tag “${tagFilter}”.` : 'Configure a Movie storage root, enable Active Operations, and start a scan.'} />}
    {context && <><div className="context-dismiss-layer" onClick={() => setContext(null)} onContextMenu={(event) => { event.preventDefault(); setContext(null) }} /><MovieBulkContextMenu position={context} count={selected.size} profiles={profiles} tags={tags} busy={bulkBusy} onRun={runBulk} onCreateTag={createTag} onClear={() => { setSelected(new Set()); setContext(null) }} /></>}
  </Page>
}

function MovieTagChips({ movie, onTagClick }: { movie: Movie; onTagClick?: (tag: string) => void }) {
  if (!movie.tags?.length) return null
  return <div className="tag-chip-row">{movie.tags.map((tag) => <span key={tag.id} className="tag-chip" role={onTagClick ? 'button' : undefined} tabIndex={onTagClick ? 0 : undefined} onClick={(event) => { if (!onTagClick) return; event.preventDefault(); event.stopPropagation(); onTagClick(tag.name) }} onKeyDown={(event) => { if (onTagClick && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onTagClick(tag.name) } }}>{tag.name}</span>)}</div>
}

function MovieCard({ movie, selected, onToggle, onContext, onTagClick }: { movie: Movie; selected: boolean; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void; onTagClick: (tag: string) => void }) {
  return <Link className={`media-card ${selected ? 'media-selected' : ''}`} to={`/movies/${movie.id}`} onContextMenu={(event) => onContext(event, movie.id)} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}><div className={`poster poster-${movie.id}`}><div className="poster-noise" /><span className="poster-title">{movie.title}</span><span className="poster-year">{movie.year}</span><span className="poster-mark">MM</span>{selected && <span className="selection-mark"><Icon name="check" size={14} /></span>}</div><div className="media-card-body"><div className="media-card-title"><strong>{movie.title}</strong><span>{movie.year}</span></div><div className="media-card-meta"><Badge tone={statusTone(movie.status)}>{movie.status}</Badge><Badge tone={statusTone(movie.plex)}>Plex {movie.plex}</Badge>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</div><MovieTagChips movie={movie} onTagClick={onTagClick} /><div className="media-card-quality"><span>{movie.quality}</span>{movie.edition && <span>{movie.edition}</span>}</div><div className="media-card-path"><Icon name="folder" size={13} />{movie.location}</div></div></Link>
}

function MovieTable({ items, selected, onToggle, onContext, onTagClick }: { items: Movie[]; selected: Set<string>; onToggle: (id: string) => void; onContext: (event: React.MouseEvent, id: string) => void; onTagClick: (tag: string) => void }) { return <Panel className="table-panel"><table className="data-table"><thead><tr><th>Title</th><th>State</th><th>Current release</th><th>Plex</th><th>Tags</th><th>Confidence</th><th>Location</th><th /></tr></thead><tbody>{items.map((movie) => <tr key={movie.id} className={selected.has(movie.id) ? 'row-selected' : ''} onContextMenu={(event) => onContext(event, movie.id)} onClick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); onToggle(movie.id) } }}><td><Link className="table-title" to={`/movies/${movie.id}`}><span className={`table-poster poster-${movie.id}`} />{movie.title}<span className="muted">{movie.year}</span>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</Link></td><td><Badge tone={statusTone(movie.status)}>{movie.status}</Badge></td><td>{movie.quality}{movie.edition && <span className="table-sub">{movie.edition}</span>}</td><td><Badge tone={statusTone(movie.plex)}>{movie.plex}</Badge></td><td><MovieTagChips movie={movie} onTagClick={onTagClick} /></td><td><span className="confidence">{movie.confidence}%</span></td><td className="path-cell">{movie.location}</td><td>{selected.has(movie.id) ? <Icon name="check" size={15} /> : <Icon name="chevron" size={15} />}</td></tr>)}</tbody></table></Panel> }

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

function evidenceFromMovie(movie: Movie): ReconciliationEvidence[] {
  const evidence = [...(movie.problems ?? [])]
  const aggregate = movie.reconciliation
  if (aggregate?.qbitMediaDisagreement && !evidence.some((item) => item.code === 'QBIT_MEDIA_DISAGREEMENT')) evidence.push({ code: 'QBIT_MEDIA_DISAGREEMENT', title: 'qBittorrent / media disagreement', detail: aggregate.qbitMediaDetail ?? 'qBittorrent reports a completed item but the expected media path is not present.', severity: 'high', source: 'qBittorrent + filesystem' })
  if ((aggregate?.plexBlocked || movie.plex === 'Conflict') && !evidence.some((item) => item.code === 'PLEX_IDENTITY_MISMATCH')) evidence.push({ code: 'PLEX_IDENTITY_MISMATCH', title: 'Plex identity conflict blocks replacement', detail: aggregate?.plexBlockDetail ?? 'The replacement remains blocked until Plex and local path identity agree.', severity: 'high', source: 'Plex + filesystem' })
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
  useEffect(() => { let alive = true; api.movie(id).then((item) => { if (alive) setMovie(item) }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Movie not found.') }); return () => { alive = false } }, [id])
  const recheckPlex = async () => {
    setBusy(true); setMessage('')
    try { const result = await api.recheckMoviePlex(id); setMovie(await api.movie(id)); setMessage(`Plex recheck complete: ${result.matched_releases} matched, ${result.conflict_releases} conflicts.`) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not recheck Plex.') }
    finally { setBusy(false) }
  }
  const refreshEvidence = async () => {
    setBusy(true); setMessage('Refreshing reconciliation evidence…')
    try {
      await api.reconcileMovie(id)
      setMovie(await api.movie(id))
      setMessage('Reconciliation refresh complete. No filesystem changes were made.')
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
  return <Page title={movie.title} subtitle={`${movie.year} · ${movie.tmdbId ? `TMDB ${movie.tmdbId}` : `Internal ID ${movie.id}`}`} back="Back to Movies" action={<><Button variant="ghost" icon="refresh" onClick={refreshEvidence} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh evidence'}</Button><Button variant="ghost" icon="refresh" onClick={recheckPlex} disabled={busy}>Recheck Plex</Button><Button variant="primary" icon="search">Interactive search</Button></>}>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    {(movie.rootHealth === 'offline' || movie.rootHealth === 'unavailable' || movie.reconciliation?.rootOffline) && <div className="reconciliation-banner reconciliation-banner-red"><Icon name="alert" size={17} /><div><strong>Storage Root Offline</strong><span>{movie.reconciliation?.rootAffectedCount ?? movie.rootAffectedCount ?? 0} media affected. Missing grace is held until the root is reachable.</span></div></div>}
    <div className="detail-layout"><div><Panel className="detail-hero"><div className="detail-poster poster-inception"><span className="poster-title">{movie.title}</span><span className="poster-year">{movie.year}</span></div><div className="detail-intro"><div className="eyebrow">MOVIE {movie.tmdbId ? `· TMDB ${movie.tmdbId}` : ''}</div><h2>{movie.title} <span className="detail-year">({movie.year})</span></h2><div className="badge-row"><Badge tone={statusTone(movie.status)}>{movie.status}</Badge><Badge tone={statusTone(movie.plex)}>Plex {movie.plex}</Badge><Badge tone="blue">{movie.confidence}% match</Badge>{movie.monitored === false && <Badge tone="neutral">Unmonitored</Badge>}</div>{movie.tags?.length ? <div className="tag-chip-row detail-tag-row">{movie.tags.map((tag) => <Link key={tag.id} className="tag-chip" to={`/movies?tag=${encodeURIComponent(tag.name)}`}>{tag.name}</Link>)}</div> : null}<p className="detail-description">{movie.overview ?? 'The filesystem remains the source of truth. Reconciliation preserves old paths and release evidence without moving or deleting media.'}</p><div className="detail-actions"><Button variant="ghost" icon="external">Change match</Button></div></div></Panel>{movie.incoming && <IncomingReplacement incoming={movie.incoming} />}<Panel title="Current releases" eyebrow="REGISTERED MEDIA">{currentReleases.length ? currentReleases.map((release) => <ReleaseEvidenceRow key={release.id || release.name} release={release} />) : currentSummary}</Panel><MovieProfilePanel resourceId={id} /><MovieTagsPanel resourceId={id} assigned={movie.tags ?? []} onChanged={(tags) => setMovie((current) => current ? { ...current, tags } : current)} /><Panel title="Release history" eyebrow="PRESERVED EVIDENCE">{history.length ? history.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{event.type} · {formatEvidenceDate(event.createdAt)}</span></div><Badge tone={event.type.includes('replaced') ? 'purple' : event.type.includes('duplicate') || event.type.includes('conflict') ? 'red' : 'neutral'}>{event.type.replaceAll('.', ' ')}</Badge></div>) : historyReleases.map((release) => <div className="history-row" key={release.id || release.name}><span className="history-line" /><div><strong>{release.name}</strong><span>{release.state} · first observed {formatEvidenceDate(release.firstSeenAt)}</span></div><Badge tone={releaseStateTone(release.state) as 'green' | 'amber' | 'red' | 'neutral'}>Release</Badge></div>)}{!history.length && !historyReleases.length && <div className="history-empty">No persisted release events yet. New replacement and reappearance events will appear here.</div>}</Panel><Panel title="Torrent history" eyebrow="RECOVERY EVIDENCE">{movie.torrentHistory?.length ? movie.torrentHistory.map((torrent) => <div className="history-row" key={torrent.id}><span className="history-line" /><div><strong>{torrent.releaseName ?? torrent.torrentName}</strong><span>{torrent.originalDownloadClient ?? 'Unknown client'} · {torrent.infoHash.slice(0, 12)}… · qBit {torrent.qbitPresent ? 'present' : 'removed'}</span></div><Badge tone={torrent.archiveState === 'archived' ? 'green' : torrent.archiveState === 'failed' ? 'red' : 'amber'}>{torrent.archiveState.replaceAll('_', ' ')}</Badge></div>) : <div className="history-empty">No torrent recovery history is associated with this movie yet.</div>}</Panel></div><aside className="detail-side"><Panel title="At a glance" eyebrow="STATUS"><DetailFact label="Library state" value={movie.status} tone={statusTone(movie.status)} /><DetailFact label="Plex verification" value={movie.plex} tone={statusTone(movie.plex)} /><DetailFact label="Storage root" value={movie.storageRoot ?? 'Unknown root'} /><DetailFact label="Last observed" value={formatEvidenceDate(movie.lastObservedAt)} /></Panel><Panel title="Problems" eyebrow={evidence.length ? `${evidence.length} NEED ATTENTION` : 'NEEDS ATTENTION'}>{evidence.length ? evidence.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}><div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div><div><strong>{item.title}</strong><span>{item.detail}</span></div></div>) : <div className="problem-mini"><div className="problem-icon"><Icon name="check" size={14} /></div><div><strong>No unresolved problems</strong><span>Identity, qBittorrent, Plex, and path evidence agree.</span></div></div>}</Panel></aside></div>
  </Page>
}

function DetailFact({ label, value, tone }: { label: string; value: string; tone?: string }) { return <div className="detail-fact"><span>{label}</span>{tone ? <Badge tone={tone as 'green' | 'amber' | 'red' | 'neutral'}>{value}</Badge> : <strong>{value}</strong>}</div> }

export function ShowsPage() {
  const [items, setItems] = useState<Show[]>([])
  const [view, setView] = useState<'cards' | 'table'>(() => normalizeMediaView(window.localStorage.getItem('medialogue.shows.view')))
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)
  const [lookupQuery, setLookupQuery] = useState('')
  const [lookupResults, setLookupResults] = useState<TMDBShowLookup[]>([])
  const [lookupBusy, setLookupBusy] = useState(false)
  const load = async () => {
    setLoading(true)
    try { setItems(await api.shows(query)); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load shows.') }
    finally { setLoading(false) }
  }
  useEffect(() => { const timer = window.setTimeout(() => { void load() }, 150); return () => window.clearTimeout(timer) }, [query])
  const lookup = async () => {
    if (!lookupQuery.trim()) return
    setLookupBusy(true)
    try { setLookupResults(await api.lookupShows(lookupQuery.trim())); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'TMDB lookup failed.') }
    finally { setLookupBusy(false) }
  }
  const add = async (candidate: TMDBShowLookup) => {
    setLookupBusy(true)
    try { await api.addShow(candidate.tmdbId); setAdding(false); setLookupQuery(''); setLookupResults([]); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not add show.') }
    finally { setLookupBusy(false) }
  }
  const present = items.filter((show) => show.status === 'Present').length
  const missingEpisodes = items.reduce((sum, show) => sum + (show.episodesMissing ?? Math.max(0, show.episodesTotal - show.episodesPresent)), 0)
  const totalEpisodes = items.reduce((sum, show) => sum + show.episodesTotal, 0)
  return <Page title="Shows" subtitle="Track seasons and episodes without reorganizing your files." action={<Button variant="primary" icon="plus" onClick={() => setAdding((value) => !value)}>Add show</Button>}>
    <div className="stats-row"><Stat label="Shows" value={String(items.length)} detail={`${present} fully present`} tone="blue" /><Stat label="Episodes" value={String(totalEpisodes)} detail={`${items.reduce((sum, show) => sum + show.episodesPresent, 0)} present`} tone="green" /><Stat label="Missing" value={String(missingEpisodes)} detail="Episode-level inventory" tone="amber" /><Stat label="Needs review" value={String(items.filter((show) => show.status === 'Conflict' || (show.problemCount ?? 0) > 0).length)} detail="Conflicts and mapping issues" tone="red" /></div>
    {adding && <Panel title="Add Show from TMDB" eyebrow="METADATA"><div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={lookupQuery} onChange={(event) => setLookupQuery(event.target.value)} placeholder="Search TMDB for a show…" onKeyDown={(event) => { if (event.key === 'Enter') void lookup() }} /></div><Button variant="primary" onClick={() => void lookup()} disabled={lookupBusy}>{lookupBusy ? 'Searching…' : 'Search'}</Button></div>{lookupResults.length ? <div className="history-list">{lookupResults.map((candidate) => <div className="history-row" key={candidate.tmdbId}><span className="history-line" /><div><strong>{candidate.title} {candidate.year ? `(${candidate.year})` : ''}</strong><span>TMDB {candidate.tmdbId}{candidate.originalTitle && candidate.originalTitle !== candidate.title ? ` · ${candidate.originalTitle}` : ''}</span></div><Button variant="ghost" onClick={() => void add(candidate)} disabled={lookupBusy}>Add</Button></div>)}</div> : <span className="muted">Search TMDB, choose the exact Show, then Medialogue will create Seasons and Episodes as Missing until media is discovered.</span>}</Panel>}
    <div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search shows…" /></div><div className="toolbar-spacer" /><div className="view-toggle"><button className={view === 'cards' ? 'selected' : ''} onClick={() => { setView('cards'); window.localStorage.setItem('medialogue.shows.view', 'cards') }}><Icon name="grid" size={16} /></button><button className={view === 'table' ? 'selected' : ''} onClick={() => { setView('table'); window.localStorage.setItem('medialogue.shows.view', 'table') }}><Icon name="list" size={16} /></button></div><Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button></div>
    {error && <EmptyState title="Could not load Shows" detail={error} />}
    {!error && !loading && (view === 'cards' ? <div className="media-grid show-grid">{items.map((show) => <ShowCard key={show.id} show={show} />)}</div> : <Panel className="table-panel"><table className="data-table"><thead><tr><th>Show</th><th>Episodes</th><th>Status</th><th>Plex</th><th>Seasons</th><th /></tr></thead><tbody>{items.map((show) => <tr key={show.id}><td><Link className="table-title" to={`/shows/${show.id}`}><span className={`table-poster poster-${show.id}`} />{show.title}<span className="muted">{show.year || ''}</span></Link></td><td>{show.episodesPresent} / {show.episodesTotal}</td><td><Badge tone={statusTone(show.status)}>{show.status}</Badge></td><td><Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge></td><td>{show.seasons}</td><td><Icon name="chevron" size={15} /></td></tr>)}</tbody></table></Panel>)}
    {!error && !loading && !items.length && <EmptyState title="No Shows yet" detail="Add a Show from TMDB or scan a configured Show storage root." />}
  </Page>
}

function ShowCard({ show }: { show: Show }) {
  const percent = show.episodesTotal ? Math.round((show.episodesPresent / show.episodesTotal) * 100) : 0
  return <Link className="media-card show-card" to={`/shows/${show.id}`}><div className={`poster poster-${show.id}`}><div className="poster-noise" /><span className="poster-title">{show.title}</span><span className="poster-year">{show.year || ''}</span><span className="poster-mark">TV</span></div><div className="media-card-body"><div className="media-card-title"><strong>{show.title}</strong><span>{show.year || ''}</span></div><div className="media-card-meta"><Badge tone={statusTone(show.status)}>{show.status}</Badge><Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge></div><div className="episode-progress"><div><span>Episodes</span><strong>{show.episodesPresent} / {show.episodesTotal}</strong></div><Progress value={percent} tone={percent === 100 ? 'green' : 'amber'} /></div><div className="media-card-path"><Icon name="tv" size={13} />{show.seasons} seasons · {show.problemCount ?? 0} problems</div></div></Link>
}

export function ShowDetailPage({ id }: { id: string }) {
  const [show, setShow] = useState<Show | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [mappingEditor, setMappingEditor] = useState<{ media: EpisodeMedia; season: Season } | null>(null)
  const [mappingEpisodeIds, setMappingEpisodeIds] = useState<string[]>([])
  const load = async () => { try { setShow(await api.show(id)); setError('') } catch (reason) { setError(reason instanceof Error ? reason.message : 'Show not found.') } }
  useEffect(() => { void load() }, [id])
  const refreshMetadata = async () => { setBusy(true); try { setShow(await api.refreshShowMetadata(id)); setMessage('TMDB metadata refreshed. Existing media mappings were preserved.') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Metadata refresh failed.') } finally { setBusy(false) } }
  const recheckPlex = async () => { setBusy(true); try { const result = await api.recheckShowPlex(id); await load(); setMessage(`Plex checked ${result.checked_releases} episode files: ${result.matched_releases} matched, ${result.conflict_releases} conflicts.`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Plex recheck failed.') } finally { setBusy(false) } }
  const setSeasonMonitored = async (season: Season, monitored: boolean) => { try { await api.updateSeason(season.id, { monitored, expected_revision: season.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update season monitoring.') } }
  const setEpisodeMonitored = async (episode: Episode, monitored: boolean) => { try { await api.updateEpisode(episode.id, { monitored, expected_revision: episode.revision }); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not update episode monitoring.') } }
  const searchSeason = async (season: Season) => { try { const result = await api.startSeasonSearch(season.id); setMessage(`Season search started · job ${result.job_id.slice(0, 8)}…`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start season search.') } }
  const searchEpisode = async (episode: Episode) => { try { const result = await api.startEpisodeSearch(episode.id); setMessage(`Episode search started · job ${result.job_id.slice(0, 8)}…`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start episode search.') } }
  const editMapping = (media: EpisodeMedia, season: Season) => { const selected = season.episodes.filter((episode) => media.mappedEpisodeNumbers.includes(episode.episodeNumber)).map((episode) => episode.id); setMappingEpisodeIds(selected); setMappingEditor({ media, season }) }
  const saveMapping = async () => { if (!mappingEditor || !mappingEpisodeIds.length) return; setBusy(true); try { await api.correctEpisodeMapping(mappingEditor.media.mediaFileId, mappingEpisodeIds); setMessage('Episode mapping corrected. The media file and path were not changed.'); setMappingEditor(null); await load() } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not correct episode mapping.') } finally { setBusy(false) } }
  if (error) return <Page title="Show unavailable" subtitle={error} back="Back to Shows" backTo="/shows"><EmptyState title="Could not load this Show" detail={error} /></Page>
  if (!show) return <Page title="Loading Show" subtitle="Retrieving seasons and episode inventory." back="Back to Shows" backTo="/shows"><EmptyState title="Loading…" detail="Reading the persisted Show record." /></Page>
  const seasons = show.seasonDetail ?? []
  return <Page title={show.title} subtitle={`${show.year || 'Year unknown'} · ${show.tmdbId ? `TMDB ${show.tmdbId}` : show.id}${show.tvdbId ? ` · TVDB ${show.tvdbId}` : ''}`} back="Back to Shows" backTo="/shows" action={<><Button variant="ghost" icon="refresh" onClick={() => void refreshMetadata()} disabled={busy}>Refresh metadata</Button><Button variant="ghost" icon="refresh" onClick={() => void recheckPlex()} disabled={busy}>Recheck Plex</Button></>}>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    {mappingEditor && <Panel title="Correct episode mapping" eyebrow="LOGICAL MAPPING ONLY"><div className="settings-note"><Icon name="shield" size={15} /><span>This changes only Medialogue's episode mapping. The file is not renamed, moved, copied, or modified.</span></div><div className="mapping-path">{mappingEditor.media.path}</div><div className="mapping-grid">{mappingEditor.season.episodes.map((episode) => <label className="inline-check" key={episode.id}><input type="checkbox" checked={mappingEpisodeIds.includes(episode.id)} onChange={(event) => setMappingEpisodeIds((current) => event.target.checked ? [...current, episode.id] : current.filter((item) => item !== episode.id))} />S{String(episode.seasonNumber).padStart(2, '0')}E{String(episode.episodeNumber).padStart(2, '0')} · {episode.title || 'Untitled episode'}</label>)}</div><div className="settings-footer"><Button variant="ghost" onClick={() => setMappingEditor(null)}>Cancel</Button><Button variant="primary" onClick={() => void saveMapping()} disabled={busy || !mappingEpisodeIds.length}>{busy ? 'Saving…' : 'Save mapping'}</Button></div></Panel>}
    <Panel className="detail-hero"><div className={`detail-poster poster-${show.id}`}><span className="poster-title">{show.title}</span><span className="poster-year">{show.year || ''}</span></div><div className="detail-intro"><div className="eyebrow">SHOW {show.tmdbId ? `· TMDB ${show.tmdbId}` : ''}</div><h2>{show.title} {show.year ? <span className="detail-year">({show.year})</span> : null}</h2><div className="badge-row"><Badge tone={statusTone(show.status)}>{show.status}</Badge><Badge tone={statusTone(show.plex)}>Plex {show.plex}</Badge><Badge tone={show.monitored === false ? 'neutral' : 'blue'}>{show.monitored === false ? 'Unmonitored' : 'Monitored'}</Badge></div><p className="detail-description">{show.overview ?? 'Episode presence is tracked independently while every file stays at its existing path.'}</p></div></Panel>
    <div className="detail-layout"><div><Panel title="Seasons & Episodes" eyebrow={`${show.episodesPresent} / ${show.episodesTotal} PRESENT`}>{seasons.map((season) => { const open = expanded[season.id] ?? false; return <div className="season-block" key={season.id}><div className="release-evidence-row"><button className="icon-button" onClick={() => setExpanded((value) => ({ ...value, [season.id]: !open }))}><Icon name="chevron" size={15} style={{ transform: open ? 'rotate(90deg)' : undefined }} /></button><div className="release-main"><strong>{season.title || `Season ${season.seasonNumber}`}</strong><span>{season.presentCount} / {season.episodeCount} present · {season.missingCount} missing</span></div><Badge tone={season.missingCount ? 'amber' : 'green'}>{season.missingCount ? 'Incomplete' : 'Complete'}</Badge><Button variant="ghost" onClick={() => void searchSeason(season)}>Search season</Button><label className="inline-check"><input type="checkbox" checked={season.monitored} onChange={(event) => void setSeasonMonitored(season, event.target.checked)} />Monitored</label></div>{open && <div className="episode-list">{season.episodes.map((episode) => <div className="history-row" key={episode.id}><span className="history-line" /><div><strong>S{String(episode.seasonNumber).padStart(2, '0')}E{String(episode.episodeNumber).padStart(2, '0')} · {episode.title || 'Untitled episode'}</strong><span>{episode.quality || 'No media'}{episode.media[0]?.path ? ` · ${episode.media[0].path}` : ''}</span><div className="badge-row compact">{episode.media[0]?.releaseScope === 'season_pack' && <Badge tone="purple">Season pack</Badge>}{episode.media[0]?.mappedEpisodeNumbers.length > 1 && <Badge tone="blue">Multi-episode · {episode.media[0].mappedEpisodeNumbers.map((number) => `E${String(number).padStart(2, '0')}`).join(' + ')}</Badge>}{episode.media[0]?.manualMapping && <Badge tone="neutral">Manual mapping</Badge>}</div></div><Badge tone={statusTone(episode.status)}>{episode.status}</Badge><Badge tone={statusTone(episode.plex)}>Plex {episode.plex}</Badge><label className="inline-check"><input type="checkbox" checked={episode.monitored} onChange={(event) => void setEpisodeMonitored(episode, event.target.checked)} />Monitor</label>{episode.media[0] && <Button variant="ghost" onClick={() => editMapping(episode.media[0], season)}>Map</Button>}<Button variant="ghost" onClick={() => void searchEpisode(episode)}>Search</Button></div>)}</div>}</div> })}{!seasons.length && <div className="history-empty">No season metadata exists yet. Refresh TMDB metadata or scan a Show root.</div>}</Panel><ShowProfilePanel resourceId={id} /><Panel title="Recent history" eyebrow="EVENTS">{show.recentEvents?.length ? show.recentEvents.map((event, index) => <div className="history-row" key={event.id ?? `${event.type}-${index}`}><span className="history-line" /><div><strong>{event.message}</strong><span>{event.type} · {formatEvidenceDate(event.createdAt)}</span></div></div>) : <div className="history-empty">No Show or episode events recorded yet.</div>}</Panel></div><aside className="detail-side"><Panel title="At a glance" eyebrow="STATUS"><DetailFact label="Library state" value={show.status} tone={statusTone(show.status)} /><DetailFact label="Plex verification" value={show.plex} tone={statusTone(show.plex)} /><DetailFact label="Episodes present" value={`${show.episodesPresent} / ${show.episodesTotal}`} /><DetailFact label="Last observed" value={formatEvidenceDate(show.lastObservedAt)} /></Panel><Panel title="Problems" eyebrow={`${show.problemCount ?? 0} NEED ATTENTION`}>{show.problems?.length ? show.problems.map((item, index) => <div className="problem-mini problem-mini-alert" key={item.id ?? `${item.code}-${index}`}><div className={`problem-icon severity-${item.severity}`}><Icon name="alert" size={14} /></div><div><strong>{item.title}</strong><span>{item.detail}</span></div></div>) : <div className="problem-mini"><div className="problem-icon"><Icon name="check" size={14} /></div><div><strong>No unresolved problems</strong><span>Mapped episode files are consistent with current evidence.</span></div></div>}</Panel></aside></div>
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
  const seeding = items.filter((item) => item.state === 'Seeding')
  const completed = items.filter((item) => item.state === 'Completed')
  const clientCount = new Set(items.map((item) => item.client)).size
  const disagreements = items.filter((item) => item.reconciliationState?.toLowerCase().includes('disagree') || item.mediaState?.toLowerCase().includes('missing') || item.mediaState?.toLowerCase().includes('conflict')).length
  return <Page title="Downloads" subtitle="Observe qBittorrent activity without importing or moving media." action={<Button variant="ghost" icon="refresh" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</Button>}>
    <div className="stats-row"><Stat label="Downloading" value={String(downloading.length)} detail="Live progress" tone="blue" /><Stat label="Seeding" value={String(seeding.length)} detail="Active torrents" tone="green" /><Stat label="Completed" value={String(completed.length)} detail="Observed torrents" /><Stat label="Tracked clients" value={String(clientCount)} detail={clientCount ? 'Reporting activity' : 'No activity'} />{disagreements > 0 && <Stat label="Evidence mismatch" value={String(disagreements)} detail="qBit / media review" tone="red" />}</div>
    <div className="toolbar"><div className="filter-tabs">{(['Downloading', 'Seeding', 'Completed', 'Paused', 'Error', 'All'] as const).map((item) => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item}</button>)}</div><div className="toolbar-spacer" /><Link className="button button-ghost" to="/settings"><Icon name="settings" size={16} />Client settings</Link></div>
    {error && <EmptyState icon="download" title="Could not load downloads" detail={error} action={<Button variant="ghost" icon="refresh" onClick={() => void load()}>Try again</Button>} />}
    {!error && !loading && !filtered.length && <EmptyState icon="download" title={items.length ? 'No downloads match this filter' : 'No downloads observed'} detail={items.length ? 'Try another state filter or refresh qBittorrent.' : 'Configure an enabled qBittorrent client to begin read-only polling.'} />}
    {!error && (loading || filtered.length > 0) && <Panel className="table-panel"><table className="data-table downloads-table"><thead><tr><th>{sortLabel('name', 'Release')}</th><th>{sortLabel('client', 'Client')}</th><th>{sortLabel('kind', 'Scope')}</th><th>{sortLabel('progress', 'Progress')}</th><th>{sortLabel('size', 'Size')}</th><th>{sortLabel('eta', 'ETA')}</th><th>{sortLabel('path', 'Save path')}</th><th>Media evidence</th><th /></tr></thead><tbody>{loading ? <tr><td colSpan={9} className="table-loading">Reading qBittorrent observations…</td></tr> : filtered.map((download) => <tr key={download.id}><td><div className="release-cell"><span className={`state-icon state-${download.state.toLowerCase()}`}><Icon name={download.state === 'Downloading' ? 'download' : download.state === 'Seeding' ? 'activity' : download.state === 'Error' ? 'alert' : 'check'} size={14} /></span><strong>{download.name}</strong>{(download.quality || download.edition) && <small>{[download.quality, download.edition].filter(Boolean).join(' · ')}</small>}</div></td><td><span className="client-name"><span className="client-dot" />{download.client}</span></td><td><Badge tone="neutral">{download.kind}</Badge></td><td><div className="download-progress"><Progress value={download.progress} tone={download.state === 'Seeding' || download.state === 'Completed' ? 'green' : download.state === 'Error' ? 'amber' : 'blue'} /><span>{Math.round(download.progress)}%</span></div></td><td>{download.size}</td><td className="muted">{download.eta}</td><td className="path-cell">{download.path}</td><td>{download.reconciliationState || download.mediaState ? <Badge tone={download.reconciliationState?.toLowerCase().includes('disagree') || download.mediaState?.toLowerCase().includes('missing') || download.mediaState?.toLowerCase().includes('conflict') ? 'red' : 'neutral'}>{download.reconciliationState || download.mediaState}</Badge> : <span className="muted">Observed only</span>}</td><td><button className="icon-button" aria-label={`Open ${download.name}`}><Icon name="chevron" size={15} /></button></td></tr>)}</tbody></table></Panel>}
  </Page>
}


export function EventHistoryPage() {
  const [events, setEvents] = useState<EventHistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [eventType, setEventType] = useState('')
  const [severity, setSeverity] = useState('')
  const [entityType, setEntityType] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    try {
      const payload = await api.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, pageSize: 100 })
      setEvents(payload.items)
      setTotal(payload.total)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load event history.')
    } finally { setLoading(false) }
  }

  useEffect(() => {
    let alive = true
    setLoading(true)
    const refresh = () => api.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, pageSize: 100 }).then((payload) => { if (alive) { setEvents(payload.items); setTotal(payload.total); setError('') } }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load event history.') }).finally(() => { if (alive) setLoading(false) })
    void refresh()
    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const onDurableEvent = () => void refresh()
    ;[
      'download.completed', 'scan.completed', 'scan.failed', 'plex.health', 'plex.verified', 'plex.conflict',
      'problem.created', 'problem.resolved', 'storage_root.unavailable', 'storage_root.restored', 'release.replaced',
      'media.missing', 'media.present', 'media.reappeared', 'torrent.detected', 'torrent.removed', 'torrent.reappeared',
      'search.download_submitted', 'show.metadata_refreshed', 'qbittorrent.health',
      'tag.created', 'tag.updated', 'tag.deleted', 'movie.tags_updated', 'movie.monitoring_updated',
      'quality_profile.assignment_updated', 'parser.reevaluated', 'custom_formats.reevaluated', 'bulk.operation_completed',
    ].forEach((name) => stream.addEventListener(name, onDurableEvent))
    const timer = window.setInterval(refresh, 30000)
    return () => { alive = false; stream.close(); window.clearInterval(timer) }
  }, [eventType, severity, entityType])

  return <Page title="Event History" subtitle="Durable state changes and decisions. High-frequency progress stays live-only and does not flood this history." action={<Button variant="ghost" icon="refresh" onClick={() => void load()}>Refresh</Button>}>
    <div className="toolbar event-toolbar">
      <Select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option><option value="info">Info</option><option value="warning">Warning</option><option value="error">Error</option></Select>
      <Select value={entityType} onChange={(event) => setEntityType(event.target.value)}><option value="">All entities</option><option value="movie">Movies</option><option value="show">Shows</option><option value="episode">Episodes</option><option value="movie_release">Movie releases</option><option value="show_release">Show releases</option><option value="torrent">Torrents</option><option value="storage_root">Storage roots</option><option value="download_client">Download clients</option><option value="tag">Tags</option><option value="bulk_operation">Bulk operations</option></Select>
      <Input value={eventType} onChange={(event) => setEventType(event.target.value)} placeholder="Exact event type, e.g. release.replaced" />
      <div className="toolbar-spacer" /><span className="muted">{total} durable events</span>
    </div>
    {error && <EmptyState icon="alert" title="Could not load event history" detail={error} />}
    {!error && !loading && <Panel className="table-panel event-history-panel"><table className="data-table event-history-table"><thead><tr><th>When</th><th>Severity</th><th>Event</th><th>Entity</th><th>Message</th><th /></tr></thead><tbody>{events.map((item) => <tr key={item.id}><td className="event-time">{new Date(item.createdAt).toLocaleString()}</td><td><Badge tone={item.severity === 'error' ? 'red' : item.severity === 'warning' ? 'amber' : 'neutral'}>{item.severity}</Badge></td><td><code>{item.eventType}</code></td><td><span className="event-entity">{item.entityType}{item.entityId ? ` · ${item.entityId.slice(0, 8)}…` : ''}</span></td><td><strong>{item.message}</strong></td><td>{Object.keys(item.details).length > 0 && <details className="event-details"><summary>Details</summary><pre>{JSON.stringify(item.details, null, 2)}</pre></details>}</td></tr>)}</tbody></table>{!events.length && <div className="history-empty">No durable events match the selected filters.</div>}</Panel>}
    {!error && loading && <EmptyState icon="clock" title="Loading event history" detail="Reading persisted state changes from PostgreSQL." />}
  </Page>
}

export function ProblemsPage() {
  const [items, setItems] = useState<Problem[]>([])
  const [selected, setSelected] = useState<string | undefined>()
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
  const [duplicateMovie, setDuplicateMovie] = useState<Movie | null>(null)
  const [reasonFilter, setReasonFilter] = useState('all')
  const [severityFilter, setSeverityFilter] = useState('all')

  const load = async () => {
    setLoading(true)
    try { setItems(await api.problems('open')); setMessage(''); setLoaded(true) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load live problems.'); setLoaded(true) }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  const sourceProblems = items
  const visible = sourceProblems.filter((problem) => problemMatchesFilter(problem, reasonFilter, severityFilter))
  const current = visible.find((problem) => problem.id === selected) ?? visible[0]
  useEffect(() => {
    setDuplicatePreview(null)
    setWinnerReleaseId('')
    setDeleteMedia(false)
    setRemoveTorrents(false)
    setTmdbMatches([])
    setTmdbShowMatches([])
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
    try { await api.reconcileAll(); await load(); setMessage('Reconciliation refresh complete. No filesystem changes were made.') }
    catch (reason) {
      if (reason instanceof ApiError && (reason.status === 404 || reason.status === 405)) { await load(); setMessage('Problems refreshed. This server does not expose a bulk reconciliation action.') }
      else { setMessage(reason instanceof Error ? reason.message : 'Could not refresh problems.'); setLoading(false) }
    }
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
      const result = await api.resolveMovieDuplicate(current.entityId, duplicatePreview.confirmationToken)
      setDuplicatePreview(null)
      await load()
      setMessage(result.duplicateResolved
        ? `Duplicate resolved. ${result.deletedDirectories.length ? `${result.deletedDirectories.length} losing director${result.deletedDirectories.length === 1 ? 'y' : 'ies'} deleted.` : 'No media was deleted.'}`
        : 'Preferred release recorded. The duplicate remains open until the losing copy is actually gone.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not commit duplicate resolution.') }
    finally { setLoading(false) }
  }

  const searchTmdb = async () => {
    if (!tmdbQuery.trim()) return
    setLoading(true); setMessage('')
    try { setTmdbMatches(await api.lookupMovies(tmdbQuery.trim())) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'TMDB lookup failed.') }
    finally { setLoading(false) }
  }

  const confirmMovie = async (match: TMDBMovieLookup) => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      await api.resolveProblem(current.id, 'confirm_movie_match', { tmdb_id: match.tmdbId })
      await load()
      setMessage(`Manually matched to ${match.title}${match.year ? ` (${match.year})` : ''}. No media was renamed or moved.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not apply the manual Movie match.') }
    finally { setLoading(false) }
  }

  const searchTmdbShows = async () => {
    if (!tmdbQuery.trim()) return
    setLoading(true); setMessage('')
    try { setTmdbShowMatches(await api.lookupShows(tmdbQuery.trim())) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'TMDB Show lookup failed.') }
    finally { setLoading(false) }
  }

  const confirmShow = async (match: TMDBShowLookup) => {
    if (!current) return
    setLoading(true); setMessage('')
    try {
      await api.resolveProblem(current.id, 'confirm_show_match', { tmdb_id: match.tmdbId })
      await load()
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
    try { await api.resolveProblem(current.id, 'recheck'); await reEvaluate() }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not request a recheck.') }
    finally { setLoading(false) }
  }

  return <Page title="Problems" subtitle="A single queue for identity conflicts, duplicates, root outages, and low-confidence matches.">
    <div className="problem-summary"><div className="problem-summary-icon"><Icon name="alert" size={22} /></div><div><strong>{!loaded ? 'Loading Problems…' : `${visible.length} item${visible.length === 1 ? '' : 's'} need your attention`}</strong><span>Evidence is preserved; nothing is deleted or changed automatically.</span></div><Button variant="ghost" icon="refresh" onClick={() => void reEvaluate()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh evidence'}</Button></div>
    <div className="toolbar"><Select value={reasonFilter} onChange={(event) => { setReasonFilter(event.target.value); setSelected(undefined) }}><option value="all">All problem types</option><option value="duplicates">Duplicates</option><option value="identity">Identity / matching</option><option value="paths">Paths / storage</option><option value="PLEX_IDENTITY_MISMATCH">Plex conflicts</option></Select><Select value={severityFilter} onChange={(event) => { setSeverityFilter(event.target.value); setSelected(undefined) }}><option value="all">All priorities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></Select><span className="muted">Showing {visible.length} of {sourceProblems.length} open Problems</span></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    <div className="problem-layout"><Panel className="problem-list-panel"><div className="problem-filter"><span className="eyebrow">OPEN PROBLEMS</span><span className="muted">{visible.length} total</span></div>{visible.map((problem, index) => <button key={problem.id || `${problem.code}-${index}`} className={`problem-row ${current?.id === problem.id ? 'selected' : ''}`} onClick={() => setSelected(problem.id)}><div className={`problem-severity severity-${problem.severity}`}><Icon name="alert" size={15} /></div><div className="problem-row-copy"><strong>{problem.title}</strong><span>{problem.subject}</span><small>{problem.created}</small></div><Icon name="chevron" size={16} /></button>)}</Panel>
      <Panel className="problem-detail-panel" title={current?.title ?? 'Select a problem'} eyebrow={current?.code ?? 'REVIEW QUEUE'}>{current ? <>
        <div className="issue-banner"><Badge tone={current.severity === 'high' ? 'red' : current.severity === 'low' ? 'neutral' : 'amber'}>{current.severity} priority</Badge><span>{current.code}</span></div>
        <p className="issue-detail">{current.detail}</p>
        <div className="compare-card"><div><span className="eyebrow">AFFECTED MEDIA</span><strong>{current.subject}</strong><span>{current.entityType ? `${current.entityType} · ` : ''}{current.entityId ?? 'Persisted reconciliation evidence'}</span></div><Icon name="arrow" size={20} /><div><span className="eyebrow">EVIDENCE</span><strong>{current.code === 'PLEX_IDENTITY_MISMATCH' ? 'Plex + local identity' : current.code.includes('DUPLICATE') ? 'Physical filesystem evidence' : 'Parser / integration observation'}</strong><span>{current.details ? Object.entries(current.details).slice(0, 3).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`).join(' · ') : 'Review the persisted evidence before applying any action.'}</span></div></div>

        {current.code === 'DUPLICATE_PHYSICAL_RELEASE' && releaseIds.length > 1 && <div className="resolution-block"><div className="eyebrow">DUPLICATE RESOLVER</div><p>Select the copy to keep. Medialogue will not delete the loser unless you explicitly request deletion and review a fresh inventory first.</p><div className="history-list">{releaseIds.map((releaseId) => { const release = duplicateMovie?.releasesDetail?.find((item) => item.id === releaseId); const path = release?.directories?.find((directory) => directory.exists)?.path ?? release?.directories?.[0]?.path; return <label className="inline-check" key={releaseId}><input type="radio" name="duplicate-winner" checked={winnerReleaseId === releaseId} onChange={() => { setWinnerReleaseId(releaseId); setDuplicatePreview(null) }} /><span><strong>{release?.name ?? `Release ${releaseId}`}</strong><small>{[release?.quality, release?.edition, release?.releaseGroup].filter(Boolean).join(' · ') || 'Release details unavailable'}{path ? ` · ${path}` : ''}</small></span></label> })}</div><label className="inline-check"><input type="checkbox" checked={deleteMedia} onChange={(event) => { setDeleteMedia(event.target.checked); setDuplicatePreview(null) }} />Delete the losing media director{loserIds.length === 1 ? 'y' : 'ies'} after preview</label><label className="inline-check"><input type="checkbox" checked={removeTorrents} onChange={(event) => { setRemoveTorrents(event.target.checked); setDuplicatePreview(null) }} />Remove losing torrent(s) from qBittorrent; archived .torrent files remain</label><div className="detail-actions"><Button variant="primary" disabled={!winnerReleaseId || loading} onClick={() => void previewDuplicate()}>Preview resolution</Button></div></div>}

        {duplicatePreview && <div className="resolution-block destructive-preview"><div className="eyebrow">FRESH DESTRUCTIVE PREVIEW</div><strong>{duplicatePreview.movieTitle}</strong><div className="settings-note"><Icon name="shield" size={15} /><span>Torrent backups are retained. The confirmation expires at {new Date(duplicatePreview.expiresAt).toLocaleTimeString()}.</span></div><div className="compare-card"><div><span className="eyebrow">KEEP</span><strong>{duplicatePreview.winner.releaseName}</strong><span>{[duplicatePreview.winner.quality, duplicatePreview.winner.edition, duplicatePreview.winner.releaseGroup].filter(Boolean).join(' · ')}</span></div><Icon name="arrow" size={20} /><div><span className="eyebrow">LOSING COPY</span><strong>{duplicatePreview.losers.map((item) => item.releaseName).join(' / ')}</strong><span>{deleteMedia ? 'Entire associated media directory will be deleted.' : 'Media will remain untouched; duplicate stays open.'}</span></div></div>{duplicatePreview.losers.flatMap((release) => release.directories).map((directory) => <div className="duplicate-directory-preview" key={directory.directoryId}><strong>{directory.path}</strong><span>{directory.storageRoot} · {directory.accessMode} · {directory.files.length} files inventoried</span>{deleteMedia && <div className="expert-code">{directory.files.map((file) => file.relativePath).join('\n') || '(directory is already empty)'}</div>}</div>)}{duplicatePreview.warnings.map((warning) => <div className="settings-note" key={warning}><Icon name="alert" size={15} /><span>{warning}</span></div>)}<div className="detail-actions"><Button variant="ghost" onClick={() => setDuplicatePreview(null)}>Cancel</Button><Button variant={deleteMedia || removeTorrents ? 'danger' : 'primary'} disabled={loading} onClick={() => void commitDuplicate()}>{deleteMedia || removeTorrents ? 'Commit destructive resolution' : 'Record preferred copy'}</Button></div></div>}

        {current.availableActions?.includes('confirm_movie_match') && <div className="resolution-block"><div className="eyebrow">MANUAL MOVIE MATCH</div><p>Search TMDB and choose the exact Movie. This changes logical identity only; the filesystem is not renamed or moved.</p><div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={tmdbQuery} onChange={(event) => setTmdbQuery(event.target.value)} placeholder="Search TMDB…" onKeyDown={(event) => { if (event.key === 'Enter') void searchTmdb() }} /></div><Button variant="ghost" onClick={() => void searchTmdb()} disabled={loading || !tmdbQuery.trim()}>Search</Button></div>{tmdbMatches.length > 0 && <div className="history-list">{tmdbMatches.map((match) => <div className="history-row" key={match.tmdbId}><span className="history-line" /><div><strong>{match.title} {match.year ? `(${match.year})` : ''}</strong><span>TMDB {match.tmdbId}{match.originalTitle && match.originalTitle !== match.title ? ` · ${match.originalTitle}` : ''}</span></div><Button variant="primary" onClick={() => void confirmMovie(match)} disabled={loading}>Select</Button></div>)}</div>}</div>}

        {current.availableActions?.includes('confirm_show_match') && <div className="resolution-block"><div className="eyebrow">MANUAL SHOW MATCH</div><p>Search TMDB and choose the exact Show. This changes logical identity only; episode files and folders remain exactly where they are.</p><div className="toolbar"><div className="search-field"><Icon name="search" size={16} /><Input value={tmdbQuery} onChange={(event) => setTmdbQuery(event.target.value)} placeholder="Search TMDB Shows…" onKeyDown={(event) => { if (event.key === 'Enter') void searchTmdbShows() }} /></div><Button variant="ghost" onClick={() => void searchTmdbShows()} disabled={loading || !tmdbQuery.trim()}>Search</Button></div>{tmdbShowMatches.length > 0 && <div className="history-list">{tmdbShowMatches.map((match) => <div className="history-row" key={match.tmdbId}><span className="history-line" /><div><strong>{match.title} {match.year ? `(${match.year})` : ''}</strong><span>TMDB {match.tmdbId}{match.originalTitle && match.originalTitle !== match.title ? ` · ${match.originalTitle}` : ''}</span></div><Button variant="primary" onClick={() => void confirmShow(match)} disabled={loading}>Select</Button></div>)}</div>}</div>}

        {current.code === 'PATH_MAPPING_FAILED' && <div className="resolution-block"><div className="eyebrow">PATH MAPPING</div><p>Add or adjust the qBittorrent remote path mapping under Settings → Storage Roots, then recheck this Problem. Medialogue will never guess a filesystem translation.</p></div>}

        {current.availableActions?.includes('choose_episode_winner') && Array.isArray(current.details?.media_file_ids) && <div className="resolution-block"><div className="eyebrow">EPISODE DUPLICATE</div><p>Choose a preferred mapping. The physical duplicate remains a Problem until the losing file is actually removed.</p><div className="detail-actions">{current.details.media_file_ids.map((id) => <Button variant="ghost" key={String(id)} onClick={() => void chooseEpisodeWinner(String(id))}>Prefer {String(id)}</Button>)}</div></div>}

        <div className="detail-actions">{current.availableActions?.includes('recheck') && <Button variant="ghost" icon="refresh" onClick={() => void recheckProblem()} disabled={loading}>Recheck evidence</Button>}<Button variant="ghost" onClick={() => setMessage('Problem remains open; no media was changed.')}>Keep unresolved</Button></div>
      </> : <EmptyState icon="alert" title="No unresolved problems" detail="The live reconciliation queue is clear." />}</Panel></div>
  </Page>
}

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<Movie[]>([])
  const [selectedMovie, setSelectedMovie] = useState<Movie | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<InteractiveSearchJob | null>(null)
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    const timer = window.setTimeout(() => {
      api.movies(query).then((items) => { if (alive) setCandidates(items.slice(0, 12)) }).catch(() => { if (alive) setCandidates([]) })
    }, 180)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [query])

  useEffect(() => { api.downloadClients().then((items) => setClients(items.filter((item) => item.enabled))).catch(() => undefined) }, [])

  useEffect(() => {
    if (!jobId) return
    let alive = true
    const refresh = () => api.searchJob(jobId).then((value) => { if (alive) setJob(value) }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not refresh search results.') })
    void refresh()
    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const onSearchEvent = (event: Event) => {
      const message = event as MessageEvent<string>
      try {
        const payload = JSON.parse(message.data) as { entity_id?: string }
        if (!payload.entity_id || payload.entity_id === jobId) void refresh()
      } catch { void refresh() }
    }
    ;['search.result', 'search.indexer_status', 'search.completed', 'search.failed', 'search.cancelled'].forEach((name) => stream.addEventListener(name, onSearchEvent))
    const fallback = window.setInterval(() => { if (job?.status === 'running' || job?.status === 'queued' || !job) void refresh() }, 2500)
    return () => {
      alive = false
      window.clearInterval(fallback)
      stream.close()
    }
  }, [jobId, job?.status])

  const start = async () => {
    if (!selectedMovie) { setError('Choose a movie from your library first.'); return }
    setBusy(true); setError(''); setJob(null)
    try {
      const started = await api.startMovieSearch(selectedMovie.id)
      setJobId(started.job_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start the interactive search.') }
    finally { setBusy(false) }
  }

  const movieClients = clients.filter((client) => client.scope === 'movies')
  const indexerCount = job?.indexers.length ?? 0
  const resultCount = job?.resultTotal ?? 0
  return <Page title="Interactive Search" subtitle="Search configured Prowlarr-backed indexers and choose exactly what to download.">
    <Panel className="search-hero">
      <div className="search-hero-icon"><Icon name="search" size={23} /></div>
      <div className="search-hero-copy"><div className="eyebrow">MANUAL WORKFLOW</div><h2>Find a release for a library title</h2><p>Search is read-only. A torrent is submitted only when you explicitly choose a result; the selected qBittorrent client keeps control of its destination.</p></div>
      <div className="search-form">
        <Select value="Movie" disabled><option>Movie</option></Select>
        <Input placeholder="Find a movie in your library…" value={query} onChange={(event) => { setQuery(event.target.value); setSelectedMovie(null) }} />
        <Button variant="primary" icon="search" onClick={() => void start()} disabled={busy || !selectedMovie}>{busy ? 'Starting…' : 'Search indexers'}</Button>
      </div>
      <div className="search-targets">
        {selectedMovie ? <button className="search-target selected" onClick={() => setSelectedMovie(null)}><strong>{selectedMovie.title}</strong><span>{selectedMovie.year}</span><Badge tone={statusTone(selectedMovie.status)}>{selectedMovie.status}</Badge></button> : candidates.slice(0, 6).map((movie) => <button className="search-target" key={movie.id} onClick={() => { setSelectedMovie(movie); setQuery(`${movie.title} (${movie.year})`) }}><strong>{movie.title}</strong><span>{movie.year}</span><Badge tone={statusTone(movie.status)}>{movie.status}</Badge></button>)}
      </div>
    </Panel>
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {job ? <Panel title="Search results" eyebrow={`${indexerCount} INDEXER${indexerCount === 1 ? '' : 'S'} · ${resultCount} RESULT${resultCount === 1 ? '' : 'S'}`} action={<Badge tone={job.status === 'completed' ? 'green' : job.status === 'failed' ? 'red' : 'amber'}>{job.status}</Badge>}>
      <div className="indexer-strip">{job.indexers.map((indexer) => <span key={indexer.id} title={indexer.error}><span className={`health-dot ${indexer.status === 'completed' ? 'green' : indexer.status === 'failed' || indexer.status === 'timeout' ? 'red' : 'amber'}`} />{indexer.name} · {indexer.status}{indexer.status === 'completed' ? ` · ${indexer.results} results` : ''}</span>)}<span className="muted">Custom Format matches, profile scores, overrides, and minimum-quality status are frozen with each result. No score hides or blocks a release.</span></div>
      {job.results.length ? <div className="search-table-wrap"><table className="data-table search-results"><thead><tr><th>Release</th><th>Indexer</th><th>Age</th><th>Size</th><th>Quality</th><th>Edition</th><th>Group</th><th>CF Score</th><th>Seeders</th><th /></tr></thead><tbody>{job.results.map((result) => <SearchResultRows key={result.id} result={result} clients={movieClients} onSubmitted={() => jobId && api.searchJob(jobId).then(setJob).catch(() => undefined)} onError={setError} />)}</tbody></table></div> : <EmptyState icon="search" title={job.status === 'completed' ? 'No releases returned' : 'Waiting for indexers'} detail={job.status === 'completed' ? 'The enabled Movie indexers returned no results.' : 'Results appear here as each indexer responds.'} />}
    </Panel> : <EmptyState icon="search" title="Select a movie to search" detail="Interactive searches fan out to every enabled Movie/Both indexer. One slow or failed indexer does not block the others." />}
  </Page>
}

function SearchResultRows({ result, clients, onSubmitted, onError }: { result: InteractiveSearchResult; clients: DownloadClient[]; onSubmitted: () => void; onError: (message: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const [selectedClient, setSelectedClient] = useState(clients[0]?.id ?? '')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!selectedClient && clients[0]) setSelectedClient(clients[0].id) }, [clients, selectedClient])
  const submit = async (clientId?: string) => {
    const destination = clientId ?? (clients.length === 1 ? clients[0]?.id : selectedClient)
    if (!destination) { setChoosing(true); return }
    setBusy(true); onError('')
    try { await api.downloadSearchResult(result.id, destination); setChoosing(false); onSubmitted() }
    catch (reason) { onError(reason instanceof Error ? reason.message : 'Could not submit the selected release.') }
    finally { setBusy(false) }
  }
  const snapshotFormats = Array.isArray(result.customFormatSnapshot.formats)
    ? result.customFormatSnapshot.formats.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : []
  const matchedFormats = snapshotFormats.filter((item) => item.matched === true)
  return <>
    <tr className={searchResultNeedsWarning(result.minimumQualityMet, result.warnings) ? 'search-result-warning' : ''} onClick={() => setExpanded((value) => !value)}>
      <td><div className="release-result"><strong>{result.title}</strong>{result.selectedAt && <Badge tone="green">Submitted</Badge>}{result.minimumQualityMet === false && <Badge tone="amber">Below minimum</Badge>}{result.warnings.length > 0 && <Badge tone="amber">{result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'}</Badge>}</div></td>
      <td>{result.indexerName}</td><td className="muted">{formatSearchAge(result.publishedAt)}</td><td>{formatSearchBytes(result.size)}</td><td><Badge tone={result.quality ? 'green' : 'neutral'}>{result.quality ?? 'Unknown'}</Badge></td><td>{result.edition ?? '—'}</td><td>{result.releaseGroup ?? 'NoGroup'}</td><td><span className={result.customFormatScore !== undefined && result.customFormatScore < 0 ? 'score-negative' : 'score-positive'}><strong>{result.customFormatScore !== undefined && result.customFormatScore > 0 ? '+' : ''}{result.customFormatScore ?? 0}</strong></span>{snapshotFormats.length ? <small className="table-sub">{matchedFormats.length}/{snapshotFormats.length} matched</small> : null}</td><td>{result.seeders ?? '—'}</td>
      <td onClick={(event) => event.stopPropagation()}>{result.selectedAt ? <Badge tone="green">Sent</Badge> : choosing && clients.length > 1 ? <div className="search-client-chooser"><Select value={selectedClient} onChange={(event) => setSelectedClient(event.target.value)}>{clients.map((client) => <option value={client.id} key={client.id}>{client.name}</option>)}</Select><Button variant="primary" icon="download" onClick={() => void submit()} disabled={busy}>{busy ? 'Sending…' : 'Send'}</Button></div> : <Button variant="secondary" icon="download" onClick={() => clients.length > 1 ? setChoosing(true) : void submit()} disabled={busy || clients.length === 0}>{busy ? 'Sending…' : clients.length === 0 ? 'No Movie client' : 'Download'}</Button>}</td>
    </tr>
    {expanded && <tr className="search-result-detail"><td colSpan={10}><div className="search-detail-grid"><div><div className="eyebrow">PARSER RESULT</div><pre>{JSON.stringify(result.parser, null, 2)}</pre></div><div><div className="eyebrow">SEARCH-TIME EVIDENCE</div><dl><dt>Indexer</dt><dd>{result.indexerName}</dd><dt>Quality</dt><dd>{result.quality ?? 'Unknown'}</dd><dt>Edition</dt><dd>{result.edition ?? 'None'}</dd><dt>Release group</dt><dd>{result.releaseGroup ?? 'NoGroup'}</dd><dt>Quality Profile</dt><dd>{result.qualityProfileName ?? 'No profile assigned'}</dd><dt>Minimum quality</dt><dd>{result.minimumQuality ? `${result.minimumQuality} · ${result.minimumQualityMet === false ? 'below minimum' : result.minimumQualityMet === true ? 'meets minimum' : 'not comparable'}` : 'No minimum'}</dd><dt>Custom Format score</dt><dd><strong className={(result.customFormatScore ?? 0) < 0 ? 'score-negative' : 'score-positive'}>{(result.customFormatScore ?? 0) > 0 ? '+' : ''}{result.customFormatScore ?? 0}</strong></dd></dl><div className="search-cf-evidence"><div className="field-label">Custom Format snapshot <span>Immutable search-time evidence</span></div>{snapshotFormats.length ? snapshotFormats.map((format, index) => {
      const name = typeof format.custom_format_name === 'string' ? format.custom_format_name : `Custom Format ${index + 1}`
      const conditions = Array.isArray(format.conditions) ? format.conditions.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object')) : []
      const matched = format.matched === true
      const profileScore = Number(format.profile_score ?? 0); const overrideScore = format.override_score === null || format.override_score === undefined ? undefined : Number(format.override_score); const effectiveScore = Number(format.effective_score ?? profileScore); const contribution = Number(format.contribution ?? (matched ? effectiveScore : 0)); return <div className="search-cf-format" key={`${String(format.custom_format_id ?? name)}-${index}`}><div className="search-cf-format-head"><Badge tone={matched ? 'green' : 'neutral'}>{matched ? 'Matched' : 'Not matched'}</Badge><strong>{name}</strong><span className={contribution < 0 ? 'score-negative' : 'score-positive'}>{contribution > 0 ? '+' : ''}{contribution}</span><small>Profile {profileScore > 0 ? '+' : ''}{profileScore}{overrideScore !== undefined ? ` · Override ${overrideScore > 0 ? '+' : ''}${overrideScore}` : ''}</small></div>{conditions.map((condition, conditionIndex) => <div className={`search-cf-condition ${condition.effective_result === true ? 'pass' : 'fail'}`} key={`${String(condition.condition_id ?? conditionIndex)}`}><Icon name={condition.effective_result === true ? 'check' : 'close'} size={13} /><span>{String(condition.name || condition.condition_type || 'Condition')}</span><small>{String(condition.reason || (condition.effective_result === true ? 'Passed' : 'Failed'))}</small></div>)}</div>
    }) : <span className="muted">No enabled Custom Formats were eligible when this result was discovered.</span>}</div>{result.warnings.length > 0 && <div className="search-warning-list">{result.warnings.map((warning) => <span key={warning}><Icon name="alert" size={14} />{warning}</span>)}</div>}</div></div></td></tr>}
  </>
}

function formatSearchBytes(bytes?: number) {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}

function formatSearchAge(value?: string) {
  if (!value) return '—'
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return '—'
  const hours = Math.max(0, Math.floor((Date.now() - timestamp) / 3_600_000))
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  return `${days}d`
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
      setMessage(`Submitted ${restoreItem.torrentName} to ${result.client_name}. qBittorrent will be observed on the next poll.`)
      setRestoreItem(null)
      await load()
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Restore failed.') }
    finally { setBusy(false) }
  }
  const retryArchive = async (item: TorrentArchiveItem) => {
    setBusy(true); setMessage('')
    try {
      const result = await api.retryTorrentArchive(item.id)
      setMessage(result.archive_state === 'archived' ? `Recovery archive completed for ${item.torrentName}.` : (result.message ?? 'Archive retry failed.'))
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


export function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabs = ['General', 'Storage Roots', 'Metadata', 'Plex', 'qBittorrent', 'Indexers', 'Schedules', 'Security', 'Backup / Recovery']
  const requested = searchParams.get('tab')
  const [tab, setTab] = useState(requested && tabs.includes(requested) ? requested : 'General')
  useEffect(() => { const value = searchParams.get('tab'); if (value && tabs.includes(value)) setTab(value) }, [searchParams])
  const chooseTab = (item: string) => { setTab(item); const next = new URLSearchParams(searchParams); next.set('tab', item); setSearchParams(next, { replace: true }) }
  return <Page title="Settings" subtitle="Configure integrations, storage boundaries, and safe operating defaults.">
    {searchParams.get('setup') === '1' && <div className="setup-return"><Icon name="activity" size={16} /><span>You are configuring first-run setup.</span><Link to="/setup">Back to setup checklist</Link></div>}
    <div className="settings-layout"><nav className="settings-nav">{tabs.map((item) => <button className={tab === item ? 'active' : ''} onClick={() => chooseTab(item)} key={item}><Icon name={item === 'Storage Roots' ? 'folder' : item === 'Security' || item === 'Backup / Recovery' ? 'shield' : item === 'General' ? 'settings' : 'server'} size={16} />{item}<Icon name="chevron" size={14} /></button>)}</nav><Panel className="settings-panel" eyebrow="SETTINGS" title={tab}>{tab === 'General' ? <GeneralSettings /> : tab === 'Storage Roots' ? <StorageSettings /> : tab === 'Metadata' ? <MetadataSettings /> : tab === 'Plex' ? <PlexSettings /> : tab === 'qBittorrent' ? <QBittorrentSettings /> : tab === 'Indexers' ? <IndexerSettings /> : tab === 'Schedules' ? <ScheduleSettings /> : tab === 'Security' ? <SecuritySettings /> : <BackupRecoverySettings />}</Panel></div>
  </Page>
}

function GeneralSettings() { return <><div className="settings-section"><div><h3>Application behavior</h3><p>Medialogue opens to Movies and remains observational until you explicitly enable Active Operations or choose an action.</p></div><div><Link className="button button-secondary" to="/setup">Open setup checklist</Link></div></div><div className="settings-section safeguard"><div className="setting-icon"><Icon name="shield" size={18} /></div><div><h3>Leave-in-place safeguard</h3><p>Scanning never moves, renames, copies, hardlinks, imports, or creates sidecars. Filesystem deletion exists only behind the explicit destructive preview/commit workflow.</p><Badge tone="green">Always enforced</Badge></div></div><div className="settings-section"><div><h3>Operations mode</h3><p>The global toggle in the top bar resets to SAFE/OFF whenever Medialogue starts.</p></div><div className="setting-control"><Badge tone="amber">Off after restart</Badge></div></div></> }

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
  return <><div className="integration-hero"><div className="integration-icon"><Icon name="shield" size={21} /></div><div><h3>Administrator security</h3><p>Medialogue uses one local administrator account and secure session cookies.</p></div><Badge tone={security?.default_password_warning ? 'amber' : 'green'}>{security?.default_password_warning ? 'Default password active' : 'Password changed'}</Badge></div><div className="settings-form security-form"><label><span className="field-label">Current password</span><Input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" /></label><label><span className="field-label">New password</span><Input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" /></label><label><span className="field-label">Confirm new password</span><Input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" /></label></div>{message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}<div className="settings-footer"><Button variant="primary" onClick={change} disabled={busy || !currentPassword || !newPassword}>{busy ? 'Changing…' : 'Change password'}</Button></div><div className="settings-note"><Icon name="shield" size={16} /><span>The default admin/adminadmin login remains allowed until you change it; Medialogue keeps the warning visible rather than forcing a password change.</span></div></>
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
  return <><div className="settings-section"><div><h3>qBittorrent reconciliation</h3><p>Polling is lightweight and configured independently per qBittorrent instance. This is the schedule that drives incoming-download and completion observations.</p></div></div>{clients.length ? <div className="root-list">{clients.map((client) => <div className="root-row" key={client.id}><div className="root-icon"><Icon name="download" size={16} /></div><div><strong>{client.name}</strong><span>{client.scope === 'movies' ? 'Movies' : 'Shows'} · {client.url}</span></div><Select value={String(client.pollIntervalSeconds ?? 15)} onChange={(event) => void updateInterval(client, Number(event.target.value))}><option value="5">Every 5 seconds</option><option value="10">Every 10 seconds</option><option value="15">Every 15 seconds</option><option value="30">Every 30 seconds</option><option value="60">Every minute</option><option value="300">Every 5 minutes</option></Select></div>)}</div> : <EmptyState icon="download" title="No polling schedules yet" detail="Add a qBittorrent client first; each client owns its actual reconciliation interval." />}<div className="settings-section"><div><h3>Full library scans</h3><p>Full storage-root scans are intentionally manual in v1. Medialogue does not create a cron scan merely because a root exists. This keeps a fresh install inactive and prevents unexpected large NAS scans.</p></div><Badge tone="green">Manual by design</Badge></div><div className="settings-note"><Icon name="activity" size={16} /><span>Advanced cron scheduling is not attached to any hidden automatic scan job in v1. Future scheduled job types must be explicit and observable.</span></div>{message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}</>
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
    try { const job = await api.startScan(root.id); setMessage(`Scan queued: ${job.job_id}`) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not start scan.') }
  }
  const addRoot = async () => {
    try {
      const created = await api.createStorageRoot({ name, path, media_type: mediaType, access_mode: accessMode })
      setRoots((items) => [...items, created]); setAdding(false); setMappingRootId((value) => value || created.id); setMessage(`${created.name} added.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not add root.') }
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

  return <>
    <div className="storage-head"><div><h3>Configured storage roots</h3><p>Only these explicit roots may be scanned by the application. Offline roots preserve known media as degraded instead of flooding the Missing queue.</p></div><Button variant="primary" icon="plus" onClick={() => setAdding((value) => !value)}>{adding ? 'Cancel' : 'Add root'}</Button></div>
    {adding && <div className="settings-form"><label><span className="field-label">Name</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label><label><span className="field-label">Container path</span><Input value={path} onChange={(event) => setPath(event.target.value)} /></label><label><span className="field-label">Media type</span><Select value={mediaType} onChange={(event) => setMediaType(event.target.value as 'movies' | 'shows')}><option value="movies">Movies</option><option value="shows">Shows</option></Select></label><label><span className="field-label">Access</span><Select value={accessMode} onChange={(event) => setAccessMode(event.target.value as 'read_only' | 'read_write')}><option value="read_only">Read-only — detection only</option><option value="read_write">Read/write — allow explicit confirmed deletion</option></Select></label><div className="settings-footer"><Button variant="primary" onClick={addRoot}>Save root</Button></div></div>}
    <div className="root-list">{roots.map((root) => { const health = (root.last_health ?? 'unchecked').toLowerCase(); const offline = health === 'offline' || health === 'unavailable'; const affected = root.affected_media_count ?? root.media_affected ?? 0; return <div className={`root-row ${offline ? 'root-row-offline' : ''}`} key={root.id}><div className="root-icon"><Icon name="folder" size={17} /></div><div><strong>{root.name}</strong><span>{root.resolved_root_path}</span>{offline && <small className="root-outage-copy">Storage Root Offline · {affected} media affected</small>}</div><Badge tone={health === 'available' || health === 'healthy' ? 'green' : offline ? 'red' : health === 'degraded' ? 'amber' : 'neutral'}>{root.last_health ?? 'Unchecked'}</Badge><Badge tone="neutral">{root.access_mode === 'read_only' ? 'Read-only' : 'Read/write'}</Badge><span className="root-items">{root.media_type}{root.missing_media_count !== undefined ? ` · ${root.missing_media_count} missing` : ''}</span><Button variant="ghost" icon="play" onClick={() => scan(root)}>Scan now</Button></div>})}{!roots.length && <EmptyState title="No storage roots configured" detail="Add an explicit container-visible Movie or Show root to begin discovery." />}</div>

    <div className="storage-head"><div><h3>Remote path mappings</h3><p>Translate paths reported by qBittorrent into the container-visible paths above. This is the UI used to fix PATH_MAPPING_FAILED without editing the database.</p></div><Button variant="ghost" icon="plus" onClick={() => setAddingMapping((value) => !value)}>{addingMapping ? 'Cancel' : 'Add mapping'}</Button></div>
    {addingMapping && <div className="settings-form"><label><span className="field-label">Name</span><Input value={mappingName} onChange={(event) => setMappingName(event.target.value)} /></label><label><span className="field-label">qBittorrent client</span><Select value={mappingClientId} onChange={(event) => setMappingClientId(event.target.value)}><option value="">All qBittorrent clients</option>{clients.map((client) => <option value={client.id} key={client.id}>{client.name}</option>)}</Select></label><label><span className="field-label">Remote prefix reported by qBittorrent</span><Input value={remotePrefix} onChange={(event) => setRemotePrefix(event.target.value)} placeholder="/downloads/movies" /></label><label><span className="field-label">Local/container prefix</span><Input value={localPrefix} onChange={(event) => setLocalPrefix(event.target.value)} placeholder="/media/movies" /></label><label><span className="field-label">Storage root</span><Select value={mappingRootId} onChange={(event) => setMappingRootId(event.target.value)}><option value="">No explicit root</option>{roots.map((root) => <option value={root.id} key={root.id}>{root.name} · {root.resolved_root_path}</option>)}</Select></label><div className="settings-footer"><Button variant="primary" onClick={addMapping}>Save mapping</Button></div></div>}
    <div className="root-list">{mappings.map((mapping) => { const client = clients.find((item) => item.id === mapping.integration_id); return <div className="root-row" key={mapping.id}><div className="root-icon"><Icon name="arrow" size={17} /></div><div><strong>{mapping.name}</strong><span>{mapping.remote_prefix} → {mapping.local_prefix}</span><small>{client?.name ?? 'All qBittorrent clients'}</small></div><Badge tone={mapping.enabled ? 'green' : 'neutral'}>{mapping.enabled ? 'Enabled' : 'Disabled'}</Badge><span className="root-items">qBittorrent</span><Button variant="ghost" onClick={() => void removeMapping(mapping)}>Remove</Button></div>})}{!mappings.length && <EmptyState title="No remote path mappings" detail="Only add one when qBittorrent reports a path that differs from the media path visible inside Medialogue." />}</div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    <div className="settings-note"><Icon name="shield" size={16} /><span>Root scans and path mappings never move or rename media. Read/write roots only permit deletion through an explicit destructive preview and confirmation.</span></div>
  </>
}

function MetadataSettings() {
  const [configuration, setConfiguration] = useState<{ configured: boolean; api_key_configured: boolean; enabled: boolean; health: string; latency_ms?: number; last_error?: string; revision?: number } | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => { api.tmdbConfiguration().then((value) => { setConfiguration(value); setEnabled(value.enabled) }).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'Could not load TMDB settings.')) }, [])
  const save = async () => { setBusy(true); setMessage('Saving TMDB settings…'); try { const value = await api.saveTmdb({ api_key: apiKey || undefined, enabled, expected_revision: configuration?.revision }); setConfiguration(value); setApiKey(''); setMessage('TMDB settings saved.') } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save TMDB settings.') } finally { setBusy(false) } }
  const test = async () => { setBusy(true); setMessage('Testing TMDB connection…'); try { const value = await api.testTmdb({ api_key: apiKey || undefined }); setMessage(value.status === 'healthy' ? `TMDB is reachable${value.latency_ms ? ` in ${value.latency_ms} ms` : ''}.` : value.message ?? `TMDB status: ${value.status}.`) } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not test TMDB.') } finally { setBusy(false) } }
  const healthTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : 'neutral'
  return <><div className="integration-hero"><div className="integration-icon"><Icon name="search" size={21} /></div><div><h3>TMDB metadata</h3><p>TMDB is the primary identity source for newly discovered movies. Local parsing alone does not create a matched title.</p></div><Badge tone={healthTone}>{!configuration?.configured ? 'Not configured' : configuration.health}</Badge></div><div className="settings-form"><label><span className="field-label">API key</span><Input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={configuration?.api_key_configured ? 'Stored server-side · leave blank to preserve' : 'Required for automatic matching'} /></label><label className="setting-control"><span className="field-label">Enabled</span><button type="button" aria-pressed={enabled} className={`toggle ${enabled ? 'is-on' : ''}`} onClick={() => setEnabled((value) => !value)}><span /></button></label></div>{configuration?.last_error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{configuration.last_error}</span></div>}{message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}<div className="settings-footer"><Button variant="ghost" icon="refresh" onClick={test} disabled={busy}>Test connection</Button><Button variant="primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save TMDB'}</Button></div><div className="settings-note"><Icon name="shield" size={16} /><span>The API key is stored server-side and is never returned to the browser after saving.</span></div></>
}

function PlexSettings() {
  const [configuration, setConfiguration] = useState<import('./types').PlexConfiguration | null>(null)
  const [url, setUrl] = useState('http://plex:32400')
  const [token, setToken] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    api.plexConfiguration().then((value) => {
      setConfiguration(value)
      if (value.url) setUrl(value.url)
      setEnabled(value.enabled)
    }).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'Could not load Plex settings.')).finally(() => setLoading(false))
  }, [])
  const test = async () => {
    setBusy(true); setMessage('Testing Plex connection…')
    try {
      const result = await api.testPlex({ url: url || undefined, token: token || undefined })
      setMessage(result.status === 'healthy' ? `Plex is reachable${result.latency_ms ? ` in ${result.latency_ms} ms` : ''}.` : result.message ?? `Plex status: ${result.status}.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not test Plex.') }
    finally { setBusy(false) }
  }
  const save = async () => {
    if (!url.trim()) { setMessage('Enter a Plex server URL.'); return }
    setBusy(true); setMessage('Saving Plex settings…')
    try {
      const value = await api.savePlex({ url: url.trim(), token: token || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setToken(''); setMessage('Plex settings saved.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save Plex settings.') }
    finally { setBusy(false) }
  }
  const refreshHealth = async () => {
    setBusy(true); setMessage('Refreshing Plex health…')
    try {
      const result = await api.refreshPlexHealth()
      setConfiguration((value) => value ? { ...value, health: result.status, machine_identifier: result.machine_identifier, latency_ms: result.latency_ms, last_error: result.message } : value)
      setMessage(result.status === 'healthy' ? 'Plex health is healthy.' : result.message ?? `Plex status: ${result.status}.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not refresh Plex health.') }
    finally { setBusy(false) }
  }
  const healthTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : configuration?.health === 'degraded' ? 'amber' : 'neutral'
  return <>
    <div className="integration-hero"><div className="integration-icon"><Icon name="tv" size={21} /></div><div><h3>Plex connection</h3><p>Read-only library verification uses exact media paths before title fallback.</p></div><Badge tone={healthTone}>{loading ? 'Loading' : !configuration?.configured ? 'Not configured' : configuration.health}</Badge></div>
    <div className="settings-form"><label><span className="field-label">URL</span><Input placeholder="http://plex:32400" value={url} onChange={(event) => setUrl(event.target.value)} /></label><label><span className="field-label">API token</span><Input type="password" placeholder={configuration?.token_configured ? 'Stored server-side · leave blank to preserve' : 'Required'} value={token} onChange={(event) => setToken(event.target.value)} /></label><label className="setting-control"><span className="field-label">Enabled</span><button type="button" aria-pressed={enabled} className={`toggle ${enabled ? 'is-on' : ''}`} onClick={() => setEnabled((value) => !value)}><span /></button></label></div>
    {configuration?.last_error && <div className="settings-note"><Icon name="alert" size={16} /><span>{configuration.last_error}</span></div>}
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    <div className="settings-footer"><Button variant="ghost" icon="refresh" onClick={test} disabled={busy}>{busy ? 'Working…' : 'Test connection'}</Button>{configuration?.configured && configuration.enabled && <Button variant="ghost" onClick={refreshHealth} disabled={busy}>Refresh health</Button>}<Button variant="primary" onClick={save} disabled={busy}>Save changes</Button></div>
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
  pollIntervalSeconds: 15,
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
    pollIntervalSeconds: client.pollIntervalSeconds ?? 15,
  }
}

function QBittorrentSettings() {
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<DownloadClientDraft>(emptyDownloadClient)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadClients = async (keepSelection = true) => {
    setLoading(true)
    try {
      const items = await api.downloadClients()
      setClients(items)
      const nextId = keepSelection && selectedId && items.some((item) => item.id === selectedId) ? selectedId : items[0]?.id ?? null
      setSelectedId(nextId)
      const selected = items.find((item) => item.id === nextId)
      setDraft(selected ? draftFromDownloadClient(selected) : emptyDownloadClient)
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
    setMessage('')
  }
  const updateDraft = <K extends keyof DownloadClientDraft>(key: K, value: DownloadClientDraft[K]) => setDraft((current) => ({ ...current, [key]: value }))
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
      setSelectedId(value.id); setDraft(draftFromDownloadClient(value)); setMessage(`${value.name} saved. Password is stored server-side and never returned to the browser.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save qBittorrent client.'); setMessage('') }
    finally { setBusy(false) }
  }
  const test = async () => {
    setBusy(true); setError(''); setMessage('Testing qBittorrent connection…')
    try {
      const result = selectedId ? await api.testDownloadClient(selectedId) : await api.testDownloadClient(undefined, payload())
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
      setSelectedId(next?.id ?? null); setDraft(next ? draftFromDownloadClient(next) : emptyDownloadClient); setMessage('Client configuration removed; torrent history was not changed.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove qBittorrent client.') }
    finally { setBusy(false) }
  }
  const selected = clients.find((item) => item.id === selectedId)
  const healthTone = selected?.health === 'healthy' ? 'green' : selected?.health === 'unavailable' || selected?.health === 'offline' ? 'red' : selected?.health === 'degraded' ? 'amber' : 'neutral'
  return <div className="qbit-settings">
    <div className="integration-hero"><div className="integration-icon"><Icon name="download" size={21} /></div><div><h3>qBittorrent clients</h3><p>Observe multiple qBittorrent instances without importing, moving, or changing downloaded media.</p></div><Badge tone={selected ? healthTone : 'neutral'}>{loading ? 'Loading' : selected ? selected.health : `${clients.length} configured`}</Badge></div>
    <div className="qbit-layout">
      <div className="qbit-client-list"><div className="qbit-list-head"><span className="eyebrow">DOWNLOAD CLIENTS</span><Button variant="ghost" icon="plus" onClick={() => { setSelectedId(null); setDraft(emptyDownloadClient); setMessage(''); setError('') }}>Add client</Button></div>{clients.map((client) => <button className={`qbit-client-item ${selectedId === client.id ? 'selected' : ''}`} key={client.id} onClick={() => selectClient(client.id)}><span className={`health-dot ${client.health === 'healthy' ? 'green' : client.health === 'unavailable' ? 'red' : 'amber'}`} /><span><strong>{client.name}</strong><small>{client.scope === 'movies' ? 'Movies' : 'Shows'} · {client.url}</small></span><Badge tone={client.enabled ? 'green' : 'neutral'}>{client.enabled ? 'On' : 'Off'}</Badge></button>)}{!clients.length && !loading && <EmptyState icon="download" title="No qBittorrent clients" detail="Add a client to observe downloads." />}</div>
      <div className="qbit-editor"><div className="qbit-editor-head"><div><div className="eyebrow">{selected ? 'CLIENT CONFIGURATION' : 'NEW CLIENT'}</div><h3>{selected ? selected.name : 'Add qBittorrent client'}</h3></div>{selected && <Badge tone={healthTone}>{selected.health}</Badge>}</div><div className="settings-form"><label><span className="field-label">Display name</span><Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} placeholder="qbit-movies-1" /></label><label><span className="field-label">URL</span><Input value={draft.url} onChange={(event) => updateDraft('url', event.target.value)} placeholder="http://qbittorrent:8080" /></label><label><span className="field-label">Username</span><Input value={draft.username} onChange={(event) => updateDraft('username', event.target.value)} autoComplete="off" /></label><label><span className="field-label">Password</span><Input type="password" value={draft.password} onChange={(event) => updateDraft('password', event.target.value)} autoComplete="new-password" placeholder={selected?.password_configured ? 'Stored server-side · leave blank to preserve' : 'Required for authenticated clients'} /></label><label><span className="field-label">Scope</span><Select value={draft.scope} onChange={(event) => updateDraft('scope', event.target.value as DownloadClientDraft['scope'])}><option value="movies">Movies</option><option value="shows">Shows</option></Select></label><label><span className="field-label">Category</span><Input value={draft.category} onChange={(event) => updateDraft('category', event.target.value)} placeholder="media" /></label><label><span className="field-label">Tags <span>comma separated</span></span><Input value={draft.tags} onChange={(event) => updateDraft('tags', event.target.value)} placeholder="movies, managed" /></label><label><span className="field-label">Polling interval</span><Select value={String(draft.pollIntervalSeconds)} onChange={(event) => updateDraft('pollIntervalSeconds', Number(event.target.value))}><option value="5">5 seconds</option><option value="10">10 seconds</option><option value="15">15 seconds</option><option value="30">30 seconds</option><option value="60">1 minute</option><option value="300">5 minutes</option></Select></label><label className="setting-control"><span className="field-label">Enabled</span><button type="button" aria-pressed={draft.enabled} className={`toggle ${draft.enabled ? 'is-on' : ''}`} onClick={() => updateDraft('enabled', !draft.enabled)}><span /></button></label></div>{selected?.last_error && <div className="settings-note"><Icon name="alert" size={16} /><span>{selected.last_error}</span></div>}{error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}{message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}<div className="settings-footer"><Button variant="ghost" icon="refresh" onClick={test} disabled={busy}>{busy ? 'Working…' : 'Test connection'}</Button>{selected && <Button variant="ghost" onClick={refresh} disabled={busy}>Refresh health</Button>}{selected && <Button variant="danger" onClick={remove} disabled={busy}>Remove</Button>}<Button variant="primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save client'}</Button></div><div className="settings-note"><Icon name="shield" size={16} /><span>Credentials are write-only in the UI. Leaving Password blank on an existing client preserves its stored secret.</span></div></div>
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
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<IndexerDraft>(emptyIndexer)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const items = await api.indexers()
      setIndexers(items)
      setError('')
      setSelectedId((current) => {
        const selected = items.find((item) => item.id === current) ?? items[0]
        setDraft(selected ? draftFromIndexer(selected) : emptyIndexer)
        return selected?.id ?? null
      })
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
    setMessage('')
    setError('')
  }
  const updateDraft = <K extends keyof IndexerDraft>(key: K, value: IndexerDraft[K]) => setDraft((current) => ({ ...current, [key]: value }))

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
      setSelectedId(next?.id ?? null)
      setDraft(next ? draftFromIndexer(next) : emptyIndexer)
      setMessage('Indexer configuration removed.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove indexer.') }
    finally { setBusy(false) }
  }

  const healthTone = selected?.health === 'healthy' ? 'green' : selected?.health === 'unavailable' || selected?.health === 'offline' ? 'red' : selected?.health === 'degraded' ? 'amber' : 'neutral'
  return <div className="qbit-settings indexer-settings">
    <div className="integration-hero"><div className="integration-icon"><Icon name="search" size={21} /></div><div><h3>Prowlarr-backed indexers</h3><p>Add individual Torznab endpoints manually. Medialogue does not import all Prowlarr configuration automatically.</p></div><Badge tone={selected ? healthTone : 'neutral'}>{loading ? 'Loading' : selected ? selected.health : `${indexers.length} configured`}</Badge></div>
    <div className="qbit-layout">
      <div className="qbit-client-list"><div className="qbit-list-head"><span className="eyebrow">INDEXERS</span><Button variant="ghost" icon="plus" onClick={() => { setSelectedId(null); setDraft(emptyIndexer); setMessage(''); setError('') }}>Add indexer</Button></div>{indexers.map((item) => <button className={`qbit-client-item ${selectedId === item.id ? 'selected' : ''}`} key={item.id} onClick={() => selectIndexer(item.id)}><span className={`health-dot ${item.health === 'healthy' ? 'green' : item.health === 'unavailable' || item.health === 'offline' ? 'red' : 'amber'}`} /><span><strong>{item.name}</strong><small>{item.scope === 'both' ? 'Movies + Shows' : item.scope === 'movies' ? 'Movies' : 'Shows'} · {item.torznabUrl}</small></span><Badge tone={item.enabled ? 'green' : 'neutral'}>{item.enabled ? 'On' : 'Off'}</Badge></button>)}{!indexers.length && !loading && <EmptyState icon="search" title="No indexers configured" detail="Add a Prowlarr/Torznab endpoint to enable Interactive Search." />}</div>
      <div className="qbit-editor"><div className="qbit-editor-head"><div><div className="eyebrow">{selected ? 'INDEXER CONFIGURATION' : 'NEW INDEXER'}</div><h3>{selected ? selected.name : 'Add indexer'}</h3></div>{selected && <Badge tone={healthTone}>{selected.health}</Badge>}</div><div className="settings-form"><label><span className="field-label">Display name</span><Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} placeholder="PTP" /></label><label><span className="field-label">Torznab URL</span><Input value={draft.torznabUrl} onChange={(event) => updateDraft('torznabUrl', event.target.value)} placeholder="http://prowlarr:9696/1/api" /></label><label><span className="field-label">API key</span><Input type="password" value={draft.apiKey} onChange={(event) => updateDraft('apiKey', event.target.value)} autoComplete="new-password" placeholder={selected?.apiKeyConfigured ? 'Stored server-side · leave blank to preserve' : 'Prowlarr API key'} /></label><label><span className="field-label">Scope</span><Select value={draft.scope} onChange={(event) => updateDraft('scope', event.target.value as IndexerScope)}><option value="movies">Movies</option><option value="shows">Shows</option><option value="both">Movies + Shows</option></Select></label><label><span className="field-label">Timeout</span><Select value={String(draft.timeoutSeconds)} onChange={(event) => updateDraft('timeoutSeconds', Number(event.target.value))}><option value="10">10 seconds</option><option value="15">15 seconds</option><option value="20">20 seconds</option><option value="30">30 seconds</option></Select></label><label className="setting-control"><span className="field-label">Enabled</span><button type="button" aria-pressed={draft.enabled} className={`toggle ${draft.enabled ? 'is-on' : ''}`} onClick={() => updateDraft('enabled', !draft.enabled)}><span /></button></label></div>{selected?.lastError && <div className="settings-note"><Icon name="alert" size={16} /><span>{selected.lastError}</span></div>}{error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}{message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}<div className="settings-footer"><Button variant="ghost" icon="refresh" onClick={() => void test()} disabled={busy}>{busy ? 'Working…' : 'Test connection'}</Button>{selected && <Button variant="danger" onClick={() => void remove()} disabled={busy}>Remove</Button>}<Button variant="primary" onClick={() => void save()} disabled={busy}>{busy ? 'Saving…' : 'Save indexer'}</Button></div><div className="settings-note"><Icon name="shield" size={16} /><span>API keys are write-only in the UI. Leaving the field blank on an existing indexer preserves the stored key.</span></div></div>
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
    <div className="integration-hero"><div className="integration-icon"><Icon name="shield" size={21} /></div><div><h3>Recovery Bundle</h3><p>Export the database, torrent recovery evidence, application configuration, and a human-readable library inventory as one ZIP.</p></div><Badge tone={capabilities?.supported ? 'green' : capabilities ? 'red' : 'neutral'}>{capabilities?.supported ? 'Ready' : capabilities ? 'Unavailable' : 'Checking'}</Badge></div>
    <div className="settings-section safeguard"><div className="setting-icon"><Icon name="alert" size={18} /></div><div><h3>Sensitive backup</h3><p>The bundle contains a physical PostgreSQL base backup and integration credentials. Anyone with the ZIP should be treated as having access to your Medialogue configuration and database.</p><Badge tone="amber">Store securely</Badge></div></div>
    {capabilities && <div className="settings-section"><div><h3>Backup compatibility</h3><p>Medialogue uses PostgreSQL-supported physical backup tooling. It never recursively copies a live PGDATA directory.</p></div><div className="settings-form"><label><span className="field-label">PostgreSQL server</span><Input readOnly value={capabilities.postgresServerVersion ?? capabilities.databaseBackend} /></label><label><span className="field-label">pg_basebackup</span><Input readOnly value={capabilities.pgBasebackupVersion ?? 'Unavailable'} /></label><label><span className="field-label">Migration revision</span><Input readOnly value={capabilities.migrationRevision ?? 'Unknown'} /></label><label><span className="field-label">Temporary download retention</span><Input readOnly value={`${capabilities.retentionHours} hours`} /></label></div></div>}
    {capabilities?.reasons.length ? <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{capabilities.reasons.join(' ')}</span></div> : null}
    <div className="settings-section"><div><h3>Bundle contents</h3><p>The export is intended for disaster recovery and manual verification, not automatic mass redownload.</p></div><div className="recovery-content-list"><span>database/physical-base-backup/</span><span>torrent-archive/</span><span>manifests/</span><span>config/application-config-export.json</span><span>inventory/library-inventory.json</span><span>inventory/torrent-archive-inventory.json</span><span>backup-metadata.json</span></div></div>
    {job && <div className="settings-section"><div><h3>Latest export</h3><p>{job.error || job.detail || 'Recovery export job is persisted in the Jobs history.'}</p></div><div className="recovery-job"><div className="button-row"><Badge tone={stateTone}>{job.state}</Badge>{job.stage && <Badge tone="neutral">{job.stage.replaceAll('_', ' ')}</Badge>}</div>{job.progress !== undefined && <div className="job-progress-line"><Progress value={job.progress} tone={job.state === 'completed' ? 'green' : 'blue'} /><span>{job.progress}%</span></div>}{bundleSize && <span className="muted">Bundle size: {bundleSize}</span>}{expiresAt && <span className="muted">Temporary download available until {expiresAt}</span>}{sha && <code className="recovery-hash">SHA-256: {sha}</code>}{job.state === 'completed' && <Button variant="primary" icon="download" onClick={() => { window.location.href = api.recoveryDownloadUrl(job.id) }}>Download Recovery Bundle</Button>}</div></div>}
    {message && <div className="settings-note"><Icon name="shield" size={16} /><span>{message}</span></div>}
    <div className="settings-footer"><Button variant="ghost" icon="refresh" onClick={() => void loadCapabilities()}>Recheck</Button><Button variant="primary" icon="download" disabled={busy || !capabilities?.supported || job?.state === 'running' || job?.state === 'queued'} onClick={() => void start()}>{busy ? 'Starting…' : 'Export Recovery Bundle'}</Button></div>
  </div>
}


function Page({ title, subtitle, action, back, backTo = '/movies', children }: { title: string; subtitle: string; action?: React.ReactNode; back?: string; backTo?: string; children: React.ReactNode }) { const navigate = useNavigate(); return <div className="page"><div className="page-heading">{back && <button className="back-link" onClick={() => navigate(backTo)}><Icon name="arrow" size={15} />{back}</button>}<div className="heading-copy"><div className="eyebrow">MEDIALOGUE / {title.toUpperCase()}</div><h1>{title}</h1><p>{subtitle}</p></div>{action && <div className="heading-actions">{action}</div>}</div>{children}</div> }
