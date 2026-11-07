import { describe, expect, it } from 'vitest'

import {
  browserSafeId,
  contextMenuSelection,
  normalizeMediaView,
  toggleIdSelection,
} from './uiState'

describe('movie selection and context-menu state', () => {
  it('toggles individual movie IDs without mutating the prior selection', () => {
    const original = new Set(['movie-a', 'movie-b'])
    const removed = toggleIdSelection(original, 'movie-a')
    const added = toggleIdSelection(original, 'movie-c')

    expect([...original]).toEqual(['movie-a', 'movie-b'])
    expect([...removed]).toEqual(['movie-b'])
    expect([...added]).toEqual(['movie-a', 'movie-b', 'movie-c'])
  })

  it('preserves a multi-selection when right-clicking inside it and selects only the clicked movie otherwise', () => {
    const selected = new Set(['movie-a', 'movie-b'])
    expect([...contextMenuSelection(selected, 'movie-b')]).toEqual(['movie-a', 'movie-b'])
    expect([...contextMenuSelection(selected, 'movie-c')]).toEqual(['movie-c'])
  })
})

describe('custom-format presentation rules', () => {
  it('creates local IDs even when crypto.randomUUID is unavailable on plain HTTP', () => {
    const first = browserSafeId(null)
    const second = browserSafeId(null)
    expect(first).toMatch(/^local-/)
    expect(second).toMatch(/^local-/)
    expect(second).not.toBe(first)
  })

})

describe('view preference hardening', () => {
  it('accepts only known persisted view values', () => {
    expect(normalizeMediaView('table')).toBe('table')
    expect(normalizeMediaView('cards')).toBe('cards')
    expect(normalizeMediaView('garbage')).toBe('cards')
    expect(normalizeMediaView(null, 'table')).toBe('table')
  })
})
