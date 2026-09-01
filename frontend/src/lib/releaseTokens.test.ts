import { describe, expect, it } from 'vitest'
import { diffReleaseTokens, releaseTokens } from './releaseTokens'

const text = (name: string) => releaseTokens(name).map((token) => token.text)
const changed = (candidate: string, current: string) =>
  diffReleaseTokens(candidate, current).filter((token) => token.changed).map((token) => token.text)
const rebuilt = (name: string) => releaseTokens(name).map((token) => token.sep + token.text).join('')

describe('releaseTokens', () => {
  it('splits on dots only, so hyphenated tokens stay whole', () => {
    expect(text('Dune.2021.2160p.WEB-DL.DTS-HD.MA.5.1-GROUP')).toEqual([
      'Dune', '2021', '2160p', 'WEB-DL', 'DTS-HD', 'MA', '5', '1', 'GROUP',
    ])
  })

  it('leaves a name with no release group alone', () => {
    expect(text('Dune.2021.2160p.WEB-DL.H.264')).toEqual(['Dune', '2021', '2160p', 'WEB-DL', 'H', '264'])
  })

  it('rebuilds the original name from its separators', () => {
    for (const name of ['Dune.2021.2160p.WEB-DL.DTS-HD.MA.5.1-GROUP', 'A.B.C', 'Solo']) {
      expect(rebuilt(name)).toBe(name)
    }
  })

  it('returns nothing for an empty name', () => {
    expect(releaseTokens('')).toEqual([])
  })
})

describe('diffReleaseTokens', () => {
  it('marks only the tokens the current release lacks', () => {
    expect(changed(
      'Aliens.1986.Extended.Cut.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1.REPACK-GROUP',
      'Aliens.1986.Extended.Cut.2160p.UHD.BluRay.REMUX.HDR.HEVC.DTS-HD.MA.5.1-GROUP',
    )).toEqual(['REPACK'])
  })

  it('marks a resolution change and the codecs that came with it', () => {
    expect(changed(
      'The.Thing.1982.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.5.1-OTHER',
      'The.Thing.1982.1080p.WEB-DL.DD5.1.H.264-GROUP',
    )).toEqual(['2160p', 'UHD', 'BluRay', 'REMUX', 'DV', 'HDR', 'HEVC', 'TrueHD', '5', 'OTHER'])
  })

  it('marks a group change without dragging the token it was fused to', () => {
    expect(changed('Dune.2021.2160p.WEB-DL.DTS-HD.MA.5.1-OTHER', 'Dune.2021.2160p.WEB-DL.DTS-HD.MA.5.1-GROUP'))
      .toEqual(['OTHER'])
  })

  it('ignores case, because a case flip is not an upgrade', () => {
    expect(changed('Dune.2021.2160p.WEB-DL-GROUP', 'Dune.2021.2160P.web-dl-GROUP')).toEqual([])
  })

  it('treats repeats as a multiset rather than a set', () => {
    expect(changed('A.5.1.5.1-G', 'A.5.1-G')).toEqual(['5', '1'])
  })

  it('marks everything when there is nothing to compare against', () => {
    expect(changed('Dune.2021-G', '')).toEqual(['Dune', '2021', 'G'])
  })
})
