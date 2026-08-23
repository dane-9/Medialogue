import { describe, expect, it } from 'vitest'

import {
  browserSafeId,
  contextMenuSelection,
  duplicateLoserIds,
  duplicatePreviewReady,
  isRegexConditionType,
  normalizeMediaView,
  problemMatchesFilter,
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

describe('Problems and duplicate-resolution UI state', () => {
  const duplicate = { code: 'DUPLICATE_PHYSICAL_RELEASE', severity: 'high' } as const
  const pathProblem = { code: 'PATH_MAPPING_FAILED', severity: 'medium' } as const
  const identity = { code: 'TMDB_IDENTITY_UNRESOLVED', severity: 'low' } as const

  it('applies reason and severity filters consistently', () => {
    expect(problemMatchesFilter(duplicate, 'duplicates', 'all')).toBe(true)
    expect(problemMatchesFilter(pathProblem, 'paths', 'medium')).toBe(true)
    expect(problemMatchesFilter(identity, 'identity', 'low')).toBe(true)
    expect(problemMatchesFilter(identity, 'paths', 'all')).toBe(false)
    expect(problemMatchesFilter(duplicate, 'duplicates', 'low')).toBe(false)
  })

  it('requires a real winner and at least one loser before a duplicate preview can be requested', () => {
    const releases = ['release-a', 'release-b', 'release-c']
    expect(duplicateLoserIds(releases, 'release-b')).toEqual(['release-a', 'release-c'])
    expect(duplicatePreviewReady(releases, 'release-b')).toBe(true)
    expect(duplicatePreviewReady(releases, '')).toBe(false)
    expect(duplicatePreviewReady(releases, 'not-a-candidate')).toBe(false)
    expect(duplicatePreviewReady(['release-a'], 'release-a')).toBe(false)
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

  it('supports regex matching for every condition type', () => {
    expect(isRegexConditionType('release_title')).toBe(true)
    expect(isRegexConditionType('release_group')).toBe(true)
    expect(isRegexConditionType('quality')).toBe(true)
    expect(isRegexConditionType('release_attribute')).toBe(true)
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
