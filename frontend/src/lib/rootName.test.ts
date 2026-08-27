import { describe, expect, it } from 'vitest'
import { isGeneratedRootName, nextRootName } from './rootName'

describe('nextRootName', () => {
  it('uses the plain name when nothing has taken it', () => {
    expect(nextRootName('Movies', [])).toBe('Movies')
    expect(nextRootName('Shows', ['Movies'])).toBe('Shows')
  })

  it('numbers from two once the plain name is taken', () => {
    expect(nextRootName('Movies', ['Movies'])).toBe('Movies 2')
    expect(nextRootName('Movies', ['Movies', 'Movies 2'])).toBe('Movies 3')
  })

  it('fills a gap in the sequence rather than counting past it', () => {
    expect(nextRootName('Shows', ['Shows', 'Shows 3'])).toBe('Shows 2')
  })

  it('treats existing names case-insensitively', () => {
    expect(nextRootName('Movies', ['movies'])).toBe('Movies 2')
    expect(nextRootName('Movies', ['MOVIES', 'Movies 2'])).toBe('Movies 3')
  })

  it('ignores surrounding whitespace on stored names', () => {
    expect(nextRootName('Movies', ['  Movies  '])).toBe('Movies 2')
  })

  it('is unaffected by unrelated names', () => {
    expect(nextRootName('Movies', ['4K Remuxes', 'Anime'])).toBe('Movies')
  })
})

describe('isGeneratedRootName', () => {
  it('recognises the names it hands out', () => {
    expect(isGeneratedRootName('Movies')).toBe(true)
    expect(isGeneratedRootName('Shows')).toBe(true)
    expect(isGeneratedRootName('Movies 2')).toBe(true)
    expect(isGeneratedRootName('Shows 17')).toBe(true)
  })

  it('counts an empty box as unnamed rather than chosen', () => {
    expect(isGeneratedRootName('')).toBe(true)
    expect(isGeneratedRootName('   ')).toBe(true)
  })

  it('leaves a name the operator typed alone', () => {
    expect(isGeneratedRootName('4K Remuxes')).toBe(false)
    expect(isGeneratedRootName('Movies Archive')).toBe(false)
    expect(isGeneratedRootName('My Movies')).toBe(false)
  })
})
