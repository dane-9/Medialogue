export type HealthState = 'healthy' | 'degraded' | 'offline' | 'unknown'

export interface HealthIndicatorItem {
  id: string
  name: string
  state: HealthState
  detail?: string
  facts?: Array<{
    value: string
    help: string
  }>
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
  settingsTab: 'Media' | 'qBittorrent' | 'Plex' | 'Indexers'
  items: HealthIndicatorItem[]
}

export type PlexHealthState = 'healthy' | 'unavailable' | 'unknown'
export type IdentityState = 'matched' | 'manual' | 'uncertain' | 'conflict' | 'unmatched'
export type ReleaseState = 'current' | 'missing' | 'replaced' | 'removed' | 'conflict' | 'duplicate'
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
export type ProblemStatus = 'open' | 'resolved' | 'dismissed'
export type ProblemAction = 'restore' | 'dismiss' | 'recheck' | 'confirm_movie_match' | 'confirm_show_match'
export type SearchTargetEntityType = 'tmdb_movie' | 'tmdb_show_season' | 'movie' | 'episode' | 'season'
export type SearchIndexerState = 'queued' | 'completed' | 'failed'
export type TorrentArchiveState = 'not_archived' | 'archived' | 'failed'
export type TorrentMediaState = 'untracked' | 'tracked' | 'backed_up' | 'archived' | 'partial' | 'attention'
export type TorrentAssociationType = 'incoming' | 'attached' | 'historical'
export type MediaRole = 'movie_video' | 'episode_video' | 'multi_episode_video' | 'dvd_structure' | 'bluray_structure' | 'other_media'

export interface PlexConfiguration {
  configured: boolean
  url?: string
  token_configured: boolean
  enabled: boolean
  health: PlexHealthState
  machine_identifier?: string
  last_checked_at?: string
  last_success_at?: string
  latency_ms?: number
  last_error?: string
  revision?: number
}

export interface PlexTestResult {
  status: 'healthy' | 'unavailable'
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
  identityState?: IdentityState
  monitored?: boolean
  tags?: Tag[]
  problemCount?: number
  incomingReplacement?: boolean
  incomingKind?: 'replacement' | 'release'
  overview?: string
  posterRef?: string
  storageRoot?: string
  rootHealth?: 'available' | 'unavailable' | 'unknown'
  rootAffectedCount?: number
  lastObservedAt?: string
  releasesDetail?: MovieRelease[]
  incoming?: IncomingDownload
  problems?: ReconciliationEvidence[]
  recentEvents?: MovieEvent[]
  reconciliation?: ReconciliationAggregate
  torrentHistory?: TorrentArchiveItem[]
  torrentStatus: MediaTorrentStatus
}

export interface MediaTorrentStatus {
  state: TorrentMediaState
  releaseCount: number
  trackedReleaseCount: number
  backedUpReleaseCount: number
  torrentCount: number
  presentTorrentCount: number
  archivedTorrentCount: number
  failedArchiveCount: number
  trackerState: 'healthy' | 'degraded' | 'unavailable' | 'unregistered' | 'unknown'
}

export interface MovieDirectory {
  id?: string
  path: string
  exists: boolean
  missingSince?: string
  files?: string[]
  /** Media folder this copy sits under; can differ between releases of one movie. */
  storageRoot?: string
  /** Filename of the video file itself, without the directory path. */
  mediaFilename?: string
}

export interface MovieRelease {
  id: string
  name: string
  edition?: string
  parsedEdition?: string
  editionOverrideActive: boolean
  manualEditionOverride?: string | null
  quality?: string
  releaseGroup?: string
  state: ReleaseState
  confidence?: number
  firstSeenAt?: string
  directories: MovieDirectory[]
  originalCustomFormatScore?: number
  currentCustomFormatScore?: number
  selectionSnapshot?: Record<string, unknown>
  /** What the parser read out of the release name: HDR, audio, codec, flags. */
  parser?: Record<string, unknown>
  torrentStatus: MediaTorrentStatus
}

export type MovieEditionMode = 'automatic' | 'standard' | 'named'

export interface MovieEditionUpdate {
  releaseId: string
  mode: MovieEditionMode
  edition?: string
}

