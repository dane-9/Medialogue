import { describe, expect, it } from 'vitest'
import { editionLabel, qualityMark } from './qualityMark'

describe('qualityMark', () => {
  // The tiers the server can actually store, from app/parser/quality.py. Every
  // one has to survive to the label intact: remux versus encode is the whole
  // point of the ladder.
  it('shows each stored tier as it is named', () => {
    expect(qualityMark('2160p BluRay REMUX')).toBe('2160p BluRay REMUX')
    expect(qualityMark('1080p BluRay REMUX')).toBe('1080p BluRay REMUX')
    expect(qualityMark('1080p Encode')).toBe('1080p Encode')
    expect(qualityMark('2160p Encode')).toBe('2160p Encode')
    expect(qualityMark('1080p WEB-DL')).toBe('1080p WEB-DL')
    expect(qualityMark('1080p WEBRip')).toBe('1080p WEBRip')
    expect(qualityMark('1080p HDTV')).toBe('1080p HDTV')
    expect(qualityMark('480p DVD REMUX')).toBe('480p DVD REMUX')
    expect(qualityMark('480p DVD Encode')).toBe('480p DVD Encode')
    expect(qualityMark('Full Disc BluRay')).toBe('Full Disc BluRay')
    expect(qualityMark('Full Disc UHD BluRay')).toBe('Full Disc UHD BluRay')
  })

  it('keeps REMUX in capitals however it arrives', () => {
    expect(qualityMark('1080p BluRay Remux')).toBe('1080p BluRay REMUX')
    expect(qualityMark('1080p BluRay remux')).toBe('1080p BluRay REMUX')
  })

  it('keeps the p or i in a resolution lower case', () => {
    expect(qualityMark('1080I HDTV')).toBe('1080i HDTV')
    expect(qualityMark('1080P Encode')).toBe('1080p Encode')
  })

  it('does not shred WEB-DL on its own hyphen', () => {
    expect(qualityMark('2160p WEB-DL')).toBe('2160p WEB-DL')
  })

  it('passes an unrecognised quality through rather than reshaping it', () => {
    expect(qualityMark('2160p')).toBe('2160p')
    expect(qualityMark('Full Disc')).toBe('Full Disc')
    expect(qualityMark('  1080p   Encode  ')).toBe('1080p Encode')
  })

  it('returns nothing when there is no quality', () => {
    expect(qualityMark()).toBe('')
    expect(qualityMark('')).toBe('')
  })
})

describe('editionLabel', () => {
  it('takes only the first segment of a compound edition', () => {
    expect(editionLabel('Extended · Hybrid')).toBe('Extended')
    expect(editionLabel("Director's Cut")).toBe("Director's Cut")
  })

  it('trims the segment it keeps', () => {
    expect(editionLabel('  Extended Cut  ·  Hybrid ')).toBe('Extended Cut')
  })

  it('returns nothing when there is no edition', () => {
    expect(editionLabel()).toBe('')
    expect(editionLabel('')).toBe('')
  })
})
