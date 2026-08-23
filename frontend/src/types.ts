export type HealthState = 'healthy' | 'degraded' | 'offline' | 'unknown'


export interface SetupStep {
  key: string
  title: string
  complete: boolean
  optional: boolean
  detail: string
  settings_tab?: string
}

export interface SetupStatus {
  wizard_complete: boolean
  wizard_required: boolean
  steps: SetupStep[]
}

export interface Tag {
  id: string
  name: string
  createdAt?: string
}

export interface HealthIndicator {
  name: string
  state: HealthState
  detail: string
}

export type PlexHealthState = 'healthy' | 'degraded' | 'unavailable' | 'unknown'

export interface PlexConfiguration {
  configured: boolean
  url?: string
  token_configured: boolean
  enabled: boolean
  health: PlexHealthState | string
  machine_identifier?: string
  last_checked_at?: string
  last_success_at?: string
  latency_ms?: number
  last_error?: string
  revision?: number
}

export interface PlexTestResult {
  status: string
  machine_identifier?: string
  latency_ms?: number
  message?: string
}

export interface Movie {
  id: string
  title: string
  year: number
  poster?: string
  releases: number
  quality: string
  edition?: string
  status: 'Present' | 'Missing' | 'Conflict' | 'Duplicate'
  plex: 'Verified' | 'Pending' | 'Not in Plex' | 'Multiple versions' | 'Conflict' | 'Unavailable'
  confidence: number
  location: string
  tmdbId?: number
  identityState?: string
  monitored?: boolean
  tags?: Tag[]
  problemCount?: number
  overview?: string
  posterRef?: string
  storageRoot?: string
  rootHealth?: string
  rootAffectedCount?: number
  lastObservedAt?: string
  torrentState?: string
  mediaState?: string
  releasesDetail?: MovieRelease[]
  incoming?: IncomingDownload
  problems?: ReconciliationEvidence[]
  recentEvents?: MovieEvent[]
  reconciliation?: ReconciliationAggregate
  torrentHistory?: TorrentArchiveItem[]
}

export interface MovieDirectory {
  id?: string
  path: string
  exists: boolean
  missingSince?: string
  files?: string[]
}

export interface MovieRelease {
  id: string
  name: string
  edition?: string
  quality?: string
  releaseGroup?: string
  state: string
  confidence?: number
  firstSeenAt?: string
  directories: MovieDirectory[]
  torrentState?: string
  plexState?: string
  originalCustomFormatScore?: number
  currentCustomFormatScore?: number
  selectionSnapshot?: Record<string, unknown>
}

export interface IncomingDownload {
  id?: string
  name: string
  client: string
  progress: number
  quality?: string
  edition?: string
  state?: string
  eta?: string
  path?: string
  mediaState?: string
  torrentState?: string
  kind?: 'replacement' | 'release'
}

export interface ReconciliationEvidence {
  id?: string
  code: string
  title: string
  detail: string
  severity: 'high' | 'medium' | 'low'
  subject?: string
  source?: string
}

export interface MovieEvent {
  id?: string
  type: string
  message: string
  details?: Record<string, unknown>
  createdAt?: string
}

export interface ReconciliationAggregate {
  state?: string
  incomingCount?: number
  missingCount?: number
  degradedCount?: number
  replacedCount?: number
  duplicateCount?: number
  qbitMediaDisagreement?: boolean
  qbitMediaDetail?: string
  plexBlocked?: boolean
  plexBlockDetail?: string
  rootOffline?: boolean
  rootAffectedCount?: number
}


export interface RemotePathMapping {
  id: string
  name: string
  integration_type: 'qbittorrent' | 'plex' | 'prowlarr' | 'tmdb' | 'tvdb'
  integration_id?: string
  remote_prefix: string
  local_prefix: string
  storage_root_id?: string
  enabled: boolean
}

export interface StorageRoot {
  id: string
  name: string
  resolved_root_path: string
  media_type: 'movies' | 'shows'
  access_mode: 'read_only' | 'read_write'
  enabled: boolean
  last_health?: string
  last_scan_at?: string
  last_health_checked_at?: string
  affected_media_count?: number
  media_affected?: number
  missing_media_count?: number
  degraded_media_count?: number
}

export interface EpisodeMedia {
  mediaFileId: string
  showReleaseId?: string
  path: string
  exists: boolean
  quality?: string
  releaseGroup?: string
  releaseName?: string
  releaseScope?: 'episode' | 'multi_episode' | 'season_pack' | 'other'
  mappedEpisodeNumbers: number[]
  manualMapping: boolean
}

export interface Episode {
  id: string
  seasonNumber: number
  episodeNumber: number
  title?: string
  airDate?: string
  tmdbId?: number
  tvdbId?: number
  monitored: boolean
  status: 'Present' | 'Missing' | 'Conflict' | 'Unmatched'
  revision: number
  quality?: string
  plex: 'Verified' | 'Pending' | 'Not in Plex' | 'Multiple versions' | 'Conflict' | 'Unavailable'
  media: EpisodeMedia[]
}