export interface MovieEditionUpdateResult {
  movieId: string
  duplicateResolved: boolean
  releases: Array<{
    releaseId: string
    parsedEdition?: string
    editionOverrideActive: boolean
    manualEditionOverride?: string | null
    effectiveEdition?: string
    state: ReleaseState
  }>
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
  plexBlocked?: boolean
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

export interface FilesystemListing {
  current_path: string
  parent?: string | null
  directories: { name: string; path: string }[]
  problem?: string | null
}

export interface LibrarySummary {
  movies: number
  shows: number
  unlinked_directories: number
}

export interface StorageRootScan {
  job_id: string
  status: 'queued' | 'running'
  stage?: string
  current: number
  total: number
  percent: number
}

export interface StorageRoot {
  id: string
  name: string
  resolved_root_path: string
  media_type: 'movies' | 'shows'
  access_mode: 'read_only' | 'read_write'
  discovery_mode: 'automatic' | 'review'
  enabled: boolean
  missing_grace_checks: number
  last_health?: 'available' | 'unavailable' | 'unknown'
  last_scan_at?: string
  last_health_checked_at?: string
  affected_media_count?: number
  missing_media_count?: number
  scanning: boolean

  scan?: StorageRootScan

  media_count: number
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
  torrentStatus: MediaTorrentStatus
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
  identityState?: IdentityState
  problemCount?: number
  nextMissingEpisode?: string
  overview?: string
  revision?: number
  seasonDetail?: Season[]
  recentEvents?: MovieEvent[]
  problems?: ReconciliationEvidence[]
  storageRoots?: Array<{ id: string; name: string; path: string; health?: 'available' | 'unavailable' | 'unknown' }>
  lastObservedAt?: string
  torrentStatus: MediaTorrentStatus
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
  imdbId?: string
  imdbRating?: number
  imdbVotes?: number
  popularity?: number
  voteCount?: number
  voteAverage?: number
}

export interface Download {
  id: string
  payloadGroupId?: string
  infoHash?: string
  name: string
  client: string
  clientId: string
  kind: 'Movie' | 'Show'
  state: 'Downloading' | 'Checking' | 'Seeding' | 'Completed' | 'Paused' | 'Error'
  activity: 'downloading' | 'checking' | 'metadata' | 'queued' | 'stalled' | 'seeding' | 'paused' | 'error'
  progress: number
  size: string
  eta: string
  speed: string
  path: string
  /** Untrimmed resolved qBittorrent content path used to identify shared payloads. */
  payloadPath?: string
  quality?: string
  edition?: string
  movieId?: string
  showId?: string
  releaseId?: string
  mediaTitle?: string
  mediaPosterRef?: string
  mediaRootName?: string
  mediaYear?: number
  mediaState?: string
  reconciliationState?: string
  reconciliationDetail?: string
  incoming?: boolean
  incomingKind?: 'replacement' | 'release'
  uploadSpeed: string
  /** Raw bytes/s behind the formatted speeds, for filtering on real activity. */
  downloadSpeedBytes: number
  uploadSpeedBytes: number
  /** Raw values behind the formatted size and ETA, so those columns sort correctly. */
  sizeBytes: number
  etaSeconds: number
  amountLeft?: string
  ratio?: number
  seeds?: number
  /** Whole swarm as the tracker reports it; qBittorrent shows it in parentheses. */
  seedsTotal?: number
  leeches?: number
  leechesTotal?: number
  availability?: number
  archiveState: TorrentArchiveState
  trackerState: 'healthy' | 'degraded' | 'unavailable' | 'unregistered' | 'unknown'
  trackerHost?: string
  category?: string
}

export type UpgradeType = 'quality' | 'custom_format' | 'season_pack' | 'repack' | 'proper' | 'revision' | 'replacement'
export type UpgradeExecutionState = 'submitted' | 'downloading' | 'validating' | 'handoff_attention' | 'cleanup_required' | 'completed' | 'failed'

export interface UpgradeCurrentRelease {
  id: string
  name: string
  quality?: string
  edition?: string
  customFormatScore: number
}

export interface UpgradeMatchedFormat {
  id: string
  name: string
  contribution: number
}

export interface UpgradeCustomFormatImprovement {
  id: string
  name: string
  contribution: number
  previousContribution?: number
  delta: number
  isNew: boolean
}

export interface UpgradeCandidate {
  id: string
  mediaType: 'movies' | 'shows'
  movieId?: string
  seasonId?: string
  mediaTitle: string
  mediaYear?: number
  tmdbId?: number
  seasonNumber?: number
  currentPaths: string[]
  /** Media folder the current release lives in. */
  rootName?: string
  /** Artwork for the title. */
  posterRef?: string
  /** Folder holding the current release; undefined when a season is split. */
  releaseFolder?: string
  currentReleaseIds: string[]
  currentReleaseName: string
  currentQuality?: string
  currentEdition?: string
  movieHasMultipleEditions: boolean
  currentCustomFormatScore: number
  /** Undefined means this title inherits the default Quality Profile. */
  qualityProfileId?: string
  qualityProfileName?: string
  qualityProfileInherited: boolean
  indexerId?: string
  indexerName: string
  indexerColor?: string
  title: string
  quality?: string
  candidateEdition?: string
  resultCount: number
  customFormatScore: number
  scoreDelta: number
  size?: number
  seeders?: number
  leechers?: number
  publishedAt?: string
  upgradeTypes: UpgradeType[]
  addedCustomFormats: UpgradeCustomFormatImprovement[]
  currentReleases: UpgradeCurrentRelease[]
  currentEpisodeCount?: number
  seasonEpisodeCount?: number
  currentMatchedFormats: UpgradeMatchedFormat[]
  candidateMatchedFormats: UpgradeMatchedFormat[]
  executionEligible: boolean
  executionBlockReason?: string
  comparisonSnapshot: Record<string, unknown>
  executionState?: UpgradeExecutionState
  selectedDownloadClientId?: string
  selectedDownloadClientName?: string
  selectedCategory?: string
  torrentId?: string
  downloadProgress?: number
  downloadState?: string
  executionError?: string
  submittedAt?: string
  validatedAt?: string
  oldTorrentsRemovedAt?: string
  cleanupCompletedAt?: string
  lastSearchedAt: string
  createdAt: string
  updatedAt: string
}

export interface UpgradeSearchResult {
  id: string
  indexerId?: string
  indexerName: string
  indexerColor?: string
  title: string
  quality?: string
  edition?: string
  releaseGroup?: string
  customFormatScore?: number
  size?: number
  seeders?: number
  leechers?: number
  publishedAt?: string
  eligible: boolean
  rejectionReasons: string[]
  upgradeTypes: string[]
  matchedFormatNames: string[]
  parser: Record<string, unknown>
  recommended: boolean
  selected: boolean
}

export interface UpgradeSearchResults {
  candidateId: string
  searchedAt: string
  results: UpgradeSearchResult[]
}

export interface UpgradeCleanupTarget {
  path: string
  kind: 'directory' | 'file'
  inventory: Record<string, unknown>[]
}

export interface UpgradeCleanupPreview {
  candidateId: string
  mediaType: 'movies' | 'shows'
  eligible: boolean
  blocked: string[]
  targets: UpgradeCleanupTarget[]
  totalBytes: number
  confirmationToken: string
  expiresAt: string
  warnings: string[]
}

export interface UpgradeCleanupResult {
  candidateId: string
  state: 'completed'
  deletedPaths: string[]
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
  health: 'unknown' | 'healthy' | 'unavailable'
  last_checked_at?: string
  last_success_at?: string
  latency_ms?: number
  last_error?: string
  revision: number
  pollIntervalSeconds: number
  recentPriority: 'first' | 'last'
  olderPriority: 'first' | 'last'
  sequentialOrder: boolean
  firstLastFirst: boolean
  contentLayout: 'default' | 'original' | 'subfolder'
  completedDownloadHandling: boolean
}

export interface DownloadClientCategory {
  name: string
  savePath: string
  resolvedSavePath: string
  isDefault: boolean
}

export interface ShowDownloadSeasonPreview {
  seasonNumber: number
  title?: string
  episodeCount: number
  airDate?: string
  posterRef?: string
}

export interface ShowDownloadPreview {
  tmdbId: number
  title: string
  year?: number
  overview?: string
  posterRef?: string
  seasons: ShowDownloadSeasonPreview[]
}

export interface InteractiveSearchResult {
  id: string
  jobId: string
  indexerId?: string
  indexerName: string
  indexerColor?: string
  mediaType: 'movies' | 'shows'
  targetEntityType: SearchTargetEntityType
  title: string
  size?: number
  seeders?: number
  leechers?: number
  publishedAt?: string
  quality?: string
  qualityAllowed: boolean
  qualityPreference: number
  edition?: string
  releaseGroup?: string
  customFormatScore?: number
  qualityProfileId?: string
  qualityProfileName?: string
  minimumQuality?: string
  minimumQualityMet?: boolean
  minimumCustomFormatScore?: number
  minimumCustomFormatScoreMet?: boolean
  customFormatSnapshot: Record<string, unknown>
  parser: Record<string, unknown>
  warnings: string[]
  blocklisted: boolean
  blocklistId?: string
  blocklistReason?: string
  selectedAt?: string
  selectedDownloadClientId?: string
  createdAt: string
  expiresAt: string
}

export interface BlocklistEntry {
  id: string
  mediaType: 'movies' | 'shows'
  targetEntityType: string
  targetEntityId?: string
  mediaResourceId?: string
  mediaTitle?: string
  sourceTitle: string
  infoHash?: string
  indexerName?: string
  quality?: string
  reason: string
  createdAt: string
}

export interface InteractiveSearchIndexerStatus {
  id: string
  name: string
  status: SearchIndexerState
  results: number
  elapsedMs?: number
  error?: string
}

export interface InteractiveSearchTarget {
  mediaType: 'movies' | 'shows'
  entityType: SearchTargetEntityType
  entityId?: string
  profileEntityId?: string
  tmdbId?: number
  season?: number
  episode?: number
  title: string
  year?: number
  overview?: string
  posterRef?: string
  qualityProfileId?: string
}

export interface InteractiveSearchProgress {
  totalIndexers: number
  completedIndexers: number
  percent: number
}

export interface InteractiveSearchJob {
  id: string
  status: JobStatus
  target: InteractiveSearchTarget
  progress: InteractiveSearchProgress
  indexers: InteractiveSearchIndexerStatus[]
  results: InteractiveSearchResult[]
  resultTotal: number
  error?: Record<string, unknown>
  createdAt: string
  startedAt?: string
  finishedAt?: string
}

export interface DownloadClientTestResult {
  status: 'unknown' | 'healthy' | 'unavailable'
  version?: string
  latency_ms?: number
  message?: string
}

export interface Problem {
  id: string
  code: string
  title: string
  subject: string
  /** Artwork for the title this Problem is about. */
  posterRef?: string
  /** TMDB id of that title, which is what settles an identity dispute. */
  tmdbId?: number
  detail: string
  severity: 'high' | 'medium' | 'low'
  created: string
  reason?: string
  status?: ProblemStatus
  workflow: 'manual' | 'choice' | 'config' | 'waiting'

