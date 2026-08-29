import { describe, expect, it } from 'vitest'
import { isTaskOverdue } from './taskTiming'

describe('isTaskOverdue', () => {
  const now = Date.parse('2026-08-29T12:00:00Z')

  it('allows normal scheduler polling jitter', () => {
    expect(isTaskOverdue('2026-08-29T11:59:50Z', 60, now)).toBe(false)
  })

  it('flags a minute task after its grace period', () => {
    expect(isTaskOverdue('2026-08-29T11:59:44Z', 60, now)).toBe(true)
  })

  it('does not flag future, missing, or malformed timestamps', () => {
    expect(isTaskOverdue('2026-08-29T12:00:01Z', 60, now)).toBe(false)
    expect(isTaskOverdue(undefined, 60, now)).toBe(false)
    expect(isTaskOverdue('not-a-date', 60, now)).toBe(false)
  })
})
