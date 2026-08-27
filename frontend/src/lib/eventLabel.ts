

const LABELS: Record<string, string> = {
  'download.completed': 'Download finished',
  'search.download_submitted': 'Download sent',
  'scan.completed': 'Scan finished',
  'scan.failed': 'Scan failed',
  'plex.health': 'Plex health',
  'plex.verified': 'Plex verified',
  'plex.conflict': 'Plex conflict',
  'plex.sync_completed': 'Plex synced',
  'qbittorrent.health': 'qBittorrent health',
  'problem.created': 'Problem opened',
  'problem.resolved': 'Problem resolved',
  'storage_root.unavailable': 'Folder unavailable',
  'storage_root.restored': 'Folder restored',
  'release.replaced': 'Release replaced',
  'media.missing': 'Media missing',
  'media.present': 'Media present',
  'media.reappeared': 'Media reappeared',
  'torrent.detected': 'Torrent detected',
  'torrent.removed': 'Torrent removed',
  'torrent.reappeared': 'Torrent reappeared',
  'show.metadata_refreshed': 'Metadata refreshed',
  'tag.created': 'Tag created',
  'tag.updated': 'Tag updated',
  'tag.deleted': 'Tag deleted',
  'movie.tags_updated': 'Tags updated',
  'movie.monitoring_updated': 'Monitoring changed',
  'quality_profile.assignment_updated': 'Profile assigned',
  'parser.reevaluated': 'Parser re-run',
  'custom_formats.reevaluated': 'Formats re-run',
  'bulk.operation_completed': 'Bulk operation',
}

export function eventLabel(eventType: string): string {
  const known = LABELS[eventType]
  if (known) return known
  const words = eventType.replace(/[._]+/g, ' ').trim()
  if (!words) return 'Event'
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function eventStamp(createdAt: string): string {
  const at = new Date(createdAt)
  if (Number.isNaN(at.getTime())) return '—'
  const date = at.toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
  const time = at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const year = at.getFullYear() === new Date().getFullYear() ? '' : ` ${at.getFullYear()}`
  return `${date}${year} ${time}`
}