export interface Season {
  id: string
  seasonNumber: number
  title?: string
  monitored: boolean
  /** Whether this season contributes to the show's episode totals. */
  counted: boolean
  revision: number
  episodeCount: number
  presentCount: number
  missingCount: number
  episodes: Episode[]
}

export interface EpisodeOrdering {
  id: string | null
  name: string
  type_label: string
  season_count: number
  episode_count: number
  description?: string
  network?: string
  selected: boolean
}

export interface Show {
  id: string
  internalId?: string
  title: string
  year: number
  poster?: string
  seasons: number
  episodesPresent: number
  episodesTotal: number
  episodesMissing?: number
  status: 'Present' | 'Missing' | 'Conflict'
  plex: 'Verified' | 'Pending' | 'Not in Plex' | 'Multiple versions' | 'Conflict' | 'Unavailable'
  tmdbId?: number
  tvdbId?: number
  tmdbEpisodeGroupId?: string
  monitored?: boolean
  identityState?: string
  problemCount?: number
  overview?: string
  revision?: number
  seasonDetail?: Season[]
  recentEvents?: MovieEvent[]
  problems?: ReconciliationEvidence[]
  storageRoots?: Array<{ id: string; name: string; path: string; health?: string }>
  lastObservedAt?: string
}

export interface TMDBShowLookup {
  tmdbId: number
  title: string
  originalTitle?: string
  year?: number
  overview?: string
  posterRef?: string
  director?: string
  cast?: string[]
}

export interface Download {
  id: string
  name: string
  client: string
  kind: 'Movie' | 'Show'
  state: 'Downloading' | 'Checking' | 'Seeding' | 'Completed' | 'Paused' | 'Error'
  progress: number
  size: string
  eta: string
  speed: string
  path: string
  quality?: string
  edition?: string
  movieId?: string
  mediaState?: string
  reconciliationState?: string
  reconciliationDetail?: string
  incoming?: boolean
  incomingKind?: 'replacement' | 'release'
}

export type DownloadClientScope = 'movies' | 'shows'

export interface DownloadClient {
  id: string
  name: string
  url: string
  username?: string
  password_configured: boolean
  scope: DownloadClientScope
  category?: string
  tags: string[]
  enabled: boolean
  health: string
  last_checked_at?: string
  last_success_at?: string
  latency_ms?: number
  last_error?: string
  revision?: number
  pollIntervalSeconds?: number
}

export interface DownloadClientTestResult {
  status: string
  version?: string
  latency_ms?: number
  message?: string
}

export interface Problem {
  id: string
  code: string
  title: string
  subject: string
  detail: string
  severity: 'high' | 'medium' | 'low'
  created: string
  reason?: string
  status?: string
  workflow: 'manual' | 'choice' | 'config' | 'waiting'
  entityType?: string
  entityId?: string
  details?: Record<string, unknown>
  resolution?: Record<string, unknown>
  resolvedAt?: string
  availableActions?: string[]
}

export interface Job {
  id: string
  jobType: string
  title: string
  detail: string
  progress?: number
  state: 'running' | 'queued' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  updated: string
  cancellable: boolean
  error?: string
  summary?: Record<string, unknown>
  stage?: string
}

export interface RecoveryCapabilities {
  supported: boolean
  databaseBackend: string
  postgresServerVersion?: string
  postgresServerMajor?: number
  pgBasebackupAvailable: boolean
  pgBasebackupVersion?: string
  pgBasebackupMajor?: number
  migrationRevision?: string
  customTablespaces: Array<Record<string, unknown>>
  torrentArchiveReadable: boolean
  exportDirectoryWritable: boolean
  exportDirectory: string
  retentionHours: number
  reasons: string[]
}

export interface EventHistoryItem {
  id: string
  eventType: string
  severity: 'info' | 'warning' | 'error' | string
  entityType: string
  entityId?: string
  message: string
  details: Record<string, unknown>
  createdAt: string
}

export interface TorrentArchiveItem {
  id: string
  releaseId?: string
  infoHash: string
  torrentName: string
  releaseName?: string
  mediaTitle?: string
  mediaType?: 'movies' | 'shows' | string
  tmdbId?: number
  tvdbId?: number
  quality?: string
  edition?: string
  releaseGroup?: string
  tracker?: string
  totalSize?: number
  archiveState: 'not_archived' | 'archived' | 'failed' | string
  archivePath?: string
  manifestPath?: string
  manifestSchemaVersion?: number
  originalDownloadClient?: string
  previousReportedPath?: string
  previousResolvedPath?: string
  qbitPresent: boolean
  firstSeenAt?: string
  lastSeenAt?: string
  completedAt?: string
  associationType?: string
}

export type CustomFormatScope = 'movies' | 'shows' | 'both'

export type CustomFormatConditionType =
  | 'release_title'
  | 'release_group'
  | 'quality'
  | 'quality_modifier'
  | 'resolution'
  | 'source'
  | 'edition'
  | 'language'
  | 'indexer'
  | 'web_provider'
  | 'video_codec'
  | 'audio_codec'
  | 'audio_channels'
  | 'hdr_type'
  | 'release_attribute'