  label?: string
  entityType?: string
  entityId?: string
  details?: Record<string, unknown>
  resolution?: Record<string, unknown>
  resolvedAt?: string
  availableActions?: ProblemAction[]
}

export interface Job {
  id: string
  jobType: string
  title: string
  detail: string
  progress?: number
  state: JobStatus
  updated: string
  startedAt?: string
  finishedAt?: string
  cancellable: boolean
  error?: string
  summary?: Record<string, unknown>
  stage?: string
}

export interface ScheduledTask {
  id: string
  name: string
  jobType: string
  description: string
  intervalSeconds: number
  intervalOptions: number[]
  configurable: boolean
  enabled: boolean
  lastStartedAt?: string
  lastFinishedAt?: string
  nextRunAt?: string
  lastStatus?: string
  lastDurationMs?: number
  activeJobId?: string
  activeJobStatus?: JobStatus
}

export interface RecoveryCapabilities {
  supported: boolean
  databaseBackend: string
  sqliteVersion?: string
  databasePath?: string
  databaseReadable: boolean
  torrentArchiveReadable: boolean
  exportDirectoryWritable: boolean
  exportDirectory: string
  retentionHours: number
  reasons: string[]
}

export type RecoveryReimportStatus = 'ready' | 'already_present' | 'blocked'

export interface RecoveryReimportItem {
  torrentId: string
  infoHash: string
  torrentName: string
  mediaType?: 'movies' | 'shows'
  mediaTitle?: string
  releaseName?: string
  status: RecoveryReimportStatus
  reasons: string[]
  warnings: string[]
  downloadClientId?: string
  downloadClientName?: string
  savePath?: string
  resolvedSavePath?: string
  category?: string
  tags: string[]
  totalFileCount: number
  selectedFileCount: number
  skippedFileCount: number
}

export interface RecoveryReimportPlan {
  generatedAt: string
  expiresAt: string
  confirmationToken: string
  total: number
  ready: number
  alreadyPresent: number
  blocked: number
  items: RecoveryReimportItem[]
}

export interface EventHistoryItem {
  id: string
  eventType: string
  severity: 'info' | 'warning' | 'error'
  entityType: string
  entityId?: string
  message: string
  details: Record<string, unknown>
  createdAt: string
}

export interface TorrentArchiveItem {
  includedInRemoteBackup?: boolean
  remoteBackupAt?: string
  archiveVerified?: boolean
  archiveError?: string
  capturedAt?: string
  sourceState?: string
  sourceCheckedAt?: string
  recoveryClients?: string[]
  id: string
  releaseId?: string
  infoHash: string
  torrentName: string
  releaseName?: string
  mediaTitle?: string
  mediaType?: 'movies' | 'shows'
  tmdbId?: number
  tvdbId?: number
  quality?: string
  edition?: string
  releaseGroup?: string
  tracker?: string
  totalSize?: number
  archiveState: TorrentArchiveState
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
  associationType?: TorrentAssociationType
}

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
  /** Which parsed field the pattern runs against. Built-ins match typed fields
      such as the audio codec, so this has to survive an edit round trip. */
  type: CustomFormatConditionType
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
  enabled: boolean
  schemaVersion: number
  conditions: CustomFormatCondition[]
  supersedes: string[]
  conditionCount: number
  usedByProfiles: number
  revision: number
  createdAt?: string
  updatedAt?: string

