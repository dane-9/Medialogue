/** Relative torrent age, kept pure so its unit boundaries are testable. */

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const WEEK = 7 * DAY
/** Average calendar lengths: months and years are not whole numbers of days. */
const MONTH = 30.4375 * DAY
const YEAR = 365.25 * DAY

/** Largest unit first: the first one the age fills is the one shown. */
const UNITS: Array<[size: number, name: string]> = [
  [YEAR, 'year'],
  [MONTH, 'month'],
  [WEEK, 'week'],
  [DAY, 'day'],
  [HOUR, 'hour'],
  [MINUTE, 'minute'],
]

/**
 * Describe how long ago something was published, in one unit.
 *
 * A single coarse unit is what you actually compare releases on — "2 weeks ago"
 * settles it, and "2 weeks 3 days 4 hours" does not settle it any better while
 * costing a column of width.
 *
 * Anything under a minute reads "just now", and a timestamp in the future
 * (clock skew between us and the tracker) is clamped to that rather than
 * rendering a negative age.
 */
export function formatAge(published?: string | null, now: number = Date.now()): string {
  if (!published) return '—'
  const at = Date.parse(published)
  if (Number.isNaN(at)) return '—'

  const elapsed = now - at
  if (elapsed < MINUTE) return 'just now'

  for (const [size, name] of UNITS) {
    if (elapsed < size) continue
    const count = Math.floor(elapsed / size)
    return `${count} ${name}${count === 1 ? '' : 's'} ago`
  }
  return 'just now'
}

/** Milliseconds since publication, for sorting. Unknown ages sort as oldest. */
export function ageValue(published?: string | null): number {
  if (!published) return 0
  const at = Date.parse(published)
  return Number.isNaN(at) ? 0 : at
}
