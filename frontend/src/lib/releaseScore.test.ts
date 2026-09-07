import { describe, expect, it } from 'vitest'
import { releaseScoreBreakdown } from './releaseScore'

const snapshot = (breakdown: unknown[], extra: Record<string, unknown> = {}) => ({
  current_score_snapshot: { profile_name: 'Default', total_score: 55, breakdown, ...extra },
})

const row = (name: string, matched: boolean, contribution: number) => ({
  custom_format_id: `id-${name}`,
  custom_format_name: name,
  matched,
  contribution,
})

describe('releaseScoreBreakdown', () => {
  it('returns only the formats that matched', () => {
    const result = releaseScoreBreakdown(snapshot([
      row('DTS-HD MA', true, 55),
      row('Dolby Atmos', false, 0),
      row('HDR10', true, 10),
    ]))
    expect(result.matched.map((item) => item.name)).toEqual(['DTS-HD MA', 'HDR10'])
    expect(result.profileName).toBe('Default')
    expect(result.total).toBe(55)
  })

  it('orders by how much each format moved the score', () => {
    const result = releaseScoreBreakdown(snapshot([
      row('Small', true, 5),
      row('Negative', true, -40),
      row('Big', true, 55),
    ]))
    expect(result.matched.map((item) => item.name)).toEqual(['Big', 'Negative', 'Small'])
  })

  it('keeps a matched format that scores nothing, ordered last', () => {
    const result = releaseScoreBreakdown(snapshot([row('Zero', true, 0), row('Ten', true, 10)]))
    expect(result.matched.map((item) => item.name)).toEqual(['Ten', 'Zero'])
  })

  it('breaks ties on name so the order is stable', () => {
    const result = releaseScoreBreakdown(snapshot([row('Beta', true, 10), row('Alpha', true, 10)]))
    expect(result.matched.map((item) => item.name)).toEqual(['Alpha', 'Beta'])
  })

  it('has nothing to show for a release scored before snapshots existed', () => {
    expect(releaseScoreBreakdown(undefined)).toEqual({ matched: [] })
    expect(releaseScoreBreakdown({})).toEqual({ matched: [] })
    expect(releaseScoreBreakdown({ current_score_snapshot: {} }).matched).toEqual([])
  })

  it('survives malformed rows rather than throwing', () => {
    const result = releaseScoreBreakdown(snapshot([
      null,
      'not a row',
      { matched: true },
      row('Real', true, 7),
    ]))
    expect(result.matched.map((item) => item.name)).toEqual(['Real'])
  })

  it('defaults a missing contribution to zero', () => {
    const result = releaseScoreBreakdown(snapshot([
      { custom_format_id: 'x', custom_format_name: 'No score', matched: true },
    ]))
    expect(result.matched).toEqual([{ id: 'x', name: 'No score', contribution: 0 }])
  })
})
