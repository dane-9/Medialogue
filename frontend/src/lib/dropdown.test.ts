import { describe, expect, it } from 'vitest'
import { nextIndex, typeAheadIndex } from './dropdown'

describe('nextIndex', () => {
  it('steps forwards and backwards', () => {
    expect(nextIndex(1, 4, 1)).toBe(2)
    expect(nextIndex(1, 4, -1)).toBe(0)
  })

  it('wraps at both ends', () => {
    expect(nextIndex(3, 4, 1)).toBe(0)
    expect(nextIndex(0, 4, -1)).toBe(3)
  })

  it('starts at the near end when nothing is active', () => {
    expect(nextIndex(-1, 4, 1)).toBe(0)
    expect(nextIndex(-1, 4, -1)).toBe(3)
  })

  it('has nowhere to go in an empty list', () => {
    expect(nextIndex(0, 0, 1)).toBe(-1)
  })
})

describe('typeAheadIndex', () => {
  const labels = ["Theatrical Cut", "Director's Cut", 'Extended Cut', 'Despecialized', 'Remastered']

  it('finds the first option starting with the letter', () => {
    expect(typeAheadIndex(labels, 'e', -1)).toBe(2)
  })

  it('matches longer prefixes rather than treating them as separate letters', () => {
    expect(typeAheadIndex(labels, 'des', -1)).toBe(3)
  })

  it('cycles through options sharing a first letter when it is repeated', () => {
    expect(typeAheadIndex(labels, 'd', -1)).toBe(1)
    expect(typeAheadIndex(labels, 'dd', 1)).toBe(3)
    expect(typeAheadIndex(labels, 'ddd', 3)).toBe(1)
  })

  it('ignores case', () => {
    expect(typeAheadIndex(labels, 'REM', -1)).toBe(4)
  })

  it('returns nothing for an unmatched query', () => {
    expect(typeAheadIndex(labels, 'zz', -1)).toBe(-1)
  })

  it('returns nothing for an empty query', () => {
    expect(typeAheadIndex(labels, '', 2)).toBe(-1)
  })
})
