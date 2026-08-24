export type StatusTone = 'green' | 'amber' | 'red' | 'neutral'

const SETTLED = ['Present', 'Verified', 'Completed', 'Archived']
const WORKING = ['Missing', 'Pending', 'Multiple versions', 'Downloading', 'Active']
const BROKEN = ['Conflict', 'Duplicate', 'Error']

/** The badge colour for a library or integration state. */
export function statusTone(status: string): StatusTone {
  if (SETTLED.includes(status)) return 'green'
  if (WORKING.includes(status)) return 'amber'
  if (BROKEN.includes(status)) return 'red'
  return 'neutral'
}

/**
 * Whether a card should flag itself.
 *
 * Only states that are not settled qualify. A library is mostly items that are
 * fine, so marking those spends chrome on the common case and leaves the eye
 * nothing to land on; the exceptions are what a grid should surface.
 */
export function needsAttention(status: string): boolean {
  return statusTone(status) !== 'green'
}
