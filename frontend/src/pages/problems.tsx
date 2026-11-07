import { useEffect, useRef, useState } from 'react'
import { pathParts } from '../lib/pathParts'
import { parserWarningMessage } from '../lib/parserWarnings'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Icon } from '../components/Icon'
import { Dropdown } from '../components/Dropdown'
import { useConfirm } from '../components/Confirm'
import { Button, EmptyState, Input, Select } from '../components/ui'
import { integrationsApi } from '../api/modules/integrations'
import { moviesApi } from '../api/modules/movies'
import { problemsApi } from '../api/modules/problems'
import { showsApi } from '../api/modules/shows'
import { systemApi } from '../api/modules/system'
import { updateUrlParams, useUrlNumber, useUrlState } from '../lib/urlState'
import type { Movie, MovieRelease, Problem } from '../types'
import { CUSTOM_EDITION, EditionDraft, Page, PosterImage, THEATRICAL_EDITION, TMDBCandidate, editionChoices, editionUpdate, tmdbPosterUrl } from './shared'
import { openLiveEvents } from '../lib/liveEvents'

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
  const warnings = warningsValue
    .map((value) => problemText(value))
    .filter((value): value is string => Boolean(value))
    .map(parserWarningMessage)
  return { title, year, identity: problemText(details.parsed_identity) ?? problemIdentity(title, year), warnings }
}

function readableEvidenceValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  if (Array.isArray(value)) return value.map(readableEvidenceValue).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

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
  // (1973)" - the name of the problem, presented as the name of the title.
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
      <strong>{identity ?? 'Not recorded'}</strong>
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
  return <section className="problems-key-details">{rows.map(([label, value, note], index) => <div className="problems-detail-row" key={`${label}-${index}`}><span>{label}</span><div><strong>{value}</strong>{note && <small>{note}</small>}</div></div>)}</section>
}

