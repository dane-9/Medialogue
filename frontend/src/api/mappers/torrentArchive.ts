import type { TorrentArchiveItem } from '../../types'
import type { components } from '../generated/schema'

type TorrentArchiveDto = components['schemas']['TorrentArchiveSummary']
type MovieTorrentHistoryDto = components['schemas']['MovieTorrentHistoryResponse']

export function mapTorrentArchiveItem(value: TorrentArchiveDto | MovieTorrentHistoryDto): TorrentArchiveItem {
  if ('association_type' in value) {
    return {
      id: value.id,
      releaseId: value.release_id,
      infoHash: value.info_hash,
      torrentName: value.torrent_name,
      releaseName: value.release_name,
      tracker: value.tracker ?? undefined,
      archiveState: value.archive_state,
      archivePath: value.archive_path ?? undefined,
      manifestPath: value.manifest_path ?? undefined,
      manifestSchemaVersion: value.manifest_schema_version ?? undefined,
      originalDownloadClient: value.download_client_name ?? undefined,
      qbitPresent: value.qbit_present,
      firstSeenAt: value.first_seen_at,
      completedAt: value.completed_at ?? undefined,
      associationType: value.association_type,
    }
  }
  return {
    id: value.id,
    infoHash: value.info_hash,
    torrentName: value.torrent_name,
    releaseName: value.release_name ?? undefined,
    includedInRemoteBackup: value.included_in_remote_backup,
    remoteBackupAt: value.remote_backup_at ?? undefined,
    archiveVerified: value.archive_verified,
    archiveError: value.archive_error ?? undefined,
    capturedAt: value.captured_at ?? undefined,
    sourceState: value.source_state,
    sourceCheckedAt: value.source_checked_at ?? undefined,
    recoveryClients: value.recovery_clients,
    mediaTitle: value.media_title ?? undefined,
    mediaType: value.media_type ?? undefined,
    tmdbId: value.tmdb_id ?? undefined,
    tvdbId: value.tvdb_id ?? undefined,
    quality: value.quality ?? undefined,
    edition: value.edition ?? undefined,
    releaseGroup: value.release_group ?? undefined,
    totalSize: value.total_size ?? undefined,
    archiveState: value.archive_state,
    archivePath: value.archive_path ?? undefined,
    manifestPath: value.manifest_path ?? undefined,
    manifestSchemaVersion: value.manifest_schema_version ?? undefined,
    originalDownloadClient: value.original_download_client ?? undefined,
    previousReportedPath: value.previous_reported_path ?? undefined,
    previousResolvedPath: value.previous_resolved_path ?? undefined,
    qbitPresent: value.qbit_present ?? false,
    firstSeenAt: value.first_seen_at,
    lastSeenAt: value.last_seen_at,
    completedAt: value.completed_at ?? undefined,
  }
}
