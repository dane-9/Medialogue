import { describe, expect, it } from 'vitest'
import { primaryUpgrade, upgradeChanges } from './primaryUpgrade'

const base = { upgradeTypes: [] as string[], addedCustomFormats: [], scoreDelta: 0 }

describe('primaryUpgrade', () => {
  it('leads with a resolution change', () => {
    const value = primaryUpgrade({ ...base, currentQuality: '1080p Encode', quality: '2160p REMUX', upgradeTypes: ['quality', 'custom_format'],
      addedCustomFormats: [{ name: 'Dolby Vision', delta: 40 }, { name: 'TrueHD Atmos', delta: 60 }], scoreDelta: 190 })
    expect(value?.label).toBe('1080p Encode → 2160p REMUX')
    expect(value?.details).toEqual(['1080p Encode → 2160p REMUX', 'Custom Formats +190 (TrueHD Atmos, Dolby Vision)'])
  })

  it('names only the resolution when the source stays', () => {
    expect(primaryUpgrade({ ...base, currentQuality: '1080p REMUX', quality: '2160p REMUX', upgradeTypes: ['quality'] })?.label).toBe('1080p → 2160p')
  })

  it('names the source when the resolution stays', () => {
    expect(primaryUpgrade({ ...base, currentQuality: '2160p WEB-DL', quality: '2160p REMUX', upgradeTypes: ['quality'] })?.label).toBe('WEB-DL → REMUX')
  })

  it('falls back to the biggest Custom Format gained, then the score', () => {
    expect(primaryUpgrade({ ...base, upgradeTypes: ['custom_format'], addedCustomFormats: [{ name: 'DV', delta: 50 }], scoreDelta: 50 })?.label).toBe('DV')
    expect(primaryUpgrade({ ...base, upgradeTypes: ['custom_format'], scoreDelta: 15 })?.label).toBe('CF +15')
  })

  it('ranks a better picture above a re-release, and a re-release above audio', () => {
    expect(primaryUpgrade({ ...base, upgradeTypes: ['custom_format', 'revision'], addedCustomFormats: [{ name: 'TrueHD', delta: 15 }], scoreDelta: 15 })?.label).toBe('RERIP')
    expect(primaryUpgrade({ ...base, upgradeTypes: ['custom_format', 'repack'], addedCustomFormats: [{ name: 'TrueHD', delta: 60 }, { name: 'DV HDR10+', delta: 20 }], scoreDelta: 80 })?.label).toBe('DV HDR10+')
    expect(primaryUpgrade({ ...base, upgradeTypes: ['custom_format'], addedCustomFormats: [{ name: 'TrueHD', delta: 15 }], scoreDelta: 15 })?.label).toBe('TrueHD')
  })

  it('shows a season pack and a revision', () => {
    expect(primaryUpgrade({ ...base, upgradeTypes: ['season_pack', 'repack'] })?.label).toBe('SEASON PACK')
    expect(primaryUpgrade({ ...base, upgradeTypes: ['proper'] })?.label).toBe('PROPER')
    expect(primaryUpgrade(base)).toBeNull()
  })
})

describe('upgradeChanges', () => {
  it('lists each aspect that changes, and leaves out formats that only name the source', () => {
    const format = (name: string, contribution: number, group?: string) => ({ id: name, name, contribution, group })
    const rows = upgradeChanges({
      currentQuality: '1080p WEB-DL', quality: '2160p REMUX', upgradeTypes: ['quality', 'custom_format', 'repack'],
      currentMatchedFormats: [format('WEB-DL', 5), format('DD', 5, 'Audio'), format('x264', 0, 'Video'), format('h264', 0, 'Codec')],
      candidateMatchedFormats: [format('Repack', 5, 'Release'), format('Remux', 20), format('TrueHD', 60, 'Audio'), format('DV HDR10+', 100, 'Dynamic Range'), format('x264', 0, 'Video')],
      currentCustomFormatScore: 10, customFormatScore: 180,
    })
    expect(rows.map((row) => [row.label, row.from, row.to, row.tone])).toEqual([
      ['Quality', '1080p WEB-DL', '2160p REMUX', 'better'],
      ['Release', 'Original', 'REPACK', 'better'],
      ['Dynamic Range', '—', 'DV HDR10+', 'better'],
      ['Audio', 'DD', 'TrueHD', 'better'],
    ])
  })
})
