export type StatusTone = 'green' | 'amber' | 'red' | 'neutral'

const SETTLED = ['Present', 'Verified', 'Completed', 'Archived']
const WORKING = ['Missing', 'Pending', 'Multiple versions', 'Downloading', 'Active']
const BROKEN = ['Conflict', 'Duplicate', 'Error']

export function statusTone(status: string): StatusTone {
  if (SETTLED.includes(status)) return 'green'
  if (WORKING.includes(status)) return 'amber'
  if (BROKEN.includes(status)) return 'red'
  return 'neutral'
}

export function needsAttention(status: string): boolean {
  return statusTone(status) !== 'green'
}
