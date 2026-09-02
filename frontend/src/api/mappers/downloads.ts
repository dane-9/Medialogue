import type { Download } from '../../types'
import type { components } from '../generated/schema'
import { numberValue, percentValue } from './shared'

type DownloadDto = components['schemas']['DownloadResponse']

export function formatBytes(value: unknown): string {
  const bytes = numberValue(value, 0)
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = bytes
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 100 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
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
    ? '—'
    : etaSeconds < 60
      ? `${etaSeconds}s`
      : etaSeconds < 3600
        ? `${Math.ceil(etaSeconds / 60)}m`
        : etaSeconds < 86400
          ? `${Math.floor(etaSeconds / 3600)}h ${Math.ceil((etaSeconds % 3600) / 60)}m`
          : `${Math.floor(etaSeconds / 86400)}d ${Math.ceil((etaSeconds % 86400) / 3600)}h`

  return {
    id: value.id,
    name: value.name,
    client: value.client_name,
    kind: value.scope === 'shows' ? 'Show' : 'Movie',
    state: states[value.activity],
    activity: value.activity,
    progress,
    size: formatBytes(value.total_size),
    eta,
    speed: value.download_speed ? `${formatBytes(value.download_speed)}/s` : '—',
    path: value.resolved_save_path ?? value.reported_save_path ?? 'Unknown path',
    quality: value.quality ?? undefined,
    edition: value.edition ?? undefined,
    movieId: value.movie_id ?? undefined,
    showId: value.show_id ?? undefined,
    releaseId: value.release_id ?? undefined,
    mediaTitle: value.media_title ?? undefined,
    mediaState: value.media_state ?? undefined,
    reconciliationState: value.reconciliation_state ?? undefined,
    reconciliationDetail: value.reconciliation_detail ?? undefined,
    incoming: value.incoming ?? false,
    incomingKind: value.incoming_kind ?? undefined,
    uploadSpeed: value.upload_speed ? `${formatBytes(value.upload_speed)}/s` : '—',
    downloadSpeedBytes: value.download_speed ?? 0,
    uploadSpeedBytes: value.upload_speed ?? 0,
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
  }
}