function initialEditionDraft(release: MovieRelease, options: string[]): EditionDraft {
  const edition = release.manualEditionOverride ?? release.parsedEdition ?? THEATRICAL_EDITION
  if (edition === THEATRICAL_EDITION || options.includes(edition)) return { selection: edition, custom: '' }
  return { selection: CUSTOM_EDITION, custom: edition }
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
      const result = await moviesApi.updateMovieReleaseEditions(movie.id, [update])
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
  const [plexConfiguration, setPlexConfiguration] = useState<Awaited<ReturnType<typeof integrationsApi.plexConfiguration>>>()
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
        problemsApi.problemsPage({ status: queueStatus, page: targetPage, pageSize: 250, category: reasonFilter, severity: severityFilter, workflow: queueStatus === 'open' ? workflow : 'all' }),
        problemsApi.problemSummary(),
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
    const stream = openLiveEvents()
    let timer: number | undefined
    const invalidate = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      timer = window.setTimeout(() => reloadCurrent.current(), 150)
    }
    stream.addEventListener('problem.created', invalidate); stream.addEventListener('problem.updated', invalidate); stream.addEventListener('problem.resolved', invalidate); stream.addEventListener('problem.deleted', invalidate)
    return () => { if (timer !== undefined) window.clearTimeout(timer); stream.close() }
  }, [])
  useEffect(() => { integrationsApi.plexConfiguration().then(setPlexConfiguration).catch(() => undefined) }, [])

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
      void Promise.all([moviesApi.movie(current.entityId), moviesApi.movieEditionOptions()]).then(([movie, options]) => { setDuplicateMovie(movie); setEditionOptions(options) }).catch(() => undefined)
    }
    const canMatch = current.availableActions?.some((action) => action === 'confirm_movie_match' || action === 'confirm_show_match')
    if (!canMatch || current.code === 'PLEX_IDENTITY_MISMATCH') return
    const details = current.details ?? {}
    const query = [problemText(details.parsed_title ?? details.title) ?? current.subject, problemText(details.parsed_year ?? details.year)].filter(Boolean).join(' ')
    setTmdbQuery(query)
    const candidates = Array.isArray(details.tmdb_candidates) ? details.tmdb_candidates.map(problemRecord).map(problemCandidateFromEvidence).filter((item): item is TMDBCandidate => Boolean(item)) : []
    setTmdbMatches(candidates.slice(0, 6))
    const searchCandidates = current.availableActions?.includes('confirm_show_match') ? showsApi.lookupShows(query) : moviesApi.lookupMovies(query)
    void searchCandidates.then((matches) => setTmdbMatches(matches.slice(0, 6))).catch(() => undefined)
  }, [current?.id])

  const runTmdbSearch = async () => {
    if (!current || !tmdbQuery.trim()) return
    setLoading(true); setMessage('')
    try {
      const matches = current.availableActions?.includes('confirm_show_match') ? await showsApi.lookupShows(tmdbQuery.trim()) : await moviesApi.lookupMovies(tmdbQuery.trim())
      setTmdbMatches(matches.slice(0, 6)); setSelectedTmdb(undefined)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'TMDB lookup failed.') }
    finally { setLoading(false) }
  }

  const applyTmdbMatch = async () => {
    if (!current || !selectedTmdb) return
    const action = current.availableActions?.includes('confirm_show_match') ? 'confirm_show_match' : 'confirm_movie_match'
    setLoading(true); setMessage('')
    try {
      const resolved = await problemsApi.resolveProblem(current.id, action, { tmdb_id: selectedTmdb.tmdbId })
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
      const response = await problemsApi.resolveProblem(current.id, 'recheck')
      const jobId = typeof response.resolution?.recheck_parent_job_id === 'string' ? response.resolution.recheck_parent_job_id : ''
      const jobs = jobId ? await systemApi.waitForJobs([jobId]) : []
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
    if (!resolved && current.entityId) setDuplicateMovie(await moviesApi.movie(current.entityId))
    setMessage(resolved ? 'Editions saved. The copies are different editions, so the Problem was resolved.' : 'Editions saved. At least two copies still have the same edition.')
  }

  const recheckAll = async () => {
    setLoading(true); setMessage('')
    try { const result = await problemsApi.recheckProblems(); setMessage(result.requested ? `Rechecking ${result.requested} existing Problem${result.requested === 1 ? '' : 's'}…` : 'There are no open Problems to recheck.') }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not recheck Problems.') }
    finally { setLoading(false) }
  }

  const adminAction = async (action: 'dismiss' | 'restore' | 'delete') => {
    if (!current) return
    const prompt = action === 'dismiss' ? 'Suppress this Problem fingerprint? It will not be recreated until restored.' : action === 'restore' ? 'Restore this suppressed Problem to the active queue?' : 'Permanently delete this Problem record? It may be recreated if the condition is observed again.'
    if (!await confirm({ title: prompt, confirmLabel: 'Continue' })) return
    setLoading(true); setMessage('')
    try {
      if (action === 'delete') await problemsApi.deleteProblem(current.id)
      else await problemsApi.resolveProblem(current.id, action)
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
    const request = current.availableActions?.includes('confirm_show_match') ? showsApi.lookupShows(query) : moviesApi.lookupMovies(query)
    void request.then((matches) => setTmdbMatches(matches.slice(0, 6))).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'TMDB lookup failed.'))
  }

  return <Page title="Problems" subtitle="See what is wrong, exactly where it is, and what to do next.">
    <div className="problems-toolbar"><label className="problems-search"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search issues, media, paths…" /></label><div className="problems-toolbar-rest"><Select value={reasonFilter} onChange={(event) => updateProblemView({ reason: event.target.value === 'all' ? null : event.target.value, problem: null, page: null })}><option value="all">All issue types</option><option value="duplicates">Duplicates</option><option value="identity">Identity / matching</option><option value="paths">Paths / storage</option><option value="PLEX_IDENTITY_MISMATCH">Plex conflicts</option></Select><Select value={severityFilter} onChange={(event) => updateProblemView({ severity: event.target.value === 'all' ? null : event.target.value, problem: null, page: null })}><option value="all">All priorities</option><option value="error">High priority</option><option value="warning">Medium priority</option><option value="info">Low priority</option></Select><div className="problems-toolbar-actions"><Button variant="secondary" onClick={() => updateProblemView({ problemStatus: queueStatus === 'dismissed' ? null : 'dismissed', problem: null, page: null })}>{queueStatus === 'dismissed' ? 'Back to active' : `Suppressed${summary.suppressed ? ` (${summary.suppressed})` : ''}`}</Button><Button variant="ghost" icon="refresh" onClick={() => void recheckAll()} disabled={loading || !summary.open}>{loading ? 'Working…' : 'Check all again'}</Button></div></div></div>
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
