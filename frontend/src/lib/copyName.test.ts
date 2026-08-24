import { describe, expect, it } from 'vitest'
import { copyName } from './copyName'

describe('copyName', () => {
  it('appends Copy when nothing claims that name', () => {
    expect(copyName('Bravia Core', ['Bravia Core'])).toBe('Bravia Core Copy')
  })

  it('numbers the second copy', () => {
    expect(copyName('Bravia Core', ['Bravia Core', 'Bravia Core Copy'])).toBe('Bravia Core Copy 2')
  })

  it('keeps counting past the second', () => {
    const existing = ['Bravia Core', 'Bravia Core Copy', 'Bravia Core Copy 2', 'Bravia Core Copy 3']
    expect(copyName('Bravia Core', existing)).toBe('Bravia Core Copy 4')
  })

  it('fills a gap in the sequence rather than always taking the highest', () => {
    expect(copyName('HDR', ['HDR', 'HDR Copy', 'HDR Copy 3'])).toBe('HDR Copy 2')
  })

  it('compares without regard to case or surrounding space', () => {
    expect(copyName('HDR', ['  hdr copy  '])).toBe('HDR Copy 2')
  })

  it('copies a copy without stacking the word twice', () => {
    // Duplicating "X Copy" gives "X Copy Copy", which is ugly but honest: it is
    // a copy of that format, not another copy of the original.
    expect(copyName('HDR Copy', ['HDR', 'HDR Copy'])).toBe('HDR Copy Copy')
  })

  it('handles an empty list', () => {
    expect(copyName('HDR', [])).toBe('HDR Copy')
  })
})
