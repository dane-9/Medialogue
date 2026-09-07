import { describe, expect, it } from 'vitest'
import { highlightMatch } from './highlightMatch'

const TITLE = 'Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM'

describe('highlightMatch', () => {
  it('splits the subject around the matched text', () => {
    expect(highlightMatch(TITLE, 'REMUX')).toEqual({
      before: 'Inception 2010 Hybrid 2160p UHD BluRay ',
      hit: 'REMUX',
      after: ' DV HDR HEVC DTS-HD MA 5.1-LM',
    })
  })

  it('keeps the subject intact when the match spans the whole value', () => {
    expect(highlightMatch('DTS-HD MA', 'DTS-HD MA')).toEqual({ before: '', hit: 'DTS-HD MA', after: '' })
  })

  it('returns nothing to highlight when the match is absent or unknown', () => {
    // An HDR type of "Dolby Vision" is derived from "DV" and appears nowhere in
    // the title, so there is no span to mark.
    expect(highlightMatch(TITLE, 'Dolby Vision')).toBeNull()
    expect(highlightMatch(TITLE, undefined)).toBeNull()
    expect(highlightMatch(TITLE, '')).toBeNull()
    expect(highlightMatch('', 'REMUX')).toBeNull()
  })

  it('is case sensitive, because the hit is verbatim from the subject', () => {
    expect(highlightMatch(TITLE, 'remux')).toBeNull()
  })

  it('marks the first of several identical runs', () => {
    expect(highlightMatch('2160p x 2160p', '2160p')).toEqual({ before: '', hit: '2160p', after: ' x 2160p' })
  })
})
