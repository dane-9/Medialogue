import { describe, expect, it } from 'vitest'
import { diffReleaseTokens } from './releaseTokens'
import { candidateFormatReasons, combinedDynamicRangeTokenGroups, shareCombinedDynamicRangeReasons, sharedUpgradeReasonTokenGroups, upgradeTokenReason, type UpgradeTokenContext } from './upgradeTokenReason'

const CURRENT = 'The.Thing.1982.1080p.WEB-DL.DD5.1.H.264-GROUP'
const CANDIDATE = 'The.Thing.1982.RERIP.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.5.1-OTHER'

const context: UpgradeTokenContext = {
  upgradeTypes: ['quality', 'custom_format', 'revision'],
  addedCustomFormats: [
    { name: 'Dolby Vision', delta: 45 },
    { name: 'DV HDR10', delta: 50 },
    { name: 'TrueHD Atmos', delta: 60 },
  ],
  currentQuality: '1080p WEB-DL',
  quality: '2160p BluRay REMUX',
}

const reasonFor = (text: string, ctx: UpgradeTokenContext = context) => {
  const token = diffReleaseTokens(CANDIDATE, CURRENT).find((entry) => entry.text === text)
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
    expect(reasonFor('RERIP')).toMatch(/^RERIP - the source was ripped again/)
  })

  it('does not claim a scene tag the candidate was not credited with', () => {
    expect(reasonFor('RERIP', { ...context, upgradeTypes: ['quality'] })).toBeUndefined()
  })

  it('attributes both combined dynamic-range tokens to the most specific format', () => {
    expect(reasonFor('DV')).toBe('Custom Format: DV HDR10 +50')
    expect(reasonFor('HDR')).toBe('Custom Format: DV HDR10 +50')
  })

  it.each(['DV.HDR', 'DV.HDR10+'])('links both tokens in the combined %s display', (suffix) => {
    const tokens = diffReleaseTokens(`Movie.2026.${suffix}.HEVC-GROUP`, 'Movie.2026.HEVC-GROUP')
    const groups = combinedDynamicRangeTokenGroups(tokens)
    const dv = tokens.findIndex((token) => token.text === 'DV')
    const hdr = tokens.findIndex((token) => token.text.startsWith('HDR'))
    expect(groups.get(dv)).toBeDefined()
    expect(groups.get(hdr)).toBe(groups.get(dv))
  })

  it('shares a DV-only reason with the HDR half of the combined label', () => {
    const tokens = diffReleaseTokens('Movie.2026.DV.HDR.HEVC-GROUP', 'Movie.2026.HEVC-GROUP')
    const dvReason = 'Custom Format: Dolby Vision +45'
    const reasons = shareCombinedDynamicRangeReasons(
      tokens,
      tokens.map((token) => token.text === 'DV' ? dvReason : undefined),
    )
    expect(reasons[tokens.findIndex((token) => token.text === 'DV')]).toBe(dvReason)
    expect(reasons[tokens.findIndex((token) => token.text === 'HDR')]).toBe(dvReason)
  })

  it('uses the specific combined detector even when only Dolby Vision improved the score', () => {
    const formats = candidateFormatReasons({
      candidate: {
        formats: [
          { custom_format_name: 'Dolby Vision', matched: true, contribution: 45 },
          { custom_format_name: 'DV HDR10', matched: true, contribution: 0 },
        ],
      },
    }, [{ name: 'Dolby Vision', delta: 45 }])
    const combinedContext = { ...context, addedCustomFormats: formats }
    expect(reasonFor('DV', combinedContext)).toBe('Custom Format: DV HDR10 +0')
    expect(reasonFor('HDR', combinedContext)).toBe('Custom Format: DV HDR10 +0')
  })

  it('groups every adjacent token carrying the same reason', () => {
    const groups = sharedUpgradeReasonTokenGroups([
      undefined,
      'Quality: 1080p WEB-DL to 2160p BluRay REMUX.',
      'Quality: 1080p WEB-DL to 2160p BluRay REMUX.',
      undefined,
      'Custom Format: Lossless Atmos +60',
      'Custom Format: Lossless Atmos +60',
    ])
    expect(groups.get(1)).toBe(groups.get(2))
    expect(groups.get(4)).toBe(groups.get(5))
    expect(groups.get(0)).toBeUndefined()
  })

  it('attributes arbitrary user format names through condition match evidence', () => {
    const formats = candidateFormatReasons({
      candidate: {
        formats: [{
          custom_format_name: 'Preferred lossless audio',
          matched: true,
          contribution: 75,
          conditions: [{
            condition_type: 'release_title',
            matched: true,
            negated: false,
            regex_match: 'TrueHD.Atmos',
            evidence: 'Movie.2026.TrueHD.Atmos-GROUP',
          }],
        }],
      },
    }, [])
    const tokens = diffReleaseTokens('Movie.2026.TrueHD.Atmos-GROUP', 'Movie.2026.DD5.1-GROUP')
    const customContext = { ...context, addedCustomFormats: formats }
    const reasons = tokens.map((token) => upgradeTokenReason(token, customContext))
    const expected = 'Custom Format: Preferred lossless audio +75'
    expect(reasons[tokens.findIndex((token) => token.text === 'TrueHD')]).toBe(expected)
    expect(reasons[tokens.findIndex((token) => token.text === 'Atmos')]).toBe(expected)
    const groups = sharedUpgradeReasonTokenGroups(reasons)
    expect(groups.get(tokens.findIndex((token) => token.text === 'TrueHD')))
      .toBe(groups.get(tokens.findIndex((token) => token.text === 'Atmos')))
  })

  it('matches a Custom Format named with the token itself', () => {
    expect(reasonFor('TrueHD')).toBe('Custom Format: TrueHD Atmos +60')
  })

  it('names the reason after the section the format sits under', () => {
    const sectioned = {
      ...context,
      formatSections: { 'dv hdr10': 'Dynamic Range', 'truehd atmos': 'Audio' },
    }
    expect(reasonFor('DV', sectioned)).toBe('Dynamic Range: DV HDR10 +50')
    expect(reasonFor('TrueHD', sectioned)).toBe('Audio: TrueHD Atmos +60')
    // A format the layout does not place keeps the generic label.
    expect(reasonFor('DV', { ...context, formatSections: {} })).toBe('Custom Format: DV HDR10 +50')
  })

  it('attributes resolution and source tokens to the quality change', () => {
    expect(reasonFor('2160p')).toBe('Quality: 1080p WEB-DL to 2160p BluRay REMUX.')
    expect(reasonFor('BluRay')).toBe('Quality: 1080p WEB-DL to 2160p BluRay REMUX.')
  })

  it('does not attribute a quality token when the quality did not change', () => {
    const same = { ...context, currentQuality: '2160p BluRay REMUX' }
    expect(reasonFor('2160p', same)).toBeUndefined()
  })

  it('says nothing about a different release group, which is not an upgrade', () => {
    expect(reasonFor('OTHER')).toBeUndefined()
  })

  it('stays silent on a token it cannot tie to a reason', () => {
    expect(reasonFor('HEVC')).toBeUndefined()
  })
})
