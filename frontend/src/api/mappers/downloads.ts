import type { Download } from '../../types'
import type { components } from '../generated/schema'
import { numberValue, percentValue } from './shared'

type DownloadDto = components['schemas']['DownloadResponse']

export function formatBytes(value: unknown): string {
  const bytes = numberValue(value, 0)
  if (!bytes) return '-'
  // IEC units, matching the 1024 divisor below and what qBittorrent displays.
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let amount = bytes
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 100 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

// qBittorrent reports a single-file torrent's content path as the file itself.
// The table and its tooltip are about where the media lives, so a trailing file
// name is trimmed back to its folder: the movie folder, or the season folder for
// an episode. Matched against known container extensions rather than "has a
// dot", since release folders are full of dots (…1080p.BluRay.x264).
const MEDIA_FILE_EXTENSIONS = new Set([
  'mkv', 'mp4', 'avi', 'm4v', 'mov', 'wmv', 'flv', 'webm', 'mpg', 'mpeg',
  'm2ts', 'ts', 'iso', 'img', 'vob', 'divx', 'ogm', 'rmvb', 'srt', 'sub', 'ass', 'nfo',
])

export function mediaFolderPath(raw?: string | null): string {
  const path = (raw ?? '').replace(/[\\\/]+$/, '')
  if (!path) return 'Unknown path'
  const separator = path.lastIndexOf('/') > path.lastIndexOf('\\') ? '/' : '\\'
  const last = path.slice(path.lastIndexOf(separator) + 1)
  const extension = last.includes('.') ? last.slice(last.lastIndexOf('.') + 1).toLowerCase() : ''
  if (!MEDIA_FILE_EXTENSIONS.has(extension)) return path
  const parent = path.slice(0, path.lastIndexOf(separator))
  return parent || path
}

export function mapDownload(value: DownloadDto): Download {
  const progress = percentValue(value.progress) ?? 0
  const states: Record<DownloadDto['activity'], Download['state']> = {
    downloading: 'Downloading',
    checking: 'Checking',
    metadata: 'Downloading',
    queued: 'Paused',
    stalled: 'Downloading',
    seeding: 'Seeding',
    paused: 'Paused',
    error: 'Error',
  }
  const etaSeconds = value.eta_seconds
  const eta = etaSeconds === null || etaSeconds === undefined || etaSeconds < 0 || etaSeconds >= 8640000
    ? '-'
    : etaSeconds < 60
      ? `${etaSeconds}s`
      : etaSeconds < 3600
        ? `${Math.ceil(etaSeconds / 60)}m`
        : etaSeconds < 86400
          ? `${Math.floor(etaSeconds / 3600)}h ${Math.ceil((etaSeconds % 3600) / 60)}m`
          : `${Math.floor(etaSeconds / 86400)}d ${Math.ceil((etaSeconds % 86400) / 3600)}h`

  return {
    id: value.id,
    payloadGroupId: value.payload_group_id,
    infoHash: value.info_hash,
    name: value.name,
    client: value.client_name,
    clientId: value.client_id,
    kind: value.scope === 'shows' ? 'Show' : 'Movie',
    state: states[value.activity],
    activity: value.activity,
    progress,
    size: formatBytes(value.total_size),
    eta,
    speed: value.download_speed ? `${formatBytes(value.download_speed)}/s` : '-',
    path: mediaFolderPath(value.resolved_save_path ?? value.reported_save_path),
    payloadPath: value.resolved_save_path ?? value.reported_save_path ?? undefined,
    quality: value.quality ?? undefined,
    edition: value.edition ?? undefined,
    movieId: value.movie_id ?? undefined,
    showId: value.show_id ?? undefined,
    releaseId: value.release_id ?? undefined,
    mediaTitle: value.media_title ?? undefined,
    mediaPosterRef: value.media_poster_ref ?? undefined,
    mediaRootName: value.media_root_name ?? undefined,
    mediaYear: value.media_year ?? undefined,
    mediaState: value.media_state ?? undefined,
    reconciliationState: value.reconciliation_state ?? undefined,
    reconciliationDetail: value.reconciliation_detail ?? undefined,
    incoming: value.incoming ?? false,
    incomingKind: value.incoming_kind ?? undefined,
    uploadSpeed: value.upload_speed ? `${formatBytes(value.upload_speed)}/s` : '-',
    downloadSpeedBytes: value.download_speed ?? 0,
    uploadSpeedBytes: value.upload_speed ?? 0,
    sizeBytes: value.total_size ?? 0,
    // Unknown/never sorts last rather than ahead of an imminent completion.
    etaSeconds: etaSeconds === null || etaSeconds === undefined || etaSeconds < 0 ? Number.MAX_SAFE_INTEGER : etaSeconds,
    amountLeft: value.amount_left === null || value.amount_left === undefined || value.amount_left <= 0
      ? undefined
      : formatBytes(value.amount_left),
    ratio: value.ratio ?? undefined,
    seeds: value.seeds ?? undefined,
    seedsTotal: value.seeds_total ?? undefined,
    leeches: value.leeches ?? undefined,
    leechesTotal: value.leeches_total ?? undefined,
    availability: value.availability ?? undefined,
    archiveState: value.archive_state,
    trackerState: value.tracker_state,
    trackerHost: value.tracker_host ?? undefined,
    category: value.category ?? undefined,
  }
}
