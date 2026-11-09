

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const WEEK = 7 * DAY

const MONTH = 30.4375 * DAY
const YEAR = 365.25 * DAY

const UNITS: Array<[size: number, name: string]> = [
  [YEAR, 'year'],
  [MONTH, 'month'],
  [WEEK, 'week'],
  [DAY, 'day'],
  [HOUR, 'hour'],
  [MINUTE, 'minute'],
]

export function formatAge(published?: string | null, now: number = Date.now()): string {
  if (!published) return '-'
  const at = Date.parse(published)
  if (Number.isNaN(at)) return '-'

  const elapsed = now - at
  if (elapsed < MINUTE) return 'just now'

  for (const [size, name] of UNITS) {
    if (elapsed < size) continue
    const count = Math.floor(elapsed / size)
    return `${count} ${name}${count === 1 ? '' : 's'} ago`
  }
  return 'just now'
}

/** For a column already headed Age: "2 years", where "ago" would only add width. */
export function formatAgeBrief(published?: string | null, now: number = Date.now()): string {
  return formatAge(published, now).replace(/ ago$/, '')
}

export function formatUntil(expires?: string | null, now: number = Date.now()): string {
  if (!expires) return '-'
  const at = Date.parse(expires)
  if (Number.isNaN(at)) return '-'

  const remaining = at - now
  if (remaining <= 0) return 'expired'
  if (remaining < MINUTE) return 'in under a minute'

  for (const [size, name] of UNITS) {
    if (remaining < size) continue
    const count = Math.floor(remaining / size)
    return `in ${count} ${name}${count === 1 ? '' : 's'}`
  }
  return 'in under a minute'
}

export function ageValue(published?: string | null): number {
  if (!published) return 0
  const at = Date.parse(published)
  return Number.isNaN(at) ? 0 : at
}
