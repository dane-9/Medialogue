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
  const rawState = (value.state ?? 'unknown').toLowerCase()
  const progress = percentValue(value.progress) ?? 0
  let state: Download['state'] = 'Downloading'
  if (rawState.startsWith('checking')) state = 'Checking'
  else if (rawState.includes('error') || rawState.includes('missing')) state = 'Error'
  else if (rawState.includes('pause') || rawState.includes('stopped')) state = 'Paused'
  else if (rawState === 'completed' || rawState === 'complete') state = 'Completed'
  else if (rawState.includes('up') || rawState.includes('seed') || rawState.includes('upload')) state = 'Seeding'
  else if (progress >= 100 && !rawState.includes('down') && !rawState.includes('meta')) state = 'Completed'

  return {
    id: value.id,
    name: value.name,
    client: value.client_name,
    kind: value.scope === 'shows' ? 'Show' : 'Movie',
    state,
    progress,
    size: formatBytes(value.total_size),
    eta: '—',
    speed: '—',
    path: value.resolved_save_path ?? value.reported_save_path ?? 'Unknown path',
    quality: value.quality ?? undefined,
    edition: value.edition ?? undefined,
    movieId: value.movie_id ?? undefined,
    mediaState: value.media_state ?? undefined,
    reconciliationState: value.reconciliation_state ?? undefined,
    reconciliationDetail: value.reconciliation_detail ?? undefined,
    incoming: value.incoming ?? false,
    incomingKind: value.incoming_kind ?? undefined,
  }
}
