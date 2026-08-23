import type { ApiErrorShape, CustomFormat, CustomFormatCondition, CustomFormatConditionEvaluation, CustomFormatConditionType, CustomFormatEvaluation, CustomFormatScope, CustomFormatSection, CustomFormatTestAllResult, CustomFormatTestResult, Download, DownloadClient, DownloadClientScope, DownloadClientTestResult, HealthIndicator, EventHistoryItem, IncomingDownload, Indexer, IndexerScope, IndexerTestResult, Job, MediaProfileSettings, Movie, MovieDirectory, MovieEvent, MovieRelease, PlexConfiguration, PlexTestResult, Problem, QualityDefinition, QualityProfile, ReconciliationAggregate, ReconciliationEvidence, Show, Season, Episode, EpisodeMedia, TMDBShowLookup, TMDBMovieLookup, DuplicateResolvePreview, StorageRoot, RemotePathMapping, TorrentArchiveItem, RecoveryCapabilities, Tag, SetupStatus } from '../types'

export class ApiError extends Error {
  status: number
  /** Machine-readable code from the API error envelope, when present. */
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function readCookie(name: string): string | undefined {
  const prefix = `${encodeURIComponent(name)}=`
  return document.cookie.split('; ').find((cookie) => cookie.startsWith(prefix))?.slice(prefix.length)
}

async function ensureCsrf(): Promise<string | undefined> {
  const existing = readCookie('csrf_token') ?? readCookie('csrf') ?? readCookie('medialogue_csrf')
  if (existing) return decodeURIComponent(existing)
  try {
    await fetch('/api/v1/auth/csrf', { credentials: 'include' })
  } catch {
    // The caller will receive the original API error; CSRF is optional for read-only/demo APIs.
  }
  const refreshed = readCookie('csrf_token') ?? readCookie('csrf') ?? readCookie('medialogue_csrf')
  return refreshed ? decodeURIComponent(refreshed) : undefined
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
    const csrf = await ensureCsrf()
    if (csrf) headers.set('X-CSRF-Token', csrf)
    if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    let payload: ApiErrorShape = {}
    try { payload = await response.json() as ApiErrorShape } catch { /* non-json response */ }
    throw new ApiError((payload.error?.message ?? payload.detail ?? payload.message ?? response.statusText) || 'Request failed', response.status, payload.error?.code)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export interface LoginResponse { user?: { username: string; default_password?: boolean }; authenticated?: boolean }

type JsonRecord = Record<string, unknown>

function record(value: unknown): JsonRecord {
  return value && typeof value === 'object' ? value as JsonRecord : {}
}

function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : value === undefined || value === null ? fallback : String(value)
}

function regexLiteral(value: unknown): string {
  return textValue(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : Number.isFinite(Number(value)) ? Number(value) : fallback
}

function optionalNumber(value: unknown): number | undefined {
  if (value === undefined || value === null || value === '') return undefined
  const result = Number(value)
  return Number.isFinite(result) ? result : undefined
}

function optionalBoolean(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : value === undefined || value === null ? undefined : ['true', '1', 'yes', 'on'].includes(String(value).toLowerCase())
}

function dateValue(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : value instanceof Date ? value.toISOString() : undefined
}

function percentValue(value: unknown): number | undefined {
  const result = optionalNumber(value)
  if (result === undefined) return undefined
  return Math.max(0, Math.min(100, result <= 1 ? result * 100 : result))
}

function normalizedMovieState(value: unknown): Movie['status'] {
  const state = textValue(value, 'Missing').toLowerCase()
  if (state.includes('duplicate')) return 'Duplicate'
  if (state.includes('conflict')) return 'Conflict'
  if (state.includes('present') || state.includes('current') || state.includes('available')) return 'Present'
  return 'Missing'
}

function normalizedPlexState(value: unknown): Movie['plex'] {
  const state = textValue(value, 'unknown').toLowerCase()
  if (state.includes('match') || state === 'verified' || state === 'healthy') return 'Verified'
  if (state.includes('multiple_versions') || state.includes('multiple versions')) return 'Multiple versions'
  if (state.includes('conflict')) return 'Conflict'
  if (state.includes('unavailable') || state.includes('offline')) return 'Unavailable'
  if (state.includes('not_found') || state.includes('not found') || state.includes('not-in-plex')) return 'Not in Plex'
  return 'Pending'
}

function normalizeDirectory(value: unknown): MovieDirectory {
  const item = record(value)
  return {
    id: textValue(item.id || item.resource_id) || undefined,
    path: textValue(item.path || item.resolved_path || item.resolvedPath, 'Unknown path'),
    exists: item.exists !== false && item.path_exists !== false,
    missingSince: dateValue(item.missing_since || item.missingSince),
    files: Array.isArray(item.files) ? item.files.map((file) => textValue(file)).filter(Boolean) : undefined,
  }
}

function normalizeRelease(value: unknown): MovieRelease {
  const item = record(value)
  const directories = Array.isArray(item.directories) ? item.directories.map(normalizeDirectory) : []
  return {
    id: textValue(item.id || item.resource_id || item.release_id),
    name: textValue(item.name || item.raw_release_name || item.release_name, 'Unnamed release'),
    edition: textValue(item.edition || item.effective_edition) || undefined,
    quality: textValue(item.quality || item.quality_name) || undefined,
    releaseGroup: textValue(item.release_group || item.releaseGroup) || undefined,
    state: textValue(item.state || item.release_state, 'unknown'),
    confidence: percentValue(item.confidence),
    firstSeenAt: dateValue(item.first_seen_at || item.firstSeenAt),
    directories,
    torrentState: textValue(item.torrent_state || item.torrentStatus) || undefined,
    plexState: textValue(item.plex_state || item.plexStatus) || undefined,
    originalCustomFormatScore: optionalNumber(item.original_custom_format_score ?? item.originalCustomFormatScore),
    currentCustomFormatScore: optionalNumber(item.current_custom_format_score ?? item.currentCustomFormatScore),
    selectionSnapshot: Object.keys(record(item.selection_snapshot || item.selectionSnapshot)).length ? record(item.selection_snapshot || item.selectionSnapshot) : undefined,
  }
}

function normalizeIncoming(value: unknown): IncomingDownload | undefined {
  const item = record(value)
  if (!Object.keys(item).length) return undefined
  return {
    id: textValue(item.id || item.resource_id || item.torrent_id) || undefined,
    name: textValue(item.name || item.release_name, 'Incoming release'),
    client: textValue(item.client || item.client_name || item.download_client || item.download_client_name, 'Unknown client'),
    progress: percentValue(item.progress ?? item.progress_percent) ?? 0,
    quality: textValue(item.quality || item.quality_name) || undefined,
    edition: textValue(item.edition || item.effective_edition) || undefined,
    state: textValue(item.state || item.status) || undefined,
    eta: textValue(item.eta) || (item.eta_seconds !== undefined ? formatDuration(item.eta_seconds) : undefined),
    path: textValue(item.path || item.save_path || item.resolved_save_path || item.content_path) || undefined,
    mediaState: textValue(item.media_state || item.mediaStatus) || undefined,
    torrentState: textValue(item.torrent_state || item.torrentStatus) || undefined,
    kind: (textValue(item.incoming_kind || item.kind).toLowerCase() === 'replacement' ? 'replacement' : 'release'),
  }
}

function normalizeEvidence(value: unknown): ReconciliationEvidence {
  const item = record(value)
  const code = textValue(item.code || item.reason || item.reason_code, 'RECONCILIATION_REVIEW')
  const rawSeverity = textValue(item.severity, 'medium').toLowerCase()
  return {
    id: textValue(item.id || item.problem_id) || undefined,
    code,
    title: textValue(item.title || item.reason_title || item.message, code.replaceAll('_', ' ')),
    detail: textValue(item.detail || item.message || item.explanation, 'Evidence needs review.'),
    severity: rawSeverity === 'error' || rawSeverity === 'high' ? 'high' : rawSeverity === 'info' || rawSeverity === 'low' ? 'low' : 'medium',
    subject: textValue(item.subject || item.entity_name || item.entity_title) || undefined,
    source: textValue(item.source || item.evidence_source) || undefined,
  }
}

function normalizeEvent(value: unknown): MovieEvent {
  const item = record(value)
  const details = record(item.details)
  return {
    id: textValue(item.id || item.event_id) || undefined,
    type: textValue(item.type || item.event_type, 'movie.event'),
    message: textValue(item.message || item.summary, 'State updated.'),
    details: Object.keys(details).length ? details : undefined,
    createdAt: dateValue(item.created_at || item.createdAt || item.timestamp),
  }
}

function normalizeReconciliation(value: unknown): ReconciliationAggregate | undefined {
  const item = record(value)
  if (!Object.keys(item).length) return undefined
  return {
    state: textValue(item.state || item.status) || undefined,
    incomingCount: optionalNumber(item.incoming_count ?? item.incomingCount),
    missingCount: optionalNumber(item.missing_count ?? item.missingCount),
    degradedCount: optionalNumber(item.degraded_count ?? item.degradedCount),
    replacedCount: optionalNumber(item.replaced_count ?? item.replacedCount),
    duplicateCount: optionalNumber(item.duplicate_count ?? item.duplicateCount),
    qbitMediaDisagreement: optionalBoolean(item.qbit_media_disagreement ?? item.qbitMediaDisagreement ?? item.media_torrent_disagreement),
    qbitMediaDetail: textValue(item.qbit_media_detail || item.qbitMediaDetail || item.media_torrent_detail) || undefined,
    plexBlocked: optionalBoolean(item.plex_blocked ?? item.plexBlocked),
    plexBlockDetail: textValue(item.plex_block_detail || item.plexBlockDetail) || undefined,
    rootOffline: optionalBoolean(item.root_offline ?? item.rootOffline),
    rootAffectedCount: optionalNumber(item.root_affected_count ?? item.rootAffectedCount ?? item.affected_media_count),
  }
}



function normalizeEpisodeMedia(value: unknown): EpisodeMedia {
  const item = record(value)
  return {
    mediaFileId: textValue(item.media_file_id || item.mediaFileId),
    showReleaseId: textValue(item.show_release_id || item.showReleaseId) || undefined,
    path: textValue(item.path, 'Unknown path'),
    exists: item.exists !== false,
    quality: textValue(item.quality) || undefined,
    releaseGroup: textValue(item.release_group || item.releaseGroup) || undefined,
    releaseName: textValue(item.release_name || item.releaseName) || undefined,
    releaseScope: (textValue(item.release_scope || item.releaseScope) || undefined) as EpisodeMedia['releaseScope'],
    mappedEpisodeNumbers: (Array.isArray(item.mapped_episode_numbers) ? item.mapped_episode_numbers : Array.isArray(item.mappedEpisodeNumbers) ? item.mappedEpisodeNumbers : []).map((value) => numberValue(value)).filter((value) => value > 0),
    manualMapping: item.manual_mapping === true || item.manualMapping === true,
  }
}

function normalizeEpisode(value: unknown): Episode {
  const item = record(value)
  const state = textValue(item.presence_state || item.status, 'missing').toLowerCase()
  return {
    id: textValue(item.id),
    seasonNumber: numberValue(item.season_number || item.seasonNumber),
    episodeNumber: numberValue(item.episode_number || item.episodeNumber),
    title: textValue(item.title) || undefined,
    airDate: dateValue(item.air_date || item.airDate),
    tmdbId: optionalNumber(item.tmdb_id || item.tmdbId),
    tvdbId: optionalNumber(item.tvdb_id || item.tvdbId),
    monitored: item.monitored !== false,
    status: state.includes('present') ? 'Present' : state.includes('conflict') ? 'Conflict' : state.includes('unmatched') ? 'Unmatched' : 'Missing',
    revision: numberValue(item.revision, 1),
    quality: textValue(item.quality) || undefined,
    plex: normalizedPlexState(item.plex_state || item.plex),
    media: Array.isArray(item.media) ? item.media.map(normalizeEpisodeMedia) : [],
  }
}

function normalizeSeason(value: unknown): Season {
  const item = record(value)
  const episodes = Array.isArray(item.episodes) ? item.episodes.map(normalizeEpisode) : []
  return {
    id: textValue(item.id),
    seasonNumber: numberValue(item.season_number || item.seasonNumber),
    title: textValue(item.title) || undefined,
    monitored: item.monitored !== false,
    counted: item.counted !== false,
    revision: numberValue(item.revision, 1),
    episodeCount: numberValue(item.episode_count || item.episodeCount, episodes.length),
    presentCount: numberValue(item.present_count || item.presentCount, episodes.filter((episode) => episode.status === 'Present').length),
    missingCount: numberValue(item.missing_count || item.missingCount, episodes.filter((episode) => episode.status !== 'Present').length),
    episodes,
  }
}

function normalizeShow(value: unknown): Show {
  const item = record(value)
  const seasons = Array.isArray(item.seasons) ? item.seasons.map(normalizeSeason) : []
  const eventValues = Array.isArray(item.recent_events) ? item.recent_events : []
  const problemValues = Array.isArray(item.problems) ? item.problems : []
  return {
    id: textValue(item.resource_id || item.id),
    internalId: textValue(item.id) || undefined,
    title: textValue(item.title, 'Untitled show'),
    year: numberValue(item.year, 0),
    poster: textValue(item.poster_ref || item.poster) || undefined,
    seasons: numberValue(item.season_count, seasons.length),
    episodesPresent: numberValue(item.episodes_present, seasons.reduce((sum, season) => sum + season.presentCount, 0)),
    episodesTotal: numberValue(item.episode_count, seasons.reduce((sum, season) => sum + season.episodeCount, 0)),
    episodesMissing: optionalNumber(item.episodes_missing),
    status: normalizedMovieState(item.state) === 'Conflict' ? 'Conflict' : normalizedMovieState(item.state) === 'Present' ? 'Present' : 'Missing',
    plex: normalizedPlexState(item.plex_state),
    tmdbId: optionalNumber(item.tmdb_id),
    tvdbId: optionalNumber(item.tvdb_id),
    tmdbEpisodeGroupId: textValue(item.tmdb_episode_group_id) || undefined,
    monitored: item.monitored !== false,
    identityState: textValue(item.identity_state) || undefined,
    problemCount: optionalNumber(item.problem_count),
    overview: textValue(item.overview) || undefined,
    revision: optionalNumber(item.revision),
    seasonDetail: seasons,
    recentEvents: eventValues.map(normalizeEvent),
    problems: problemValues.map(normalizeEvidence),
    storageRoots: Array.isArray(item.storage_roots) ? item.storage_roots.map((rootValue) => { const root = record(rootValue); return { id: textValue(root.id), name: textValue(root.name), path: textValue(root.path), health: textValue(root.health) || undefined } }) : undefined,
    lastObservedAt: dateValue(item.last_observed_at),
  }
}

function normalizeTMDBShowLookup(value: unknown): TMDBShowLookup {
  const item = record(value)
  return {
    tmdbId: numberValue(item.tmdb_id),
    title: textValue(item.title),
    originalTitle: textValue(item.original_title) || undefined,
    year: optionalNumber(item.year),
    overview: textValue(item.overview) || undefined,
    posterRef: textValue(item.poster_ref) || undefined,
    director: textValue(item.director) || undefined,
    cast: Array.isArray(item.cast) ? item.cast.map((entry) => textValue(entry)).filter(Boolean) : undefined,
  }
}

function normalizeTorrentArchiveItem(value: unknown): TorrentArchiveItem {
  const item = record(value)
  return {
    id: textValue(item.id || item.torrent_id),
    releaseId: textValue(item.release_id || item.releaseId) || undefined,
    infoHash: textValue(item.info_hash),
    torrentName: textValue(item.torrent_name || item.name, 'Unnamed torrent'),
    releaseName: textValue(item.release_name) || undefined,
    mediaTitle: textValue(item.media_title) || undefined,
    mediaType: textValue(item.media_type) || undefined,
    tmdbId: optionalNumber(item.tmdb_id),
    tvdbId: optionalNumber(item.tvdb_id),
    quality: textValue(item.quality) || undefined,
    edition: textValue(item.edition) || undefined,
    releaseGroup: textValue(item.release_group) || undefined,
    tracker: textValue(item.tracker) || undefined,
    totalSize: optionalNumber(item.total_size),
    archiveState: textValue(item.archive_state, 'not_archived'),
    archivePath: textValue(item.archive_path) || undefined,
    manifestPath: textValue(item.manifest_path) || undefined,
    manifestSchemaVersion: optionalNumber(item.manifest_schema_version),
    originalDownloadClient: textValue(item.original_download_client || item.download_client_name) || undefined,
    previousReportedPath: textValue(item.previous_reported_path) || undefined,
    previousResolvedPath: textValue(item.previous_resolved_path) || undefined,
    qbitPresent: Boolean(item.qbit_present),
    firstSeenAt: dateValue(item.first_seen_at),
    lastSeenAt: dateValue(item.last_seen_at),
    completedAt: dateValue(item.completed_at),
    associationType: textValue(item.association_type) || undefined,
  }
}

function normalizeTag(value: unknown): Tag {
  const item = record(value)
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed tag'),
    createdAt: dateValue(item.created_at || item.createdAt),
  }
}

function normalizeMovie(value: unknown): Movie {
  const item = record(value)
  const releaseValues = Array.isArray(item.releases) ? item.releases : Array.isArray(item.current_releases) ? item.current_releases : Array.isArray(item.release_history) ? item.release_history : Array.isArray(item.historical_releases) ? item.historical_releases : []
  const evidenceValues = Array.isArray(item.problems) ? item.problems : Array.isArray(item.reconciliation_problems) ? item.reconciliation_problems : []
  const eventValues = Array.isArray(item.recent_events) ? item.recent_events : Array.isArray(item.events) ? item.events : []
  const reconciliation = normalizeReconciliation(item.reconciliation || item.reconciliation_summary || item.aggregate)
  const torrentHistoryValues = Array.isArray(item.torrent_history) ? item.torrent_history : []
  const incomingRaw = item.incoming || item.incoming_download || item.incoming_downloads
  const incoming = Array.isArray(incomingRaw) ? normalizeIncoming(incomingRaw[0]) : normalizeIncoming(incomingRaw)
  const state = normalizedMovieState(item.state || item.status || item.media_state)
  const plexState = normalizedPlexState(item.plex_state || item.plexStatus)
  const rootHealth = textValue(item.root_health || item.storage_root_health || item.storageHealth) || undefined
  return {
    id: textValue(item.resource_id || item.id),
    title: textValue(item.title, 'Untitled movie'),
    year: numberValue(item.year, 0),
    poster: textValue(item.poster || item.poster_ref) || undefined,
    releases: numberValue(item.release_count ?? item.releases_count, releaseValues.length),
    quality: textValue(item.current_quality || item.quality, 'Unknown quality'),
    edition: textValue(item.edition || item.current_edition) || undefined,
    status: state,
    plex: plexState,
    confidence: percentValue(item.confidence) ?? 0,
    location: textValue(item.location || item.current_path || item.path, 'No active path'),
    tmdbId: optionalNumber(item.tmdb_id) as number | undefined,
    identityState: textValue(item.identity_state) || undefined,
    monitored: optionalBoolean(item.monitored),
    tags: Array.isArray(item.tags) ? item.tags.map(normalizeTag) : [],
    problemCount: optionalNumber(item.problem_count ?? item.problems_count),
    overview: textValue(item.overview) || undefined,
    posterRef: textValue(item.poster_ref) || undefined,
    storageRoot: textValue(item.storage_root || item.root_name) || undefined,
    rootHealth,
    rootAffectedCount: optionalNumber(item.root_affected_count ?? item.rootAffectedCount ?? item.affected_media_count),
    lastObservedAt: dateValue(item.last_observed_at || item.last_seen_at || item.updated_at),
    torrentState: textValue(item.torrent_state || item.torrentStatus) || undefined,
    mediaState: textValue(item.media_state) || undefined,
    releasesDetail: releaseValues.map(normalizeRelease),
    incoming,
    problems: evidenceValues.map(normalizeEvidence),
    recentEvents: eventValues.map(normalizeEvent),
    reconciliation,
    torrentHistory: torrentHistoryValues.map(normalizeTorrentArchiveItem),
  }
}

function formatBytes(value: unknown): string {
  const bytes = numberValue(value, 0)
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = bytes
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 100 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

function formatDuration(value: unknown): string {
  const seconds = numberValue(value, -1)
  if (seconds < 0) return textValue(value, '—') || '—'
  if (!seconds || !Number.isFinite(seconds)) return '—'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes || 1}m`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

function normalizeDownloadClient(value: unknown): DownloadClient {
  const item = record(value)
  const scope = textValue(item.scope || item.media_type, 'movies').toLowerCase() === 'shows' ? 'shows' : 'movies'
  return {
    id: textValue(item.id || item.resource_id),
    name: textValue(item.name, 'Unnamed client'),
    url: textValue(item.url),
    username: textValue(item.username) || undefined,
    password_configured: Boolean(item.password_configured ?? item.has_password ?? item.password_set),
    scope,
    category: textValue(item.category) || undefined,
    tags: Array.isArray(item.tags) ? item.tags.map((tag) => textValue(tag)).filter(Boolean) : textValue(item.tags).split(',').map((tag) => tag.trim()).filter(Boolean),
    enabled: item.enabled !== false,
    health: textValue(item.health || item.status, 'unknown'),
    last_checked_at: textValue(item.last_health_checked_at || item.last_checked_at || item.checked_at) || undefined,
    last_success_at: textValue(item.last_success_at || item.last_success) || undefined,
    latency_ms: item.latency_ms === undefined ? undefined : numberValue(item.latency_ms),
    last_error: textValue(item.last_error || item.message) || undefined,
    revision: item.revision === undefined ? undefined : numberValue(item.revision),
    pollIntervalSeconds: optionalNumber(item.poll_interval_seconds ?? item.pollIntervalSeconds),
  }
}

function normalizeDownload(value: unknown): Download {
  const item = record(value)
  const reconciliation = record(item.reconciliation || item.reconciliation_evidence)
  const rawState = textValue(item.state || item.status, 'unknown').toLowerCase()
  const progressRaw = numberValue(item.progress ?? item.progress_percent, 0)
  const progress = progressRaw <= 1 ? progressRaw * 100 : progressRaw
  let state: Download['state'] = 'Downloading'
  if (rawState.startsWith('checking')) state = 'Checking'
  else if (rawState.includes('error') || rawState.includes('missing')) state = 'Error'
  else if (rawState.includes('pause') || rawState.includes('stopped')) state = 'Paused'
  else if (rawState === 'completed' || rawState === 'complete') state = 'Completed'
  else if (rawState.includes('up') || rawState.includes('seed') || rawState.includes('upload')) state = 'Seeding'
  else if (progress >= 100 && !rawState.includes('down') && !rawState.includes('meta')) state = 'Completed'
  const scope = textValue(item.kind || item.scope || item.media_type, 'Movie').toLowerCase() === 'show' || textValue(item.kind || item.scope || item.media_type).toLowerCase() === 'shows' ? 'Show' : 'Movie'
  return {
    id: textValue(item.id || item.resource_id || item.info_hash),
    name: textValue(item.name || item.release_name, 'Unnamed torrent'),
    client: textValue(item.client || item.client_name || item.download_client || item.download_client_name, 'Unknown client'),
    kind: scope,
    state,
    progress: Math.max(0, Math.min(100, progress)),
    size: textValue(item.size) || formatBytes(item.total_size ?? item.size_bytes),
    eta: textValue(item.eta) || formatDuration(item.eta_seconds ?? item.eta_seconds_remaining),
    speed: textValue(item.speed) || formatBytes(item.download_speed ?? item.dl_speed) + '/s',
    path: textValue(item.path || item.save_path || item.resolved_save_path || item.content_path, 'Unknown path'),
    quality: textValue(item.quality || item.quality_name || reconciliation.quality) || undefined,
    edition: textValue(item.edition || item.effective_edition) || undefined,
    movieId: textValue(item.movie_id || item.media_id || item.entity_id) || undefined,
    mediaState: textValue(item.media_state || item.mediaStatus || reconciliation.media_state || reconciliation.mediaStatus) || undefined,
    reconciliationState: textValue(item.reconciliation_state || item.reconciliationStatus || reconciliation.state || reconciliation.status) || undefined,
    reconciliationDetail: textValue(item.reconciliation_detail || item.reconciliationMessage || reconciliation.detail || reconciliation.message || item.message) || undefined,
    incoming: optionalBoolean(item.incoming ?? reconciliation.incoming) ?? false,
    incomingKind: textValue(item.incoming_kind || reconciliation.incoming_kind).toLowerCase() === 'replacement' ? 'replacement' : (optionalBoolean(item.incoming ?? reconciliation.incoming) ? 'release' : undefined),
  }
}

function normalizeStorageRoot(value: unknown): StorageRoot {
  const item = record(value)
  return {
    id: textValue(item.id || item.resource_id),
    name: textValue(item.name, 'Unnamed root'),
    resolved_root_path: textValue(item.resolved_root_path || item.path, 'Unknown path'),
    media_type: textValue(item.media_type || item.type, 'movies').toLowerCase() === 'shows' ? 'shows' : 'movies',
    access_mode: textValue(item.access_mode, 'read_only').toLowerCase() === 'read_write' ? 'read_write' : 'read_only',
    enabled: item.enabled !== false,
    missing_grace_checks: optionalNumber(item.missing_grace_checks) ?? 2,
    last_health: textValue(item.last_health || item.health || item.status) || undefined,
    last_scan_at: dateValue(item.last_scan_at || item.lastScanAt),
    last_health_checked_at: dateValue(item.last_health_checked_at || item.lastHealthCheckedAt),
    affected_media_count: optionalNumber(item.affected_media_count ?? item.affected_count ?? item.media_affected),
    media_affected: optionalNumber(item.media_affected ?? item.affected_media_count ?? item.affected_count),
    missing_media_count: optionalNumber(item.missing_media_count ?? item.missing_count),
    degraded_media_count: optionalNumber(item.degraded_media_count ?? item.degraded_count),
  }
}

function normalizeProblem(value: unknown): Problem {
  const item = record(value)
  const code = textValue(item.code || item.reason || item.reason_code, 'RECONCILIATION_REVIEW')
  const rawSeverity = textValue(item.severity, 'medium').toLowerCase()
  const createdAt = dateValue(item.created_at || item.createdAt)
  const details = record(item.details)
  const resolution = record(item.resolution)
  const subject = textValue(item.subject || item.entity_name || item.entity_title || details.subject || details.path, 'Affected media')
  return {
    id: textValue(item.id || item.problem_id || item.entity_id),
    code,
    title: textValue(item.title || item.reason_title || item.message, code.replaceAll('_', ' ')),
    subject,
    detail: textValue(item.detail || item.message || item.explanation, 'Evidence needs review.'),
    severity: rawSeverity === 'error' || rawSeverity === 'high' ? 'high' : rawSeverity === 'info' || rawSeverity === 'low' ? 'low' : 'medium',
    created: createdAt ? new Date(createdAt).toLocaleString() : textValue(item.created, 'Unknown time'),
    reason: textValue(item.reason) || undefined,
    status: textValue(item.status) || undefined,
    workflow: (['manual', 'choice', 'config', 'waiting'].includes(textValue(item.workflow)) ? textValue(item.workflow) : 'waiting') as Problem['workflow'],
    entityType: textValue(item.entity_type || item.entityType) || undefined,
    entityId: textValue(item.entity_id || item.entityId) || undefined,
    details: Object.keys(details).length ? details : undefined,
    resolution: Object.keys(resolution).length ? resolution : undefined,
    resolvedAt: dateValue(item.resolved_at || item.resolvedAt),
    availableActions: Array.isArray(item.available_actions ?? item.availableActions) ? ((item.available_actions ?? item.availableActions) as unknown[]).map((entry) => textValue(entry)).filter(Boolean) : undefined,
  }
}

function normalizeTMDBMovieLookup(value: unknown): TMDBMovieLookup {
  const item = record(value)
  return {
    tmdbId: numberValue(item.tmdb_id ?? item.tmdbId, 0),
    title: textValue(item.title, 'Unknown Movie'),
    originalTitle: textValue(item.original_title ?? item.originalTitle) || undefined,
    year: optionalNumber(item.year),
    overview: textValue(item.overview) || undefined,
    posterRef: textValue(item.poster_ref ?? item.posterRef) || undefined,
    director: textValue(item.director) || undefined,
    cast: Array.isArray(item.cast) ? item.cast.map((entry) => textValue(entry)).filter(Boolean) : undefined,
  }
}

function normalizeDuplicateRelease(value: unknown) {
  const item = record(value)
  const directories = Array.isArray(item.directories) ? item.directories : []
  const torrents = Array.isArray(item.torrents) ? item.torrents : []
  return {
    releaseId: textValue(item.release_id ?? item.releaseId),
    releaseName: textValue(item.release_name ?? item.releaseName),
    edition: textValue(item.edition) || undefined,
    quality: textValue(item.quality) || undefined,
    releaseGroup: textValue(item.release_group ?? item.releaseGroup) || undefined,
    state: textValue(item.state),
    directories: directories.map((entry) => { const row = record(entry); return { directoryId: textValue(row.directory_id ?? row.directoryId), path: textValue(row.path), storageRoot: textValue(row.storage_root ?? row.storageRoot), accessMode: textValue(row.access_mode ?? row.accessMode), exists: Boolean(row.exists), files: (Array.isArray(row.files) ? row.files : []).map((file) => { const f = record(file); return { relativePath: textValue(f.relative_path ?? f.relativePath), size: optionalNumber(f.size), isSymlink: Boolean(f.is_symlink ?? f.isSymlink) } }) } }),
    torrents: torrents.map((entry) => { const row = record(entry); return { torrentId: textValue(row.torrent_id ?? row.torrentId), infoHash: textValue(row.info_hash ?? row.infoHash), name: textValue(row.name), archived: Boolean(row.archived), qbitPresent: Boolean(row.qbit_present ?? row.qbitPresent), clients: Array.isArray(row.clients) ? row.clients.map((client) => textValue(client)).filter(Boolean) : [] } }),
  }
}

function normalizeDuplicatePreview(value: unknown): DuplicateResolvePreview {
  const item = record(value)
  return {
    movieId: textValue(item.movie_id ?? item.movieId),
    movieTitle: textValue(item.movie_title ?? item.movieTitle),
    winner: normalizeDuplicateRelease(item.winner),
    losers: (Array.isArray(item.losers) ? item.losers : []).map(normalizeDuplicateRelease),
    deleteMedia: Boolean(item.delete_media ?? item.deleteMedia),
    removeTorrents: Boolean(item.remove_torrents ?? item.removeTorrents),
    torrentBackupsWillBeKept: Boolean(item.torrent_backups_will_be_kept ?? item.torrentBackupsWillBeKept),
    confirmationToken: textValue(item.confirmation_token ?? item.confirmationToken),
    expiresAt: textValue(item.expires_at ?? item.expiresAt),
    warnings: Array.isArray(item.warnings) ? item.warnings.map((entry) => textValue(entry)).filter(Boolean) : [],
  }
}

function normalizeCustomFormatCondition(value: unknown): CustomFormatCondition {
  const item = record(value)
  const rawType = textValue(item.type || item.condition_type, 'release_title') as CustomFormatConditionType
  return {
    id: textValue(item.id || item.condition_id),
    name: textValue(item.name) || undefined,
    type: rawType,
    value: Array.isArray(item.value) ? item.value.map((entry) => textValue(entry)) : (item.value === undefined || item.value === null ? undefined : textValue(item.value)),
    pattern: textValue(item.pattern || item.regex) || (Array.isArray(item.value)
      ? `^(?:${item.value.map(regexLiteral).join('|')})$`
      : item.value === undefined || item.value === null ? undefined : `^(?:${regexLiteral(item.value)})$`),
    required: Boolean(item.required),
    negate: Boolean(item.negate ?? item.negated),
    caseSensitive: Boolean(item.case_sensitive ?? item.caseSensitive),
    group: textValue(item.group) || undefined,
    scoreOffset: numberValue(item.score_offset ?? item.scoreOffset),
  }
}

function normalizeCustomFormat(value: unknown): CustomFormat {
  const item = record(value)
  const rawScope = textValue(item.media_scope || item.mediaScope, 'both').toLowerCase()
  const mediaScope: CustomFormatScope = rawScope === 'movies' || rawScope === 'shows' ? rawScope : 'both'
  const conditions = Array.isArray(item.conditions) ? item.conditions.map(normalizeCustomFormatCondition) : []
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed Custom Format'),
    description: textValue(item.description) || undefined,
    mediaScope,
    enabled: item.enabled !== false,
    builtin: item.builtin === true,
    schemaVersion: numberValue(item.schema_version ?? item.schemaVersion, 1),
    conditions,
    conditionCount: numberValue(item.condition_count ?? item.conditionCount, conditions.length),
    usedByProfiles: numberValue(item.used_by_profiles ?? item.usedByProfiles, 0),
    revision: numberValue(item.revision, 1),
    createdAt: dateValue(item.created_at || item.createdAt),
    updatedAt: dateValue(item.updated_at || item.updatedAt),
  }
}

function normalizeCustomFormatSection(value: unknown): CustomFormatSection {
  const item = record(value)
  const rawIds = item.format_ids || item.formatIds
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Untitled section'),
    formatIds: (Array.isArray(rawIds) ? rawIds : []).map((entry) => textValue(entry)).filter(Boolean),
  }
}

function normalizeConditionEvaluation(value: unknown): CustomFormatConditionEvaluation {
  const item = record(value)
  return {
    conditionId: textValue(item.condition_id || item.conditionId),
    conditionType: textValue(item.condition_type || item.conditionType),
    name: textValue(item.name) || undefined,
    matched: Boolean(item.matched),
    effectiveResult: Boolean(item.effective_result ?? item.effectiveResult),
    required: Boolean(item.required),
    negated: Boolean(item.negated ?? item.negate),
    evidence: item.evidence,
    expected: item.expected,
    reason: textValue(item.reason) || undefined,
    group: textValue(item.group) || undefined,
    regexMatch: textValue(item.regex_match || item.regexMatch) || undefined,
    scoreOffset: numberValue(item.score_offset ?? item.scoreOffset),
  }
}

function normalizeCustomFormatEvaluation(value: unknown): CustomFormatEvaluation {
  const item = record(value)
  return {
    customFormatId: textValue(item.custom_format_id || item.customFormatId),
    customFormatName: textValue(item.custom_format_name || item.customFormatName),
    matched: Boolean(item.matched),
    conditions: Array.isArray(item.conditions) ? item.conditions.map(normalizeConditionEvaluation) : [],
    groupResults: Object.fromEntries(Object.entries(record(item.group_results || item.groupResults)).map(([key, result]) => [key, Boolean(result)])),
    scoreOffset: numberValue(item.score_offset ?? item.scoreOffset),
    profileScore: optionalNumber(item.profile_score ?? item.profileScore),
    contribution: optionalNumber(item.contribution),
    error: textValue(item.error) || undefined,
  }
}

function normalizeIndexer(value: unknown): Indexer {
  const item = record(value)
  const scopeValue = textValue(item.scope, 'both').toLowerCase()
  const scope: IndexerScope = scopeValue === 'movies' || scopeValue === 'shows' ? scopeValue : 'both'
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed indexer'),
    torznabUrl: textValue(item.torznab_url || item.torznabUrl),
    apiKeyConfigured: Boolean(item.api_key_configured ?? item.apiKeyConfigured),
    scope,
    enabled: item.enabled !== false,
    timeoutSeconds: numberValue(item.timeout_seconds ?? item.timeoutSeconds, 15),
    health: textValue(item.health, 'unknown'),
    lastCheckedAt: dateValue(item.last_checked_at || item.lastCheckedAt),
    lastSuccessAt: dateValue(item.last_success_at || item.lastSuccessAt),
    latencyMs: optionalNumber(item.latency_ms ?? item.latencyMs),
    lastError: textValue(item.last_error || item.lastError) || undefined,
    revision: numberValue(item.revision, 1),
  }
}

function normalizeQualityDefinition(value: unknown): QualityDefinition {
  const item = record(value)
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unknown quality'),
    resolution: textValue(item.resolution) || undefined,
    source: textValue(item.source) || undefined,
    modifier: textValue(item.modifier) || undefined,
    scanType: textValue(item.scan_type || item.scanType) || undefined,
    rank: numberValue(item.rank),
    enabled: item.enabled !== false,
  }
}

function normalizeQualityProfile(value: unknown): QualityProfile {
  const item = record(value)
  const minimum = record(item.minimum_quality_definition || item.minimumQualityDefinition)
  const scores = Array.isArray(item.custom_format_scores || item.customFormatScores) ? (item.custom_format_scores || item.customFormatScores) as unknown[] : []
  const qualities = Array.isArray(item.qualities) ? item.qualities : []
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed profile'),
    minimumQuality: Object.keys(minimum).length ? normalizeQualityDefinition(minimum) : undefined,
    qualities: qualities.map(normalizeQualityDefinition),
    customFormatScores: scores.map((value) => {
      const score = record(value)
      return {
        customFormatId: textValue(score.custom_format_id || score.customFormatId),
        customFormatName: textValue(score.custom_format_name || score.customFormatName, 'Custom Format'),
        score: numberValue(score.score),
        enabled: score.enabled !== false,
      }
    }),
    assignedTitles: numberValue(item.assigned_titles || item.assignedTitles),
    revision: numberValue(item.revision, 1),
    createdAt: dateValue(item.created_at || item.createdAt) ?? '',
    updatedAt: dateValue(item.updated_at || item.updatedAt) ?? '',
  }
}

function normalizeMediaProfileSettings(value: unknown): MediaProfileSettings {
  const item = record(value)
  const minimum = record(item.minimum_quality_definition || item.minimumQualityDefinition)
  const profileMinimum = record(item.profile_minimum_quality_definition || item.profileMinimumQualityDefinition)
  const scores = Array.isArray(item.custom_format_scores || item.customFormatScores) ? (item.custom_format_scores || item.customFormatScores) as unknown[] : []
  return {
    mediaType: textValue(item.media_type || item.mediaType, 'movies') === 'shows' ? 'shows' : 'movies',
    entityId: textValue(item.entity_id || item.entityId),
    qualityProfileId: textValue(item.quality_profile_id || item.qualityProfileId) || undefined,
    qualityProfileName: textValue(item.quality_profile_name || item.qualityProfileName) || undefined,
    minimumQuality: Object.keys(minimum).length ? normalizeQualityDefinition(minimum) : undefined,
    profileMinimumQuality: Object.keys(profileMinimum).length ? normalizeQualityDefinition(profileMinimum) : undefined,
    minimumQualityOverridden: Boolean(item.minimum_quality_overridden ?? item.minimumQualityOverridden),
    customFormatScores: scores.map((value) => {
      const score = record(value)
      return {
        customFormatId: textValue(score.custom_format_id || score.customFormatId),
        customFormatName: textValue(score.custom_format_name || score.customFormatName, 'Custom Format'),
        profileScore: numberValue(score.profile_score || score.profileScore),
        overrideScore: optionalNumber(score.override_score ?? score.overrideScore),
        effectiveScore: numberValue(score.effective_score || score.effectiveScore),
        enabled: score.enabled !== false,
      }
    }),
    revision: numberValue(item.revision),
  }
}

function customFormatConditionPayload(condition: CustomFormatCondition) {
  return {
    id: condition.id || undefined,
    name: condition.name || undefined,
    type: condition.type,
    value: undefined,
    pattern: condition.pattern || (typeof condition.value === 'string' ? condition.value : ''),
    required: condition.required,
    negate: condition.negate,
    case_sensitive: condition.caseSensitive,
    group: condition.group || undefined,
    score_offset: condition.scoreOffset,
  }
}

function customFormatPayload(format: Pick<CustomFormat, 'name' | 'description' | 'mediaScope' | 'enabled' | 'conditions'>) {
  return {
    name: format.name,
    description: format.description || null,
    media_scope: format.mediaScope,
    enabled: format.enabled,
    conditions: format.conditions.map(customFormatConditionPayload),
  }
}

interface JobPayload {
  id: string
  job_type: string
  status: Job['state']
  progress: { percent?: number; stage?: string; detail?: string }
  summary: Record<string, unknown>
  error?: { message?: string } | null
  cancellable: boolean
  updated_at: string
}

function normalizeJobPayload(item: JobPayload): Job {
  const titles: Record<string, string> = {
      plex_movie_recheck: 'Plex movie recheck',
      plex_show_recheck: 'Plex show recheck',
      plex_library_sync: 'Plex library verification',
      tmdb_show_metadata_refresh: 'TMDB metadata refresh',
      bulk_movie_operation: 'Bulk movie operation',
      qbittorrent_poll: 'qBittorrent poll',
      qbittorrent_poll_all: 'qBittorrent poll (all clients)',
      duplicate_resolution: 'Duplicate resolution',
      torrent_archive_retry: 'Torrent archive retry',
      torrent_restore: 'Torrent restore',
  }
  return {
    id: item.id,
    jobType: item.job_type,
    title: titles[item.job_type] ?? item.job_type.replaceAll('_', ' '),
    detail: typeof item.progress.detail === 'string' ? item.progress.detail : typeof item.summary.path === 'string' ? item.summary.path : typeof item.summary.message === 'string' ? item.summary.message : '',
    progress: item.progress.percent,
    stage: typeof item.progress.stage === 'string' ? item.progress.stage : undefined,
    state: item.status,
    updated: new Date(item.updated_at).toLocaleString(),
    cancellable: item.cancellable,
    error: item.error?.message,
    summary: item.summary,
  }
}

export const api = {
  login: (username: string, password: string) => request<LoginResponse>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request<void>('/api/v1/auth/logout', { method: 'POST' }),
  session: () => request<{ username: string; is_default_password?: boolean }>('/api/v1/auth/me'),
  security: () => request<{ default_password_warning: boolean; session_expires_at?: string }>('/api/v1/auth/security'),
  changePassword: (currentPassword: string, newPassword: string) => request<{ username: string; is_default_password: boolean }>('/api/v1/auth/password', { method: 'POST', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }),
  setupStatus: () => request<SetupStatus>('/api/v1/setup/status'),
  completeSetup: (complete = true) => request<SetupStatus>('/api/v1/setup/complete', { method: 'PUT', body: JSON.stringify({ complete }) }),
  health: async () => {
    const payload = await request<{ database?: { status?: string }; plex?: { configured?: boolean; status?: string; latency_ms?: number; last_success?: string }; qbittorrent?: JsonRecord; download_clients?: JsonRecord | Array<{ health?: string; enabled?: boolean }>; indexers?: { configured?: boolean; enabled?: number; healthy?: number; total?: number; status?: string }; storage_roots?: Array<{ status?: string }> }>('/integrations/health')
    const storageHealthy = (payload.storage_roots ?? []).every((root) => root.status === 'healthy' || root.status === 'available')
    const plexState = payload.plex?.configured ? payload.plex.status ?? 'unknown' : 'unknown'
    const plexDetail = !payload.plex?.configured ? 'Not configured' : plexState === 'healthy' ? `Connected${payload.plex.latency_ms ? ` · ${payload.plex.latency_ms} ms` : ''}` : plexState
    const qb = payload.qbittorrent ?? (Array.isArray(payload.download_clients) ? undefined : payload.download_clients)
    const qbClients = Array.isArray(payload.download_clients) ? payload.download_clients : undefined
    const qbConfigured = typeof qb?.configured === 'boolean' ? qb.configured : Boolean(qbClients?.length)
    const qbHealthy = numberValue(qb?.healthy_clients ?? qb?.healthy, qbClients?.filter((client) => client.enabled !== false && client.health === 'healthy').length ?? 0)
    const qbTotal = numberValue(qb?.total_clients ?? qb?.total, qbClients?.length ?? 0)
    const qbState = qbConfigured ? textValue(qb?.status, qbTotal && qbHealthy === qbTotal ? 'healthy' : qbHealthy ? 'degraded' : 'unknown') : 'unknown'
    const qbDetail = !qbConfigured ? 'Not configured' : qbTotal ? `${qbHealthy ?? 0}/${qbTotal} clients healthy` : qbState
    return {
      indicators: [
        { name: 'Plex', state: plexState === 'healthy' ? 'healthy' as const : plexState === 'unavailable' ? 'offline' as const : plexState === 'degraded' ? 'degraded' as const : 'unknown' as const, detail: plexDetail },
        { name: 'qBittorrent', state: qbState === 'healthy' ? 'healthy' as const : qbState === 'unavailable' || qbState === 'offline' ? 'offline' as const : qbState === 'degraded' ? 'degraded' as const : 'unknown' as const, detail: qbDetail },
        { name: 'Storage', state: (payload.storage_roots?.length && storageHealthy ? 'healthy' : 'unknown') as HealthIndicator['state'], detail: payload.storage_roots?.length ? `${payload.storage_roots.length} roots configured` : 'No roots configured' },
        { name: 'Indexers', state: payload.indexers?.status === 'healthy' ? 'healthy' as const : payload.indexers?.status === 'unavailable' ? 'offline' as const : payload.indexers?.status === 'degraded' ? 'degraded' as const : 'unknown' as const, detail: payload.indexers?.configured ? `${payload.indexers.healthy ?? 0}/${payload.indexers.enabled ?? 0} enabled healthy` : 'Not configured' },
      ] satisfies HealthIndicator[],
    }
  },
  jobs: async () => {
    const payload = await request<{ items: JobPayload[] }>('/api/v1/jobs')
    return payload.items.map(normalizeJobPayload)
  },
  job: (jobId: string) => request<JobPayload>(`/api/v1/jobs/${encodeURIComponent(jobId)}`).then(normalizeJobPayload),
  recoveryCapabilities: async (): Promise<RecoveryCapabilities> => {
    const payload = await request<{ supported: boolean; database_backend: string; postgres_server_version?: string | null; postgres_server_major?: number | null; pg_basebackup_available: boolean; pg_basebackup_version?: string | null; pg_basebackup_major?: number | null; migration_revision?: string | null; custom_tablespaces?: Array<Record<string, unknown>>; torrent_archive_readable: boolean; export_directory_writable: boolean; export_directory: string; retention_hours: number; reasons?: string[] }>('/api/v1/recovery/capabilities')
    return { supported: payload.supported, databaseBackend: payload.database_backend, postgresServerVersion: payload.postgres_server_version ?? undefined, postgresServerMajor: payload.postgres_server_major ?? undefined, pgBasebackupAvailable: payload.pg_basebackup_available, pgBasebackupVersion: payload.pg_basebackup_version ?? undefined, pgBasebackupMajor: payload.pg_basebackup_major ?? undefined, migrationRevision: payload.migration_revision ?? undefined, customTablespaces: payload.custom_tablespaces ?? [], torrentArchiveReadable: payload.torrent_archive_readable, exportDirectoryWritable: payload.export_directory_writable, exportDirectory: payload.export_directory, retentionHours: payload.retention_hours, reasons: payload.reasons ?? [] }
  },
  startRecoveryExport: () => request<{ job_id: string; status: string; warning: string }>('/api/v1/recovery/export', { method: 'POST' }),
  recoveryDownloadUrl: (jobId: string) => `/api/v1/recovery/exports/${encodeURIComponent(jobId)}/download`,
  cancelJob: (jobId: string) => request<JobPayload>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }).then(normalizeJobPayload),
  events: async (filters: { eventType?: string; severity?: string; entityType?: string; page?: number; pageSize?: number } = {}) => {
    const params = new URLSearchParams()
    if (filters.eventType) params.set('event_type', filters.eventType)
    if (filters.severity) params.set('severity', filters.severity)
    if (filters.entityType) params.set('entity_type', filters.entityType)
    if (filters.page) params.set('page', String(filters.page))
    params.set('page_size', String(filters.pageSize ?? 100))
    const payload = await request<{ items: Array<{ id: string; event_type: string; severity: string; entity_type: string; entity_id?: string | null; message: string; details?: Record<string, unknown>; created_at: string }>; total: number; pages: number; page: number }>(`/api/v1/events?${params.toString()}`)
    return {
      items: payload.items.map((item): EventHistoryItem => ({ id: item.id, eventType: item.event_type, severity: item.severity, entityType: item.entity_type, entityId: item.entity_id ?? undefined, message: item.message, details: item.details ?? {}, createdAt: item.created_at })),
      total: payload.total,
      pages: payload.pages,
      page: payload.page,
    }
  },
  deleteEvent: (id: string) => request<{ id: string }>(`/api/v1/events/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  clearEvents: (filters: { eventType?: string; severity?: string; entityType?: string } = {}) => {
    const params = new URLSearchParams()
    if (filters.eventType) params.set('event_type', filters.eventType)
    if (filters.severity) params.set('severity', filters.severity)
    if (filters.entityType) params.set('entity_type', filters.entityType)
    return request<{ deleted: number }>(`/api/v1/events${params.size ? `?${params.toString()}` : ''}`, { method: 'DELETE' })
  },
  movies: async (query = '', tag = '') => {
    const params = new URLSearchParams({ page_size: '250' })
    if (query) params.set('query', query)
    if (tag) params.set('tag', tag)
    const payload = await request<{ items: unknown[] }>(`/api/v1/movies?${params.toString()}`)
    return payload.items.map(normalizeMovie)
  },
  movie: async (id: string) => {
    const payload = await request<unknown>(`/api/v1/movies/${encodeURIComponent(id)}`)
    return normalizeMovie(payload)
  },
  tags: async () => (await request<unknown[]>('/api/v1/tags')).map(normalizeTag),
  createTag: (name: string) => request<unknown>('/api/v1/tags', { method: 'POST', body: JSON.stringify({ name }) }).then(normalizeTag),
  renameTag: (id: string, name: string) => request<unknown>(`/api/v1/tags/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ name }) }).then(normalizeTag),
  deleteTag: (id: string) => request<void>(`/api/v1/tags/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  addMovieTag: async (movieId: string, tagId: string) => (await request<unknown[]>(`/api/v1/movies/${encodeURIComponent(movieId)}/tags/${encodeURIComponent(tagId)}`, { method: 'POST' })).map(normalizeTag),
  removeMovieTag: async (movieId: string, tagId: string) => (await request<unknown[]>(`/api/v1/movies/${encodeURIComponent(movieId)}/tags/${encodeURIComponent(tagId)}`, { method: 'DELETE' })).map(normalizeTag),
  bulkMovies: (payload: { movie_ids: string[]; action: 'change_profile' | 'add_tags' | 'remove_tags' | 'monitor' | 'unmonitor' | 'recheck_plex' | 'reevaluate_parser' | 'reevaluate_custom_formats'; quality_profile_id?: string | null; tag_ids?: string[] }) => request<{ action: string; requested: number; updated: number; movie_ids: string[]; details: Record<string, unknown> } | { job_id: string }>('/api/v1/movies/bulk', { method: 'POST', body: JSON.stringify(payload) }),
  addMovie: (tmdbId: number) => request<unknown>('/api/v1/movies', { method: 'POST', body: JSON.stringify({ tmdb_id: tmdbId, monitored: true }) }).then(normalizeMovie),
  lookupMovies: async (query: string, year?: number) => (await request<unknown[]>(`/api/v1/movies/lookup?query=${encodeURIComponent(query)}${year ? `&year=${year}` : ''}`)).map(normalizeTMDBMovieLookup),
  previewMovieDuplicate: (movieId: string, payload: { winner_release_id: string; losing_release_ids: string[]; delete_media: boolean; remove_torrents: boolean }) => request<unknown>(`/api/v1/movies/${encodeURIComponent(movieId)}/duplicates/resolve-preview`, { method: 'POST', body: JSON.stringify(payload) }).then(normalizeDuplicatePreview),
  resolveMovieDuplicate: (movieId: string, confirmationToken: string) => request<{ job_id: string }>(`/api/v1/movies/${encodeURIComponent(movieId)}/duplicates/resolve`, { method: 'POST', body: JSON.stringify({ confirmation_token: confirmationToken }) }),
  shows: async (query = '') => {
    const payload = await request<{ items?: unknown[] } | unknown[]>(`/api/v1/shows${query ? `?query=${encodeURIComponent(query)}` : ''}`)
    const items = Array.isArray(payload) ? payload : payload.items ?? []
    return items.map(normalizeShow)
  },
  show: (resourceId: string) => request<unknown>(`/api/v1/shows/${encodeURIComponent(resourceId)}`).then(normalizeShow),
  specialsCounting: () => request<{ count_specials: boolean }>('/api/v1/shows/specials-counting'),
  setSpecialsCounting: (countSpecials: boolean) => request<{ count_specials: boolean; seasons_changed: number; shows_affected: number }>('/api/v1/shows/specials-counting', { method: 'PUT', body: JSON.stringify({ count_specials: countSpecials }) }),
  lookupShows: async (query: string) => (await request<unknown[]>(`/api/v1/shows/lookup?query=${encodeURIComponent(query)}`)).map(normalizeTMDBShowLookup),
  addShow: (tmdbId: number) => request<unknown>('/api/v1/shows', { method: 'POST', body: JSON.stringify({ tmdb_id: tmdbId, monitored: true }) }).then(normalizeShow),
  episodeOrderings: (resourceId: string) => request<Array<{ id: string | null; name: string; type_label: string; season_count: number; episode_count: number; description?: string; network?: string; selected: boolean }>>(`/api/v1/shows/${encodeURIComponent(resourceId)}/episode-orderings`),
  updateShow: (resourceId: string, payload: { monitored?: boolean; tmdb_episode_group_id?: string; expected_revision?: number }) => request<unknown>(`/api/v1/shows/${encodeURIComponent(resourceId)}`, { method: 'PATCH', body: JSON.stringify(payload) }).then(normalizeShow),
  updateSeason: (seasonId: string, payload: { monitored?: boolean; counted?: boolean; expected_revision?: number }) => request<unknown>(`/api/v1/seasons/${encodeURIComponent(seasonId)}`, { method: 'PATCH', body: JSON.stringify(payload) }).then(normalizeSeason),
  updateEpisode: (episodeId: string, payload: { monitored?: boolean; expected_revision?: number }) => request<unknown>(`/api/v1/episodes/${encodeURIComponent(episodeId)}`, { method: 'PATCH', body: JSON.stringify(payload) }).then(normalizeEpisode),
  correctEpisodeMapping: (mediaFileId: string, episodeIds: string[]) => request<{ media_file_id: string; show_release_id: string; episode_ids: string[]; episode_numbers: number[]; manual_override: boolean }>(`/api/v1/media-files/${encodeURIComponent(mediaFileId)}/episode-mappings`, { method: 'PUT', body: JSON.stringify({ episode_ids: episodeIds }) }),
  refreshShowMetadata: (resourceId: string) => request<{ job_id: string }>(`/api/v1/shows/${encodeURIComponent(resourceId)}/metadata/refresh`, { method: 'POST' }),
  recheckShowPlex: (resourceId: string) => request<{ job_id: string }>(`/api/v1/shows/${encodeURIComponent(resourceId)}/actions/recheck-plex`, { method: 'POST' }),
  startEpisodeSearch: (episodeId: string) => request<{ job_id: string }>(`/api/v1/episodes/${encodeURIComponent(episodeId)}/interactive-search`, { method: 'POST' }),
  startSeasonSearch: (seasonId: string) => request<{ job_id: string }>(`/api/v1/seasons/${encodeURIComponent(seasonId)}/interactive-search`, { method: 'POST' }),
  remotePathMappings: async () => {
    const payload = await request<{ items?: RemotePathMapping[] } | RemotePathMapping[]>('/api/v1/remote-path-mappings')
    return Array.isArray(payload) ? payload : payload.items ?? []
  },
  createRemotePathMapping: (mapping: { name: string; integration_type: 'qbittorrent' | 'plex'; integration_id?: string; remote_prefix: string; local_prefix: string; storage_root_id?: string; enabled?: boolean }) => request<RemotePathMapping>('/api/v1/remote-path-mappings', { method: 'POST', body: JSON.stringify(mapping) }),
  updateRemotePathMapping: (id: string, mapping: { name: string; integration_type: 'qbittorrent' | 'plex'; integration_id: string | null; remote_prefix: string; local_prefix: string; storage_root_id: string | null; enabled: boolean }) => request<RemotePathMapping>(`/api/v1/remote-path-mappings/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(mapping) }),
  deleteRemotePathMapping: (id: string) => request<{ id: string }>(`/api/v1/remote-path-mappings/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  storageRoots: async () => {
    const payload = await request<{ items?: unknown[] } | unknown[]>('/api/v1/storage-roots')
    const items = Array.isArray(payload) ? payload : payload.items ?? []
    return items.map(normalizeStorageRoot)
  },
  createStorageRoot: (root: { name: string; path: string; media_type: 'movies' | 'shows'; access_mode: 'read_only' | 'read_write'; enabled: boolean; missing_grace_checks: number }) => request<unknown>('/api/v1/storage-roots', { method: 'POST', body: JSON.stringify(root) }).then(normalizeStorageRoot),
  updateStorageRoot: (rootId: string, root: { name: string; path: string; media_type: 'movies' | 'shows'; access_mode: 'read_only' | 'read_write'; enabled: boolean; missing_grace_checks: number }) => request<unknown>(`/api/v1/storage-roots/${encodeURIComponent(rootId)}`, { method: 'PATCH', body: JSON.stringify(root) }).then(normalizeStorageRoot),
  deleteStorageRoot: (rootId: string) => request<{ id: string }>(`/api/v1/storage-roots/${encodeURIComponent(rootId)}`, { method: 'DELETE' }),
  startScan: (rootId: string) => request<{ job_id: string }>(`/api/v1/storage-roots/${rootId}/scan`, { method: 'POST' }),
  plexConfiguration: () => request<PlexConfiguration>('/api/v1/integrations/plex'),
  savePlex: (configuration: { url: string; token?: string; enabled: boolean; expected_revision?: number }) => request<PlexConfiguration>('/api/v1/integrations/plex', { method: 'PUT', body: JSON.stringify(configuration) }),
  testPlex: (configuration: { url?: string; token?: string }) => request<PlexTestResult>('/api/v1/integrations/plex/test', { method: 'POST', body: JSON.stringify(configuration) }),
  refreshPlexHealth: () => request<PlexTestResult>('/api/v1/integrations/plex/health/refresh', { method: 'POST' }),
  syncPlexLibrary: () => request<{ job_id: string }>('/api/v1/integrations/plex/sync', { method: 'POST' }),
  tmdbConfiguration: () => request<{ configured: boolean; api_key_configured: boolean; enabled: boolean; health: string; latency_ms?: number; last_error?: string; revision?: number }>('/api/v1/integrations/tmdb'),
  saveTmdb: (configuration: { api_key?: string; enabled: boolean; expected_revision?: number }) => request<{ configured: boolean; api_key_configured: boolean; enabled: boolean; health: string; latency_ms?: number; last_error?: string; revision?: number }>('/api/v1/integrations/tmdb', { method: 'PUT', body: JSON.stringify(configuration) }),
  testTmdb: (configuration: { api_key?: string }) => request<{ status: string; latency_ms?: number; message?: string }>('/api/v1/integrations/tmdb/test', { method: 'POST', body: JSON.stringify(configuration) }),
  refreshTmdbHealth: () => request<{ status: string; latency_ms?: number; message?: string }>('/api/v1/integrations/tmdb/health/refresh', { method: 'POST' }),
  recheckMoviePlex: (id: string) => request<{ job_id: string }>(`/api/v1/movies/${encodeURIComponent(id)}/actions/recheck-plex`, { method: 'POST' }),
  reconcileMovie: (_id: string) => request<{ job_ids?: string[]; skipped_root_ids?: string[]; active_job_ids?: string[]; uninitialized_root_ids?: string[] }>('/api/v1/reconciliation/refresh', { method: 'POST' }),
  reconcileAll: async () => {
    const payload = await request<{ job_ids?: string[]; skipped_root_ids?: string[]; active_job_ids?: string[]; uninitialized_root_ids?: string[] }>('/api/v1/reconciliation/refresh', { method: 'POST' })
    return {
      jobIds: (payload.job_ids ?? []).map(String),
      activeJobIds: (payload.active_job_ids ?? []).map(String),
      skippedRootIds: (payload.skipped_root_ids ?? []).map(String),
      uninitializedRootIds: (payload.uninitialized_root_ids ?? []).map(String),
    }
  },
  waitForJobs: async (jobIds: string[]) => {
    const pending = new Set(jobIds.filter(Boolean))
    const seen = new Set(pending)
    const finished: Job[] = []
    while (pending.size) {
      const jobs = await Promise.all(Array.from(pending, (jobId) => request<JobPayload>(`/api/v1/jobs/${encodeURIComponent(jobId)}`).then(normalizeJobPayload)))
      for (const job of jobs) {
        if (!['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state)) continue
        pending.delete(job.id)
        finished.push(job)
        // Reconciliation scans can schedule read-only Plex verification after
        // filesystem work completes. Follow those jobs as part of the same
        // evidence refresh instead of reporting completion prematurely.
        const followups = Array.isArray(job.summary?.followup_job_ids) ? job.summary.followup_job_ids : []
        for (const value of followups) {
          const followupId = String(value || '')
          if (followupId && !seen.has(followupId)) {
            seen.add(followupId)
            pending.add(followupId)
          }
        }
      }
      if (pending.size) await new Promise((resolve) => window.setTimeout(resolve, 500))
    }
    return finished
  },
  downloadClients: async () => {
    const payload = await request<{ items?: unknown[] } | unknown[]>('/api/v1/download-clients')
    const items = Array.isArray(payload) ? payload : payload.items ?? []
    return items.map(normalizeDownloadClient)
  },
  createDownloadClient: (configuration: { name: string; url: string; username?: string; password?: string; scope: DownloadClientScope; category?: string; tags: string[]; enabled: boolean; poll_interval_seconds?: number }) => request<unknown>('/api/v1/download-clients', { method: 'POST', body: JSON.stringify(configuration) }).then(normalizeDownloadClient),
  updateDownloadClient: (id: string, configuration: { name: string; url: string; username?: string; password?: string; scope: DownloadClientScope; category?: string; tags: string[]; enabled: boolean; poll_interval_seconds?: number; expected_revision?: number }) => request<unknown>(`/api/v1/download-clients/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(configuration) }).then(normalizeDownloadClient),
  testDownloadClient: (id?: string, configuration?: { url: string; username?: string; password?: string }) => request<DownloadClientTestResult>(id ? `/api/v1/download-clients/${encodeURIComponent(id)}/test` : '/api/v1/download-clients/test', { method: 'POST', ...(configuration ? { body: JSON.stringify(configuration) } : {}) }),
  refreshDownloadClient: (id: string) => request<DownloadClientTestResult>(`/api/v1/download-clients/${encodeURIComponent(id)}/health/refresh`, { method: 'POST' }),
  deleteDownloadClient: (id: string) => request<void>(`/api/v1/download-clients/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  indexers: async () => {
    const payload = await request<{ items?: unknown[] } | unknown[]>('/api/v1/indexers')
    const items = Array.isArray(payload) ? payload : payload.items ?? []
    return items.map(normalizeIndexer)
  },
  createIndexer: (configuration: { name: string; torznab_url: string; api_key: string; scope: IndexerScope; enabled: boolean; timeout_seconds?: number }) => request<unknown>('/api/v1/indexers', { method: 'POST', body: JSON.stringify(configuration) }).then(normalizeIndexer),
  updateIndexer: (id: string, configuration: { name?: string; torznab_url?: string; api_key?: string; scope?: IndexerScope; enabled?: boolean; timeout_seconds?: number; expected_revision?: number }) => request<unknown>(`/api/v1/indexers/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(configuration) }).then(normalizeIndexer),
  deleteIndexer: (id: string) => request<void>(`/api/v1/indexers/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  testIndexer: async (id?: string, configuration?: { torznab_url: string; api_key: string; timeout_seconds?: number }): Promise<IndexerTestResult> => {
    const payload = await request<{ status: string; latency_ms?: number; title?: string; message?: string }>(id ? `/api/v1/indexers/${encodeURIComponent(id)}/test` : '/api/v1/indexers/test', { method: 'POST', ...(configuration ? { body: JSON.stringify(configuration) } : {}) })
    return { status: payload.status, latencyMs: payload.latency_ms, title: payload.title, message: payload.message }
  },
  customFormats: async () => {
    const payload = await request<{ items?: unknown[] } | unknown[]>('/api/v1/custom-formats?page_size=250')
    const items = Array.isArray(payload) ? payload : payload.items ?? []
    return items.map(normalizeCustomFormat)
  },
  customFormatLayout: async () => {
    const payload = await request<{ sections?: unknown[] }>('/api/v1/custom-formats/layout')
    return (payload.sections ?? []).map(normalizeCustomFormatSection)
  },
  saveCustomFormatLayout: async (sections: CustomFormatSection[]) => {
    const payload = await request<{ sections?: unknown[] }>('/api/v1/custom-formats/layout', {
      method: 'PUT',
      body: JSON.stringify({ sections: sections.map((section) => ({ id: section.id, name: section.name, format_ids: section.formatIds })) }),
    })
    return (payload.sections ?? []).map(normalizeCustomFormatSection)
  },
  customFormat: (id: string) => request<unknown>(`/api/v1/custom-formats/${encodeURIComponent(id)}`).then(normalizeCustomFormat),
  createCustomFormat: (format: Pick<CustomFormat, 'name' | 'description' | 'mediaScope' | 'enabled' | 'conditions'>) => request<unknown>('/api/v1/custom-formats', { method: 'POST', body: JSON.stringify(customFormatPayload(format)) }).then(normalizeCustomFormat),
  updateCustomFormat: (id: string, format: Pick<CustomFormat, 'name' | 'description' | 'mediaScope' | 'enabled' | 'conditions'> & { expectedRevision?: number }) => request<unknown>(`/api/v1/custom-formats/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ ...customFormatPayload(format), expected_revision: format.expectedRevision }) }).then(normalizeCustomFormat),
  setCustomFormatEnabled: (id: string, enabled: boolean, expectedRevision?: number) => request<unknown>(`/api/v1/custom-formats/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled, expected_revision: expectedRevision }),
  }).then(normalizeCustomFormat),
  deleteCustomFormat: (id: string) => request<void>(`/api/v1/custom-formats/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  testCustomFormat: async (format: Pick<CustomFormat, 'id' | 'name' | 'description' | 'mediaScope' | 'enabled' | 'conditions'>, releaseName: string, indexer?: string): Promise<CustomFormatTestResult> => {
    const payload = await request<{ parsed: Record<string, unknown>; evaluation: unknown }>('/api/v1/custom-formats/test', { method: 'POST', body: JSON.stringify({ release_name: releaseName, indexer, custom_format: { id: format.id || undefined, ...customFormatPayload(format) } }) })
    return { parsed: payload.parsed, evaluation: normalizeCustomFormatEvaluation(payload.evaluation) }
  },
  testAllCustomFormats: async (releaseName: string, mediaScope: CustomFormatScope = 'both', indexer?: string, qualityProfileId?: string): Promise<CustomFormatTestAllResult> => {
    const payload = await request<{ parsed: Record<string, unknown>; formats: unknown[]; matched_count: number; quality_profile_id?: string | null; quality_profile_name?: string | null; total_score?: number | null }>('/api/v1/custom-formats/test-all', { method: 'POST', body: JSON.stringify({ release_name: releaseName, media_scope: mediaScope, indexer, quality_profile_id: qualityProfileId || null }) })
    return {
      parsed: payload.parsed,
      formats: payload.formats.map(normalizeCustomFormatEvaluation),
      matchedCount: payload.matched_count,
      qualityProfileId: payload.quality_profile_id || undefined,
      qualityProfileName: payload.quality_profile_name || undefined,
      totalScore: payload.total_score ?? undefined,
    }
  },
  exportCustomFormats: () => request<Record<string, unknown>>('/api/v1/custom-formats/export'),
  exportCustomFormat: (id: string) => request<Record<string, unknown>>(`/api/v1/custom-formats/${encodeURIComponent(id)}/export`),
  importCustomFormats: async (bundle: Record<string, unknown>) => {
    const payload = await request<{ imported: unknown[]; count: number }>('/api/v1/custom-formats/import', { method: 'POST', body: JSON.stringify(bundle) })
    return { imported: payload.imported.map(normalizeCustomFormat), count: payload.count }
  },
  qualityDefinitions: async () => (await request<unknown[]>('/api/v1/quality-definitions')).map(normalizeQualityDefinition),
  qualityProfiles: async () => (await request<unknown[]>('/api/v1/quality-profiles')).map(normalizeQualityProfile),
  createQualityProfile: (payload: { name: string; minimum_quality_definition_id?: string | null; quality_definition_ids?: string[]; custom_format_scores: Array<{ custom_format_id: string; score: number }> }) => request<unknown>('/api/v1/quality-profiles', { method: 'POST', body: JSON.stringify(payload) }).then(normalizeQualityProfile),
  updateQualityProfile: (id: string, payload: { name?: string; minimum_quality_definition_id?: string | null; quality_definition_ids?: string[]; custom_format_scores?: Array<{ custom_format_id: string; score: number }>; expected_revision?: number }) => request<unknown>(`/api/v1/quality-profiles/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }).then(normalizeQualityProfile),
  deleteQualityProfile: (id: string) => request<void>(`/api/v1/quality-profiles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  movieProfileSettings: (resourceId: string) => request<unknown>(`/api/v1/movies/${encodeURIComponent(resourceId)}/profile-settings`).then(normalizeMediaProfileSettings),
  saveMovieProfileSettings: (resourceId: string, payload: { quality_profile_id?: string | null; minimum_quality_definition_override_id?: string | null; custom_format_score_overrides?: Record<string, number>; expected_revision?: number }) => request<unknown>(`/api/v1/movies/${encodeURIComponent(resourceId)}/profile-settings`, { method: 'PUT', body: JSON.stringify(payload) }).then(normalizeMediaProfileSettings),
  showProfileSettings: (resourceId: string) => request<unknown>(`/api/v1/shows/${encodeURIComponent(resourceId)}/profile-settings`).then(normalizeMediaProfileSettings),
  saveShowProfileSettings: (resourceId: string, payload: { quality_profile_id?: string | null; minimum_quality_definition_override_id?: string | null; custom_format_score_overrides?: Record<string, number>; expected_revision?: number }) => request<unknown>(`/api/v1/shows/${encodeURIComponent(resourceId)}/profile-settings`, { method: 'PUT', body: JSON.stringify(payload) }).then(normalizeMediaProfileSettings),
  downloads: async () => {
    const payload = await request<{ items?: unknown[] } | unknown[]>('/api/v1/downloads')
    const items = Array.isArray(payload) ? payload : payload.items ?? []
    return items.map(normalizeDownload)
  },
  torrentArchiveHealth: async () => {
    const payload = await request<{ torrent_archive?: { status?: string; path?: string; writable?: boolean; archived?: number; tracked?: number; missing_or_failed?: number; message?: string } }>('/integrations/health')
    return payload.torrent_archive ?? { status: 'unknown' }
  },
  torrentArchive: async (query = '') => {
    const payload = await request<{ items?: unknown[] } | unknown[]>(`/api/v1/torrent-archive${query ? `?query=${encodeURIComponent(query)}` : ''}`)
    const items = Array.isArray(payload) ? payload : payload.items ?? []
    return items.map(normalizeTorrentArchiveItem)
  },
  torrentArchiveItem: async (id: string) => normalizeTorrentArchiveItem(await request<unknown>(`/api/v1/torrent-archive/${encodeURIComponent(id)}`)),
  retryTorrentArchive: (id: string) => request<{ job_id: string }>(`/api/v1/torrent-archive/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  restoreTorrentArchive: (id: string, payload: { download_client_id: string; save_path: string; category?: string; tags?: string[] }) => request<{ job_id: string }>(`/api/v1/torrent-archive/${encodeURIComponent(id)}/restore`, { method: 'POST', body: JSON.stringify(payload) }),
  problemCount: async (status = 'open') => {
    const payload = await request<{ count: number }>(`/api/v1/problems/count?status=${encodeURIComponent(status)}`)
    return numberValue(payload.count)
  },
  problemSummary: () => request<{ open: number; suppressed: number; workflows: Record<string, number> }>('/api/v1/problems/summary'),
  problemsPage: async (filters: { status?: string; page?: number; pageSize?: number; category?: string; severity?: string; workflow?: string } = {}) => {
    const params = new URLSearchParams()
    if (filters.status) params.set('status', filters.status)
    if (filters.category && filters.category !== 'all') params.set('category', filters.category)
    if (filters.severity && filters.severity !== 'all') params.set('severity', filters.severity)
    if (filters.workflow && filters.workflow !== 'all') params.set('workflow', filters.workflow)
    params.set('page', String(filters.page ?? 1))
    params.set('page_size', String(filters.pageSize ?? 100))
    const payload = await request<{ items: unknown[]; total: number; pages: number; page: number; page_size: number }>(`/api/v1/problems?${params.toString()}`)
    return { items: payload.items.map(normalizeProblem), total: payload.total, pages: payload.pages, page: payload.page, pageSize: payload.page_size }
  },
  deleteProblem: (id: string) => request<{ id: string }>(`/api/v1/problems/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  clearProblems: (filters: { status?: string; category?: string; severity?: string } = {}) => {
    const params = new URLSearchParams()
    if (filters.status) params.set('status', filters.status)
    if (filters.category && filters.category !== 'all') params.set('category', filters.category)
    if (filters.severity && filters.severity !== 'all') params.set('severity', filters.severity)
    return request<{ deleted: number }>(`/api/v1/problems${params.size ? `?${params.toString()}` : ''}`, { method: 'DELETE' })
  },
  recheckProblems: () => request<{ requested: number; job_ids: string[] }>('/api/v1/problems/recheck', { method: 'POST' }),
  resolveProblem: (id: string, action: string, payload: Record<string, unknown> = {}) => request<unknown>(`/api/v1/problems/${encodeURIComponent(id)}/resolve`, { method: 'POST', body: JSON.stringify({ action, payload }) }).then(normalizeProblem),
}

export { request }
