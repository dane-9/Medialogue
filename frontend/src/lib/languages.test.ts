import { describe, expect, it } from 'vitest'
import { languageLabel, releaseLanguages } from './languages'

describe('releaseLanguages', () => {
  it('passes through the display names the parser produced', () => {
    expect(releaseLanguages({ languages: ['English', 'Japanese'] })).toEqual(['English', 'Japanese'])
  })

  it('keeps the parser\'s Unknown rather than hiding it', () => {
    expect(releaseLanguages({ languages: ['Unknown'] })).toEqual(['Unknown'])
  })

  it('drops duplicates however they were cased', () => {
    expect(releaseLanguages({ languages: ['English', 'english', ' English '] })).toEqual(['English'])
  })

  it('survives a snapshot whose shape is not what we expect', () => {
    expect(releaseLanguages(undefined)).toEqual([])
    expect(releaseLanguages({})).toEqual([])
    expect(releaseLanguages({ languages: 'English' })).toEqual([])
    expect(releaseLanguages({ languages: [1, null, 'English'] })).toEqual(['English'])
    expect(releaseLanguages({ languages: ['', '  '] })).toEqual([])
  })
})

describe('languageLabel', () => {
  it('names a single language outright', () => {
    expect(languageLabel(['French'])).toBe('French')
  })

  it('collapses several into one Multi-Language badge', () => {
    expect(languageLabel(['German', 'Original', 'English'])).toBe('Multi-Language')
  })

  it('has nothing to show for an empty list', () => {
    expect(languageLabel([])).toBeNull()
  })
})
