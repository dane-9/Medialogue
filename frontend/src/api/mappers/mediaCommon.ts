import type { MediaTorrentStatus, Movie, MovieEvent, ReconciliationEvidence } from '../../types'
import type { components } from '../generated/schema'

type MovieSummaryDto = components['schemas']['MovieSummaryResponse']
type MovieProblemDto = components['schemas']['MovieProblemResponse']
type ShowProblemDto = components['schemas']['ShowProblemResponse']
type MovieEventDto = components['schemas']['MovieEventResponse']
type ShowEventDto = components['schemas']['ShowEventResponse']
type MediaTorrentStatusDto = components['schemas']['MediaTorrentStatusResponse']

export function mapTorrentStatus(value?: MediaTorrentStatusDto): MediaTorrentStatus {
  if (!value) return {
    state: 'untracked',
    releaseCount: 0,
    trackedReleaseCount: 0,
    backedUpReleaseCount: 0,
    torrentCount: 0,
    presentTorrentCount: 0,
    archivedTorrentCount: 0,
    failedArchiveCount: 0,
    trackerState: 'unknown',
  }
  return {
    state: value.state,
    releaseCount: value.release_count,
    trackedReleaseCount: value.tracked_release_count,
    backedUpReleaseCount: value.backed_up_release_count,
    torrentCount: value.torrent_count,
    presentTorrentCount: value.present_torrent_count,
    archivedTorrentCount: value.archived_torrent_count,
    failedArchiveCount: value.failed_archive_count,
    trackerState: value.tracker_state,
  }
}

export function mapPlexState(value: MovieSummaryDto['plex_state']): Movie['plex'] {
  switch (value) {
    case 'matched': return 'Verified'
    case 'multiple_versions': return 'Multiple versions'
    case 'conflict': return 'Conflict'
    case 'unavailable': return 'Unavailable'
    case 'not_found': return 'Not in Plex'
    case 'pending':
    case 'unknown':
      return 'Pending'
  }
}

export function mapEvidence(value: MovieProblemDto | ShowProblemDto): ReconciliationEvidence {
  return {
    id: value.id,
    code: value.reason,
    title: value.title || value.reason.replaceAll('_', ' '),
    detail: value.description || value.message,
    severity: value.severity === 'error' ? 'high' : value.severity === 'info' ? 'low' : 'medium',
  }
}

export function mapEvent(value: MovieEventDto | ShowEventDto): MovieEvent {
  return {
    id: value.id,
    type: value.type,
    message: value.message,
    details: value.details,
    createdAt: value.created_at,
  }
}