  builtin?: boolean
}

export interface CustomFormatSection {
  id: string
  name: string
  formatIds: string[]
}

export interface CustomFormatConditionEvaluation {
  conditionId: string
  conditionType: CustomFormatConditionType
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
  rawMatched: boolean
  suppressedBy: string[]
  suppressedByNames: string[]
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
  minimumCustomFormatScore?: number
  seasonPackUpgrades: boolean
  qualities: QualityDefinition[]
  customFormatScores: QualityProfileScore[]
  assignedTitles: number
  assignedMovies: number
  assignedShows: number
  /** The seeded profile titles fall back to. Renameable, so never match by name. */
  isDefault: boolean
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
  /** True when the title has no profile of its own and is using the default. */
  qualityProfileInherited: boolean
  minimumQuality?: QualityDefinition
  profileMinimumQuality?: QualityDefinition
  minimumQualityOverridden: boolean
  minimumCustomFormatScore?: number
  profileMinimumCustomFormatScore?: number
  minimumCustomFormatScoreOverridden: boolean
  customFormatScores: MediaProfileScore[]
  revision: number
}

export interface ApiErrorShape {
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
  enableRss: boolean
  enableInteractiveSearch: boolean
  categories: number[]
  minimumSeeders: number
  color?: string
  multiLanguages: string[]

