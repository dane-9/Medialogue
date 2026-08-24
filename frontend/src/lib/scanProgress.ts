import type { StorageRootScan } from '../types'

/**
 * One line describing a running scan, for the tooltip on a scanning row.
 *
 * A scan reports no total while it is still enumerating, so a percentage is
 * meaningless until it has one. Saying "0%" for the whole enumerate stage reads
 * as a stalled scan, which is the opposite of what the tooltip is for.
 */
export function describeScan(scan: StorageRootScan | undefined): string {
  if (!scan) return 'Scanning'
  if (scan.status === 'queued') return 'Waiting for another scan to finish'
  if (!scan.total) return 'Nothing counted yet'

  const percent = Math.max(0, Math.min(100, Math.round(scan.percent)))
  return `${scan.current} of ${scan.total} folders (${percent}%)`
}
