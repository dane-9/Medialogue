import { describe, expect, it } from 'vitest'
import { releaseSearchTerm } from './ChangeMatchModal'

describe('releaseSearchTerm', () => {
  it('searches for the title in front of the year', () => {
    expect(releaseSearchTerm('Aliens.1986.Extended.Cut.2160p.UHD.BluRay.REMUX-GROUP', 'Alien')).toBe('Aliens')
    expect(releaseSearchTerm('Blade Runner 2049 2017 1080p BluRay x264-GROUP', 'x')).toBe('Blade Runner 2049')
    expect(releaseSearchTerm('1917.2019.2160p.REMUX', 'x')).toBe('1917')
  })

  it('falls back to the movie title when the name says nothing', () => {
    expect(releaseSearchTerm('', 'Heat')).toBe('Heat')
  })
})
