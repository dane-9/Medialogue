import { describe, expect, it } from 'vitest'
import { rejectionLabel, upgradeResultsToReleases } from './upgradeResults'
import type { UpgradeSearchResults } from '../types'

describe('rejectionLabel', () => {
  it.each([
    ['A different title: 50 First Dates.', 'Different title'],
    ['The release is lower quality than the current upgrade floor.', 'Lower quality'],
    ['The release scores below the release it would replace, or misses the profile\'s minimums.', 'Lower score'],
    ['The release quality is not recognized by this Quality Profile.', 'Blocked by profile'],
  ])('names %s', (reason, label) => {
    expect(rejectionLabel([reason])).toBe(label)
  })

  it('stays generic for a reason it does not know, or none', () => {
    expect(rejectionLabel(['Something new.'])).toBe('Rejected')
    expect(rejectionLabel([])).toBe('Rejected')
  })
})

describe('upgradeResultsToReleases', () => {
  const rejected = (reasons: string[]) => upgradeResultsToReleases({
    searchedAt: '2026-01-01T00:00:00Z',
    results: [{ id: 'a', eligible: false, rejectionReasons: reasons, matchedFormatNames: [] }],
  } as unknown as UpgradeSearchResults, 'job', 'movies')[0]

  it('sorts a rejected result last without claiming the profile blocked it', () => {
    const release = rejected(['A different title: Other.'])
    expect(release.qualityAllowed).toBe(false)
    expect(release.warnings).toEqual([])
  })

  it('still marks a blocklisted release', () => {
    const release = rejected(['Blocklisted: failed before'])
    expect(release.blocklisted).toBe(true)
    expect(release.blocklistReason).toBe('failed before')
  })
})
