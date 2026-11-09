import { describe, expect, it } from 'vitest'
import { ageValue, formatAge, formatAgeBrief, formatUntil } from './age'

const NOW = Date.parse('2026-08-24T12:00:00Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const WEEK = 7 * DAY

describe('formatAgeBrief', () => {
  it('drops only the "ago"', () => {
    expect(formatAgeBrief(ago(2 * WEEK), NOW)).toBe('2 weeks')
    expect(formatAgeBrief(ago(30_000), NOW)).toBe('just now')
    expect(formatAgeBrief(undefined, NOW)).toBe('-')
  })
})

describe('formatAge', () => {
  it('describes an age in a single coarse unit', () => {
    expect(formatAge(ago(14 * HOUR), NOW)).toBe('14 hours ago')
    expect(formatAge(ago(2 * WEEK), NOW)).toBe('2 weeks ago')
    expect(formatAge(ago(4 * 30.4375 * DAY), NOW)).toBe('4 months ago')
    expect(formatAge(ago(4 * 365.25 * DAY), NOW)).toBe('4 years ago')
  })

  it('uses the singular for exactly one of a unit', () => {
    expect(formatAge(ago(DAY), NOW)).toBe('1 day ago')
    expect(formatAge(ago(HOUR), NOW)).toBe('1 hour ago')
  })

  it('rounds down rather than up, so nothing looks older than it is', () => {
    expect(formatAge(ago(DAY + 23 * HOUR), NOW)).toBe('1 day ago')
  })

  it('rolls over exactly at each unit boundary', () => {
    expect(formatAge(ago(HOUR - 1), NOW)).toBe('59 minutes ago')
    expect(formatAge(ago(HOUR), NOW)).toBe('1 hour ago')
    expect(formatAge(ago(DAY - 1), NOW)).toBe('23 hours ago')
    expect(formatAge(ago(DAY), NOW)).toBe('1 day ago')
    expect(formatAge(ago(WEEK - 1), NOW)).toBe('6 days ago')
    expect(formatAge(ago(WEEK), NOW)).toBe('1 week ago')
  })

  it('collapses anything under a minute to just now', () => {
    expect(formatAge(ago(30_000), NOW)).toBe('just now')
    expect(formatAge(ago(0), NOW)).toBe('just now')
  })

  it('clamps a future timestamp instead of reporting a negative age', () => {
    expect(formatAge(new Date(NOW + 5 * HOUR).toISOString(), NOW)).toBe('just now')
  })

  it('renders a dash for a missing or unparseable date', () => {
    expect(formatAge(undefined, NOW)).toBe('-')
    expect(formatAge(null, NOW)).toBe('-')
    expect(formatAge('not a date', NOW)).toBe('-')
  })
})

describe('ageValue', () => {
  it('orders newer above older', () => {
    expect(ageValue(ago(HOUR))).toBeGreaterThan(ageValue(ago(WEEK)))
  })

  it('sorts an unknown age as the oldest', () => {
    expect(ageValue(undefined)).toBe(0)
    expect(ageValue('not a date')).toBe(0)
  })
})

describe('formatUntil', () => {
  const ahead = (ms: number) => new Date(NOW + ms).toISOString()

  it('counts down in a single coarse unit', () => {
    expect(formatUntil(ahead(14 * HOUR), NOW)).toBe('in 14 hours')
    expect(formatUntil(ahead(2 * WEEK), NOW)).toBe('in 2 weeks')
  })

  it('uses the singular for exactly one of a unit', () => {
    expect(formatUntil(ahead(DAY), NOW)).toBe('in 1 day')
  })

  it('rounds down, so nothing looks further away than it is', () => {
    expect(formatUntil(ahead(2 * DAY - MINUTE), NOW)).toBe('in 1 day')
  })

  it('reports anything already past as expired', () => {
    expect(formatUntil(ahead(-HOUR), NOW)).toBe('expired')
    expect(formatUntil(new Date(NOW).toISOString(), NOW)).toBe('expired')
  })

  it('collapses the final minute rather than counting seconds', () => {
    expect(formatUntil(ahead(30_000), NOW)).toBe('in under a minute')
  })

  it('renders a dash for a missing or unparseable date', () => {
    expect(formatUntil(undefined, NOW)).toBe('-')
    expect(formatUntil('not a date', NOW)).toBe('-')
  })
})
