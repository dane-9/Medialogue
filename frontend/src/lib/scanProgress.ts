import type { StorageRootScan } from '../types'

export function describeScan(scan: StorageRootScan | undefined): string {
  if (!scan) return 'Scanning'
  if (scan.status === 'queued') return 'Waiting for another scan to finish'
  if (!scan.total) return 'Nothing counted yet'

  const percent = Math.max(0, Math.min(100, Math.round(scan.percent)))
  return `${scan.current} of ${scan.total} folders (${percent}%)`
}
