import { describe, expect, it } from 'vitest'
import { qualityMark } from './qualityMark'

describe('qualityMark', () => {
  it('names the source alongside the rip', () => {
    expect(qualityMark('2160p BluRay REMUX')).toBe('2160p BluRay Remux')
  })

  it('reads a hyphenated quality name', () => {
    expect(qualityMark('WEBDL-1080p')).toBe('1080p WEB-DL')
  })

  it('keeps the p in a resolution lower case', () => {
    expect(qualityMark('1080p BluRay')).toBe('1080p BluRay')
    expect(qualityMark('1080I HDTV')).toBe('1080i HDTV')
  })

  it('does not shred WEB-DL on its own hyphen', () => {
    expect(qualityMark('1080p WEB-DL')).toBe('1080p WEB-DL')
  })

  it('recognises each source it knows', () => {
    expect(qualityMark('1080p WEBRip')).toBe('1080p WEBRip')
    expect(qualityMark('480p DVD')).toBe('480p DVD')
    expect(qualityMark('1080p HDTV')).toBe('1080p HDTV')
    expect(qualityMark('2160p Full Disc')).toBe('2160p Full Disc')
  })

  it('lets an edition outrank remux for the last slot', () => {
    expect(qualityMark('2160p BluRay REMUX', "Director's Cut")).toBe("2160p BluRay Director's Cut")
  })

  it('takes only the first segment of a compound edition', () => {
    expect(qualityMark('1080p BluRay', 'Extended · Hybrid')).toBe('1080p BluRay Extended')
  })

  it('handles a resolution with no recognisable source', () => {
    expect(qualityMark('2160p')).toBe('2160p')
  })

  it('handles a source with no resolution', () => {
    expect(qualityMark('Full Disc')).toBe('Full Disc')
  })

  it('returns nothing when there is no quality', () => {
    expect(qualityMark()).toBe('')
    expect(qualityMark('')).toBe('')
  })
})