export interface CustomFormatCondition {
  id: string
  name?: string
  type: CustomFormatConditionType
  value?: string | string[]
  pattern?: string
  required: boolean
  negate: boolean
  caseSensitive: boolean
  group?: string
  scoreOffset: number
}

export interface CustomFormat {
  id: string
  name: string
  description?: string
  mediaScope: CustomFormatScope
  enabled: boolean
  schemaVersion: number
  conditions: CustomFormatCondition[]
  conditionCount: number
  usedByProfiles: number
  revision: number
  createdAt?: string
  updatedAt?: string
  /** Shipped and maintained by Medialogue: definition is read-only. */
  builtin?: boolean
}

export interface CustomFormatSection {
  id: string
  name: string
  formatIds: string[]
}

export interface CustomFormatConditionEvaluation {
  conditionId: string
  conditionType: string
  name?: string
  matched: boolean
  effectiveResult: boolean
  required: boolean
  negated: boolean
  evidence?: unknown
  expected?: unknown
  reason?: string
  group?: string
  regexMatch?: string
  scoreOffset: number
}

export interface CustomFormatEvaluation {
  customFormatId: string
  customFormatName: string
  matched: boolean
  conditions: CustomFormatConditionEvaluation[]
  groupResults: Record<string, boolean>
  scoreOffset: number
  profileScore?: number
  contribution?: number
  error?: string
}

export interface CustomFormatTestResult {
  parsed: Record<string, unknown>
  evaluation: CustomFormatEvaluation
}

export interface CustomFormatTestAllResult {
  parsed: Record<string, unknown>
  formats: CustomFormatEvaluation[]
  matchedCount: number
  qualityProfileId?: string
  qualityProfileName?: string
  totalScore?: number
}

export interface QualityDefinition {
  id: string
  name: string
  resolution?: string
  source?: string
  modifier?: string
  scanType?: string
  rank: number
  enabled: boolean
}

export interface QualityProfileScore {
  customFormatId: string
  customFormatName: string
  score: number
  enabled: boolean
}

export interface QualityProfile {
  id: string
  name: string
  minimumQuality?: QualityDefinition
  qualities: QualityDefinition[]
  customFormatScores: QualityProfileScore[]
  assignedTitles: number
  revision: number
  createdAt: string
  updatedAt: string
}

export interface MediaProfileScore {
  customFormatId: string
  customFormatName: string
  profileScore: number
  overrideScore?: number
  effectiveScore: number
  enabled: boolean
}

export interface MediaProfileSettings {
  mediaType: 'movies' | 'shows'
  entityId: string
  qualityProfileId?: string
  qualityProfileName?: string
  minimumQuality?: QualityDefinition
  profileMinimumQuality?: QualityDefinition
  minimumQualityOverridden: boolean
  customFormatScores: MediaProfileScore[]
  revision: number
}

export interface ApiErrorShape {
  detail?: string
  message?: string
  error?: { code?: string; message?: string; details?: unknown }
}

export type IndexerScope = 'movies' | 'shows' | 'both'

export interface Indexer {
  id: string
  name: string
  torznabUrl: string
  apiKeyConfigured: boolean
  scope: IndexerScope
  enabled: boolean
  timeoutSeconds: number
  health: string
  lastCheckedAt?: string
  lastSuccessAt?: string
  latencyMs?: number
  lastError?: string
  revision: number
}

export interface IndexerTestResult {
  status: string
  latencyMs?: number
  title?: string
  message?: string
}

export interface TMDBMovieLookup {
  tmdbId: number
  title: string
  originalTitle?: string
  year?: number
  overview?: string
  posterRef?: string
  director?: string
  cast?: string[]
}

export interface DuplicateFilePreview {
  relativePath: string
  size?: number
  isSymlink: boolean
}

export interface DuplicateDirectoryPreview {
  directoryId: string
  path: string
  storageRoot: string
  accessMode: string
  exists: boolean
  files: DuplicateFilePreview[]
}

export interface DuplicateTorrentPreview {
  torrentId: string
  infoHash: string
  name: string
  archived: boolean
  qbitPresent: boolean
  clients: string[]
}

export interface DuplicateReleasePreview {
  releaseId: string
  releaseName: string
  edition?: string
  quality?: string
  releaseGroup?: string
  state: string
  directories: DuplicateDirectoryPreview[]
  torrents: DuplicateTorrentPreview[]
}

export interface DuplicateResolvePreview {
  movieId: string
  movieTitle: string
  winner: DuplicateReleasePreview
  losers: DuplicateReleasePreview[]
  deleteMedia: boolean
  removeTorrents: boolean
  torrentBackupsWillBeKept: boolean
  confirmationToken: string
  expiresAt: string
  warnings: string[]
}

export interface DuplicateResolveResult {
  movieId: string
  winnerReleaseId: string
  losingReleaseIds: string[]
  duplicateResolved: boolean
  deletedDirectories: string[]
  removedTorrents: string[]
  warnings: string[]
  problemStatus: string
}
