import { describe, expect, it } from 'vitest'
import { parsedResolution, splitQuality } from './quality'

describe('splitQuality', () => {
  it('drops the source, keeping only the kind of release', () => {
    expect(splitQuality('2160p BluRay REMUX')).toEqual({ resolution: '2160p', type: 'REMUX' })
    expect(splitQuality('1080p BluRay REMUX')).toEqual({ resolution: '1080p', type: 'REMUX' })
    expect(splitQuality('480i NTSC DVD REMUX')).toEqual({ resolution: '480i', type: 'REMUX' })
    expect(splitQuality('576p PAL DVD REMUX')).toEqual({ resolution: '576p', type: 'REMUX' })
  })

  it('leaves qualities whose kind is not a source word alone', () => {
    expect(splitQuality('1080p WEB-DL')).toEqual({ resolution: '1080p', type: 'WEB-DL' })
    expect(splitQuality('2160p WEBRip')).toEqual({ resolution: '2160p', type: 'WEBRip' })
    expect(splitQuality('720p HDTV')).toEqual({ resolution: '720p', type: 'HDTV' })
    expect(splitQuality('2160p Encode')).toEqual({ resolution: '2160p', type: 'Encode' })
  })

  it('collapses every Full Disc variant to one label', () => {
    expect(splitQuality('Full Disc DVD5').type).toBe('Full Disc')
    expect(splitQuality('Full Disc DVD9').type).toBe('Full Disc')
    expect(splitQuality('Full Disc DVD5/DVD9').type).toBe('Full Disc')
  })

  it('shows the resolution a Full Disc release stated', () => {
    expect(splitQuality('Full Disc DVD9', '1080p')).toEqual({ resolution: '1080p', type: 'Full Disc' })
  })

  it('shows no resolution when the release name never stated one', () => {
    expect(splitQuality('Full Disc DVD5')).toEqual({ resolution: undefined, type: 'Full Disc' })
  })

  it('prefers the parsed resolution over the canonical name', () => {
    expect(splitQuality('1080p WEB-DL', '2160p').resolution).toBe('2160p')
  })

  it('handles an unknown or empty quality without crashing', () => {
    expect(splitQuality(undefined).type).toBe('Unknown')
    expect(splitQuality('').type).toBe('Unknown')
    expect(splitQuality('Something Odd')).toEqual({ resolution: undefined, type: 'Something Odd' })
  })
})

describe('parsedResolution', () => {
  it('reads the resolution out of the parser snapshot', () => {
    expect(parsedResolution({ quality: { resolution: '2160p' } })).toBe('2160p')
  })

  it('returns nothing for a snapshot that lacks one', () => {
    expect(parsedResolution({ quality: { resolution: null } })).toBeUndefined()
    expect(parsedResolution({ quality: {} })).toBeUndefined()
    expect(parsedResolution({})).toBeUndefined()
    expect(parsedResolution(undefined)).toBeUndefined()
    expect(parsedResolution({ quality: 'nonsense' })).toBeUndefined()
  })
})
