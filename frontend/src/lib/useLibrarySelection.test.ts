import { describe, expect, it } from 'vitest'
import { applySelectionClick } from './useLibrarySelection'

const order = ['a', 'b', 'c', 'd', 'e']

describe('applySelectionClick', () => {
  it('toggles a single title', () => {
    expect([...applySelectionClick(new Set(), null, 'b', order, {}).selected]).toEqual(['b'])
    expect([...applySelectionClick(new Set(['b']), 'b', 'b', order, {}).selected]).toEqual([])
  })

  it('selects the range from the last click with Shift, either direction', () => {
    expect([...applySelectionClick(new Set(['b']), 'b', 'd', order, { shiftKey: true }).selected].sort()).toEqual(['b', 'c', 'd'])
    expect([...applySelectionClick(new Set(['d']), 'd', 'a', order, { shiftKey: true }).selected].sort()).toEqual(['a', 'b', 'c', 'd'])
  })

  it('keeps an earlier selection outside the range', () => {
    expect([...applySelectionClick(new Set(['a', 'c']), 'c', 'e', order, { shiftKey: true }).selected].sort()).toEqual(['a', 'c', 'd', 'e'])
  })

  it('clears a range when Shift-clicking a selected title', () => {
    expect([...applySelectionClick(new Set(['a', 'b', 'c', 'd']), 'a', 'c', order, { shiftKey: true }).selected]).toEqual(['d'])
  })

  it('falls back to a toggle when the last click is no longer on screen', () => {
    expect([...applySelectionClick(new Set(), 'zz', 'c', order, { shiftKey: true }).selected]).toEqual(['c'])
  })
})
