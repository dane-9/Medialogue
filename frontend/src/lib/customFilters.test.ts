import { describe, expect, it } from 'vitest'
import { fieldChoices, matchesFilter, type FilterField } from './customFilters'

interface Item { title: string; year?: number; monitored: boolean; quality?: string; tags: string[]; seen?: string }

const fields: FilterField<Item>[] = [
  { key: 'title', label: 'Title', kind: 'string', get: (item) => item.title },
  { key: 'year', label: 'Year', kind: 'number', get: (item) => item.year },
  { key: 'monitored', label: 'Monitored', kind: 'bool', get: (item) => item.monitored },
  { key: 'quality', label: 'Quality', kind: 'exact', get: (item) => item.quality },
  { key: 'tags', label: 'Tags', kind: 'array', get: (item) => item.tags },
  { key: 'seen', label: 'Seen', kind: 'date', get: (item) => item.seen },
]

const now = Date.parse('2026-11-17T12:00:00Z')
const dune: Item = { title: 'Dune', year: 2021, monitored: true, quality: '2160p', tags: ['Kids', 'sci-fi'], seen: '2026-11-15T00:00:00Z' }
const heat: Item = { title: 'Heat', year: 1995, monitored: false, quality: '1080p', tags: [], seen: '2026-01-01T00:00:00Z' }

describe('custom filters', () => {
  it('needs every condition, and any value of a condition', () => {
    const filter = [
      { key: 'monitored', type: 'equal' as const, value: [true] },
      { key: 'quality', type: 'equal' as const, value: ['2160p', '1080p'] },
    ]
    expect(matchesFilter(dune, filter, fields, now)).toBe(true)
    expect(matchesFilter(heat, filter, fields, now)).toBe(false)
  })

  it('excludes every value of a negative condition', () => {
    const filter = [{ key: 'quality', type: 'notEqual' as const, value: ['2160p', '720p'] }]
    expect(matchesFilter(dune, filter, fields, now)).toBe(false)
    expect(matchesFilter(heat, filter, fields, now)).toBe(true)
  })

  it('compares text without case, numbers by value and tags as a set', () => {
    expect(matchesFilter(dune, [{ key: 'title', type: 'startsWith', value: ['du'] }], fields, now)).toBe(true)
    expect(matchesFilter(heat, [{ key: 'year', type: 'lessThan', value: [2000] }], fields, now)).toBe(true)
    expect(matchesFilter(dune, [{ key: 'tags', type: 'contains', value: ['kids'] }], fields, now)).toBe(true)
    expect(matchesFilter(heat, [{ key: 'tags', type: 'notContains', value: ['kids'] }], fields, now)).toBe(true)
  })

  it('reads dates as before, after, or within the last days', () => {
    expect(matchesFilter(dune, [{ key: 'seen', type: 'inLast', value: [7] }], fields, now)).toBe(true)
    expect(matchesFilter(heat, [{ key: 'seen', type: 'inLast', value: [7] }], fields, now)).toBe(false)
    expect(matchesFilter(heat, [{ key: 'seen', type: 'lessThan', value: ['2026-06-01'] }], fields, now)).toBe(true)
    expect(matchesFilter(dune, [{ key: 'seen', type: 'greaterThan', value: ['2026-06-01'] }], fields, now)).toBe(true)
  })

  it('ignores a condition on a field the list no longer has', () => {
    expect(matchesFilter(heat, [{ key: 'gone', type: 'equal', value: ['x'] }], fields, now)).toBe(true)
  })

  it('offers the values the loaded items actually take', () => {
    expect(fieldChoices(fields[4], [dune, heat])).toEqual(['Kids', 'sci-fi'])
    expect(fieldChoices(fields[3], [dune, heat])).toEqual(['1080p', '2160p'])
  })
})