  downloadClientIds: string[]
  priority: number
  health: 'unknown' | 'healthy' | 'unavailable'
  lastCheckedAt?: string
  lastSuccessAt?: string
  latencyMs?: number
  lastError?: string
  revision: number
}

export interface IndexerTestResult {
  status: 'healthy' | 'unavailable'
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
  imdbId?: string
  imdbRating?: number
  imdbVotes?: number
  popularity?: number
  voteCount?: number
  voteAverage?: number
}

export interface MediaDiscovery {
  id: string
  directoryId: string
  path: string
  rootName: string
  exists: boolean
  mediaType: 'movies' | 'shows'
  newMediaState: 'pending' | 'approved' | 'ignored'
  parsedTitle?: string
  parsedYear?: number
  confidence?: number
  quality?: string
  edition?: string
  parsedEdition?: string
  editionOverrideActive: boolean
  manualEditionOverride?: string | null
  libraryEditionConflict: boolean
  /** Quality Profile the imported title will be created with. */
  qualityProfileId?: string
  qualityProfileName?: string
  hdr: string[]
  suggested?: TMDBMovieLookup | TMDBShowLookup
  selected?: TMDBMovieLookup | TMDBShowLookup
  candidates: Array<TMDBMovieLookup | TMDBShowLookup>

  files: Array<{ relativePath: string; role: MediaRole }>
  fileCount: number
  videoCount: number
  createdAt?: string
  updatedAt?: string
}
