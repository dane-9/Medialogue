/** Swarm health from a seeder count, kept pure so its thresholds are testable. */

/** How likely a release is to actually finish downloading. */
export type SwarmHealth = 'dead' | 'weak' | 'healthy' | 'unknown'

/** At or above this many seeders a release downloads without drama. */
export const HEALTHY_SEEDERS = 5

/**
 * Grade a release by its seeder count.
 *
 * The distinction that matters when picking a release is not "more is better"
 * but "will this finish": zero seeders cannot complete at all, and a handful is
 * slow and liable to stall. Above that the exact number stops changing the
 * decision, so everything healthy is graded alike.
 */
export function swarmHealth(seeders?: number | null): SwarmHealth {
  if (seeders === undefined || seeders === null) return 'unknown'
  if (seeders <= 0) return 'dead'
  return seeders < HEALTHY_SEEDERS ? 'weak' : 'healthy'
}

/**
 * Render the swarm as "seeders/leechers".
 *
 * A missing leecher count is common — plenty of trackers report only seeders —
 * and is shown as an em dash rather than a zero, which would claim there is
 * nobody downloading.
 */
export function formatPeers(seeders?: number | null, leechers?: number | null): string {
  const seed = seeders === undefined || seeders === null ? '—' : String(seeders)
  const leech = leechers === undefined || leechers === null ? '—' : String(leechers)
  return `${seed}/${leech}`
}
