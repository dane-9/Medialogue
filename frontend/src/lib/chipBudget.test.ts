import { describe, expect, it } from 'vitest'
import { fitChips } from './chipBudget'

const repeat = (count: number, label: string) => Array.from({ length: count }, () => label)

describe('fitChips', () => {
  it('shows every chip when the list is short', () => {
    expect(fitChips(['HDR', 'DV'])).toBe(2)
  })

  it('caps a list of short names at the item limit', () => {
    expect(fitChips(repeat(20, 'DV'))).toBe(9)
  })

  it('stops sooner when the names are long', () => {
    const fitted = fitChips(repeat(20, 'Bravia Core Remux Tier Two'))
    expect(fitted).toBeGreaterThan(0)
    expect(fitted).toBeLessThan(9)
  })

  it('always shows at least one chip, however long its name', () => {
    expect(fitChips(['A name far longer than any sensible character budget allows for one chip'])).toBe(1)
  })

  it('shows one and defers the rest when the first name is enormous', () => {
    const labels = ['A name quite deliberately longer than the whole character budget allows for a single chip on one card', 'HDR', 'DV']
    expect(fitChips(labels)).toBe(1)
  })

  it('returns nothing for an empty list', () => {
    expect(fitChips([])).toBe(0)
  })

  it('respects an explicit item limit', () => {
    expect(fitChips(repeat(20, 'DV'), 3)).toBe(3)
  })

  it('respects an explicit character budget', () => {
    expect(fitChips(repeat(20, 'DV'), 9, 20)).toBe(2)
  })
})
