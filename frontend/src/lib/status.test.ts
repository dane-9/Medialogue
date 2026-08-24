import { describe, expect, it } from 'vitest'
import { needsAttention, statusTone } from './status'

describe('statusTone', () => {
  it('treats settled states as green', () => {
    expect(statusTone('Present')).toBe('green')
    expect(statusTone('Verified')).toBe('green')
  })

  it('treats in-flight states as amber', () => {
    expect(statusTone('Missing')).toBe('amber')
    expect(statusTone('Downloading')).toBe('amber')
  })

  it('treats broken states as red', () => {
    expect(statusTone('Conflict')).toBe('red')
    expect(statusTone('Duplicate')).toBe('red')
  })

  it('falls back to neutral for anything unrecognised', () => {
    expect(statusTone('Unknown')).toBe('neutral')
    expect(statusTone('')).toBe('neutral')
  })
})

describe('needsAttention', () => {
  it('is quiet for a settled item', () => {
    expect(needsAttention('Present')).toBe(false)
    expect(needsAttention('Archived')).toBe(false)
  })

  it('flags anything not settled', () => {
    expect(needsAttention('Missing')).toBe(true)
    expect(needsAttention('Conflict')).toBe(true)
  })

  it('flags an unrecognised state rather than assuming it is fine', () => {
    expect(needsAttention('Something new')).toBe(true)
  })
})
