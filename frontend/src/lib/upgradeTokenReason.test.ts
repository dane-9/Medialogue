import { describe, expect, it } from 'vitest'
import { diffReleaseTokens } from './releaseTokens'
import { upgradeTokenReason, type UpgradeTokenContext } from './upgradeTokenReason'

const CURRENT = 'The.Thing.1982.1080p.WEB-DL.DD5.1.H.264-GROUP'
const CANDIDATE = 'The.Thing.1982.REAL.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.5.1-OTHER'

const context: UpgradeTokenContext = {
  upgradeTypes: ['quality', 'custom_format', 'revision'],
  addedCustomFormats: [{ name: 'Dolby Vision', delta: 45 }, { name: 'TrueHD Atmos', delta: 60 }],
  currentQuality: '1080p WEB-DL',
  quality: '2160p BluRay REMUX',
  currentReleaseName: CURRENT,
}

const reasonFor = (text: string, ctx: UpgradeTokenContext = context) => {
  const token = diffReleaseTokens(CANDIDATE, ctx.currentReleaseName).find((entry) => entry.text === text)
  if (!token) throw new Error(`no token ${text}`)
  return upgradeTokenReason(token, ctx)
}

describe('upgradeTokenReason', () => {
  it('says nothing about a token the current release already has', () => {
    const token = diffReleaseTokens(CANDIDATE, CURRENT).find((entry) => entry.text === 'Thing')
    expect(token?.changed).toBe(false)
    expect(upgradeTokenReason(token!, context)).toBeUndefined()
  })

  it('explains a scene re-release tag', () => {
    expect(reasonFor('REAL')).toMatch(/^REAL - issued to supersede/)
  })

  it('does not claim a scene tag the candidate was not credited with', () => {
    expect(reasonFor('REAL', { ...context, upgradeTypes: ['quality'] })).toBeUndefined()
  })

  it('maps a scene abbreviation to the Custom Format it earned', () => {
    expect(reasonFor('DV')).toBe('Custom Format: Dolby Vision (+45 to the score).')
  })

  it('matches a Custom Format named with the token itself', () => {
    expect(reasonFor('TrueHD')).toBe('Custom Format: TrueHD Atmos (+60 to the score).')
  })

  it('attributes resolution and source tokens to the quality change', () => {
    expect(reasonFor('2160p')).toBe('Quality: 1080p WEB-DL to 2160p BluRay REMUX.')
    expect(reasonFor('BluRay')).toBe('Quality: 1080p WEB-DL to 2160p BluRay REMUX.')
  })

  it('does not attribute a quality token when the quality did not change', () => {
    const same = { ...context, currentQuality: '2160p BluRay REMUX' }
    expect(reasonFor('2160p', same)).toBeUndefined()
  })

  it('names both release groups', () => {
    expect(reasonFor('OTHER')).toBe('Release group: GROUP to OTHER.')
  })

  it('stays silent on a token it cannot tie to a reason', () => {
    expect(reasonFor('HEVC')).toBeUndefined()
  })
})
