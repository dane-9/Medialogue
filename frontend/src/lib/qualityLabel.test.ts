import { describe, expect, it } from 'vitest'
import { qualityLabel } from './qualityLabel'

describe('qualityLabel', () => {
  it('drops BluRay from a remux, where it is the only possible source', () => {
    expect(qualityLabel('2160p BluRay REMUX')).toBe('2160p REMUX')
    expect(qualityLabel('1080p BluRay REMUX')).toBe('1080p REMUX')
    expect(qualityLabel('1080i BluRay REMUX')).toBe('1080i REMUX')
    expect(qualityLabel('720p BluRay REMUX')).toBe('720p REMUX')
  })

  it('keeps DVD, which really does distinguish the source', () => {
    expect(qualityLabel('480p DVD REMUX')).toBe('480p DVD REMUX')
    expect(qualityLabel('576i DVD Encode')).toBe('576i DVD Encode')
  })

  it('leaves whole-disc qualities alone', () => {
    expect(qualityLabel('Full Disc BluRay')).toBe('Full Disc BluRay')
    expect(qualityLabel('Full Disc UHD BluRay')).toBe('Full Disc UHD BluRay')
  })

  it('passes other qualities through unchanged', () => {
    expect(qualityLabel('1080p Encode')).toBe('1080p Encode')
    expect(qualityLabel('2160p WEB-DL')).toBe('2160p WEB-DL')
  })

  it('returns an empty string for a missing quality', () => {
    expect(qualityLabel(undefined)).toBe('')
    expect(qualityLabel(null)).toBe('')
  })
})
