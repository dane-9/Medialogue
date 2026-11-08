import { describe, expect, it } from 'vitest'
import { pathParts } from './pathParts'

describe('pathParts', () => {
  it('splits a Windows path into its leaf and the folder holding it', () => {
    expect(pathParts('\\\\NAS\\Shows 2\\Cartoons\\Super Friends')).toEqual({
      name: 'Super Friends',
      parent: '\\\\NAS\\Shows 2\\Cartoons',
    })
  })

  it('handles posix paths the same way', () => {
    expect(pathParts('/media/movies/Aladdin.1992')).toEqual({ name: 'Aladdin.1992', parent: '/media/movies' })
  })

  it('keeps a bare name with no folder above it', () => {
    expect(pathParts('Aladdin.1992')).toEqual({ name: 'Aladdin.1992', parent: undefined })
  })

  it('is undefined for nothing at all', () => {
    expect(pathParts(undefined)).toBeUndefined()
    expect(pathParts('')).toBeUndefined()
  })
})
