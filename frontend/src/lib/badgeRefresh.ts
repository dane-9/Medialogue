import { parseJobStatusLiveEvent } from '../validation/liveEvents'

// Jobs that never change what the sidebar badges count. The health sweep runs
// every few minutes and the backup jobs only copy files; refreshing the counts
// for them would rerun the Upgrade and Replace Media queries for nothing.
const BADGE_NEUTRAL_JOBS = new Set(['integration_health_sweep', 'remote_backup'])

const FINISHED = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

/**
 * Whether a `job.status` event should refresh the sidebar counts.
 *
 * A job changes what the badges count when it ends, not while it queues or
 * reports progress, so only a finished job of a relevant type counts. An event
 * that cannot be read refreshes, so a malformed payload never leaves a stale
 * badge.
 */
export function jobEventChangesBadges(raw: string): boolean {
  const event = parseJobStatusLiveEvent(raw)
  if (!event) return true
  return FINISHED.has(event.data.status) && !BADGE_NEUTRAL_JOBS.has(event.data.job_type)
}
