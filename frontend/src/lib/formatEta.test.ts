import { describe, expect, it } from 'vitest'
import { formatEta } from '../api/mappers/downloads'

describe('formatEta', () => {
  it('never shows 60 minutes or 24 hours', () => {
    expect(formatEta(7199)).toBe('2h 0m')
    expect(formatEta(3599)).toBe('1h 0m')
    expect(formatEta(3601)).toBe('1h 1m')
    expect(formatEta(86399)).toBe('1d 0h')
    expect(formatEta(90000)).toBe('1d 1h')
  })

  it('keeps short and unknown values readable', () => {
    expect(formatEta(42)).toBe('42s')
    expect(formatEta(61)).toBe('2m')
    expect(formatEta(-1)).toBe('-')
    expect(formatEta(8640000)).toBe('-')
    expect(formatEta(null)).toBe('-')
  })
})
