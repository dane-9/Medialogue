import type { Job } from '../../types'
import type { components } from '../generated/schema'

type JobDto = components['schemas']['JobResponse']

const JOB_TITLES: Record<string, string> = {
  plex_movie_recheck: 'Plex movie recheck',
  new_media_add: 'Add New Media',
  plex_show_recheck: 'Plex show recheck',
  plex_library_sync: 'Plex library verification',
  tmdb_show_metadata_refresh: 'TMDB metadata refresh',
  bulk_movie_operation: 'Bulk movie operation',
  qbittorrent_poll: 'qBittorrent poll',
  qbittorrent_poll_all: 'qBittorrent poll (all clients)',
  duplicate_resolution: 'Duplicate resolution',
  torrent_archive_retry: 'Torrent archive retry',
  torrent_restore: 'Torrent restore',
  reconciliation: 'Library file check',
  confirmed_show_reconciliation: 'Show file matching',
  integration_health_sweep: 'Check Integration Health',
  scheduled_housekeeping: 'Housekeeping',
  verify_known_media_files: 'Verify Known Media Files',
  full_media_folder_scan: 'Full Media Folder Scan',
  storage_root_scan: 'Media folder scan',
}

export function jobTitle(jobType: string) {
  return JOB_TITLES[jobType] ?? jobType.replaceAll('_', ' ')
}

/** Job types whose display name contains the text; the server only knows the raw types. */
export function jobTypesTitled(text: string) {
  const term = text.trim().toLowerCase()
  return term ? Object.keys(JOB_TITLES).filter((type) => JOB_TITLES[type].toLowerCase().includes(term)) : []
}

export function mapJob(item: JobDto): Job {
  const progress = item.progress
  const summary = item.summary
  const error = item.error
  return {
    id: item.id,
    jobType: item.job_type,
    title: jobTitle(item.job_type),
    detail: typeof progress.detail === 'string' ? progress.detail : typeof summary.path === 'string' ? summary.path : typeof summary.message === 'string' ? summary.message : '',
    progress: typeof progress.percent === 'number' ? progress.percent : undefined,
    stage: typeof progress.stage === 'string' ? progress.stage : undefined,
    state: item.status,
    updated: new Date(item.updated_at).toLocaleString(),
    startedAt: item.started_at ?? undefined,
    finishedAt: item.finished_at ?? undefined,
    cancellable: item.cancellable,
    error: error && typeof error.message === 'string' ? error.message : undefined,
    summary: item.summary,
  }
}
