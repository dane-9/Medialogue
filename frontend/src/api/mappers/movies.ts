import type { IncomingDownload, Movie, MovieDirectory, MovieRelease, ReconciliationAggregate, Tag } from '../../types'
import type { components } from '../generated/schema'
import { percentValue } from './shared'
import { mapEvidence, mapEvent, mapPlexState } from './mediaCommon'
import { mapTorrentArchiveItem } from './torrentArchive'

type MediaDirectoryDto = components['schemas']['MediaDirectoryResponse']
type MovieReleaseDto = components['schemas']['MovieReleaseResponse']
type MovieIncomingDto = components['schemas']['MovieIncomingDownloadResponse']
type MovieReconciliationDto = components['schemas']['MovieReconciliationResponse']
type MovieSummaryDto = components['schemas']['MovieSummaryResponse']
type MovieDetailDto = components['schemas']['MovieDetailResponse']
type TagDto = components['schemas']['TagResponse']

export function mapDirectory(value: MediaDirectoryDto): MovieDirectory {
  return {
    id: value.id,
    path: value.resolved_path,
    exists: value.exists,
    missingSince: value.missing_since ?? undefined,
    files: value.files,
  }
}

export function mapRelease(value: MovieReleaseDto): MovieRelease {
  return {
    id: value.id,
    name: value.raw_release_name,
    edition: value.edition ?? undefined,
    parsedEdition: value.parsed_edition ?? undefined,
    editionOverrideActive: value.edition_override_active ?? false,
    manualEditionOverride: value.manual_edition_override,
    quality: value.quality ?? undefined,
    releaseGroup: value.release_group ?? undefined,
    state: value.state,
    confidence: percentValue(value.confidence),
    firstSeenAt: value.first_seen_at,
    directories: value.directories.map(mapDirectory),
    originalCustomFormatScore: value.original_custom_format_score ?? undefined,
    currentCustomFormatScore: value.current_custom_format_score ?? undefined,
    selectionSnapshot: value.selection_snapshot ?? undefined,
  }
}

export function mapIncoming(value: MovieIncomingDto): IncomingDownload {
  return {
    id: value.id,
    name: value.name,
    client: value.client_name,
    progress: percentValue(value.progress) ?? 0,
    quality: value.quality ?? undefined,
    edition: value.edition ?? undefined,
    state: value.state,
    path: value.resolved_save_path ?? undefined,
    kind: value.incoming_kind,
  }
}

export function mapReconciliation(value: MovieReconciliationDto): ReconciliationAggregate {
  return {
    state: value.state,
    incomingCount: value.incoming_count,
    missingCount: value.missing_count,
    degradedCount: value.degraded_count,
    replacedCount: value.replaced_count,
    duplicateCount: value.duplicate_count,
    qbitMediaDisagreement: value.qbit_media_disagreement,
    plexBlocked: value.plex_blocked,
    rootOffline: value.root_offline,
    rootAffectedCount: value.root_affected_count,
  }
}

export function mapTag(value: TagDto): Tag {
  return { id: value.id, name: value.name, createdAt: value.created_at }
}

export function mapMovie(value: MovieSummaryDto | MovieDetailDto): Movie {
  const detail = 'reconciliation' in value ? value as MovieDetailDto : undefined
  const edition = value.edition?.toLowerCase() === 'theatrical cut' ? undefined : value.edition ?? undefined
  const incoming = detail?.incoming_downloads?.[0]
  return {
    id: value.resource_id,
    title: value.title,
    year: value.year ?? 0,
    poster: value.poster_ref ?? undefined,
    releases: value.release_count,
    quality: value.current_quality ?? 'Unknown quality',
    edition,
    status: value.state,
    plex: mapPlexState(value.plex_state),
    confidence: percentValue(value.confidence) ?? 0,
    location: value.location ?? 'No active path',
    tmdbId: value.tmdb_id ?? undefined,
    identityState: value.identity_state,
    monitored: value.monitored,
    tags: (value.tags ?? []).map(mapTag),
    problemCount: value.problem_count,
    incomingReplacement: value.incoming_kind === 'replacement',
    incomingKind: value.incoming_kind ?? undefined,
    overview: detail?.overview ?? undefined,
    posterRef: detail?.poster_ref ?? undefined,
    storageRoot: detail?.storage_root ?? undefined,
    rootHealth: detail?.root_health ?? undefined,
    rootAffectedCount: detail?.root_affected_count,
    lastObservedAt: value.last_observed_at ?? undefined,
    releasesDetail: detail?.releases.map(mapRelease) ?? [],
    incoming: incoming ? mapIncoming(incoming) : undefined,
    problems: (detail?.problems ?? []).map(mapEvidence),
    recentEvents: (detail?.recent_events ?? []).map(mapEvent),
    reconciliation: detail ? mapReconciliation(detail.reconciliation) : undefined,
    torrentHistory: (detail?.torrent_history ?? []).map(mapTorrentArchiveItem),
  }
}
