import { describe, expect, it } from 'vitest'
import { CONFIDENT, filterDiscoveries, laneFor, nextFocusId, reviewRoots, triage } from './reviewTriage'
import type { MediaDiscovery } from '../types'

const candidate = { tmdbId: 1, title: 'Dune', year: 2021 }

const item = (over: Partial<MediaDiscovery> = {}): MediaDiscovery => ({
  id: 'a', directoryId: 'd', path: '/movies/Dune 2021', rootName: 'Movies',
  exists: true, mediaType: 'movies', reviewState: 'pending', hdr: [], candidates: [],
  selected: candidate, confidence: 0.97, ...over,
} as MediaDiscovery)

describe('laneFor', () => {
  it('batches a confident match on a directory that exists', () => {
    expect(laneFor(item())).toBe('ready')
  })

  it('holds back an item with no identity chosen', () => {
    expect(laneFor(item({ selected: undefined }))).toBe('attention')
  })

  it('holds back a match the parser was unsure about', () => {
    expect(laneFor(item({ confidence: CONFIDENT - 0.01 }))).toBe('attention')
  })

  it('treats the threshold itself as confident', () => {
    expect(laneFor(item({ confidence: CONFIDENT }))).toBe('ready')
  })

  it('holds back a directory that is gone, however confident the match', () => {
    // Approving it would fail server-side, so it must never join the batch.
    expect(laneFor(item({ exists: false, confidence: 1 }))).toBe('attention')
  })

  it('accepts a selected identity that carries no confidence at all', () => {
    expect(laneFor(item({ confidence: undefined }))).toBe('ready')
  })
})

describe('triage', () => {
  it('splits a mixed queue and keeps order within each lane', () => {
    const result = triage([
      item({ id: '1' }),
      item({ id: '2', selected: undefined }),
      item({ id: '3' }),
    ])
    expect(result.ready.map((entry) => entry.id)).toEqual(['1', '3'])
    expect(result.attention.map((entry) => entry.id)).toEqual(['2'])
  })

  it('handles an empty queue', () => {
    expect(triage([])).toEqual({ ready: [], attention: [] })
  })
})

describe('filterDiscoveries', () => {
  const items = [
    item({ id: '1', mediaType: 'movies', rootName: 'Movies', parsedTitle: 'Dune', path: '/movies/Dune 2021' }),
    item({ id: '2', mediaType: 'shows', rootName: 'Shows', parsedTitle: 'Arcane', path: '/shows/Arcane', selected: undefined }),
    item({ id: '3', mediaType: 'movies', rootName: 'Cartoons', parsedTitle: 'Arrival', path: '/cartoons/Arrival 2016', selected: { tmdbId: 2, title: 'Arrival', year: 2016 } as MediaDiscovery['selected'] }),
  ]

  it('returns everything when nothing is filtered', () => {
    expect(filterDiscoveries(items, {}).length).toBe(3)
  })

  it('filters by media type', () => {
    expect(filterDiscoveries(items, { mediaType: 'shows' }).map((entry) => entry.id)).toEqual(['2'])
  })

  it('filters by root', () => {
    expect(filterDiscoveries(items, { rootName: 'Cartoons' }).map((entry) => entry.id)).toEqual(['3'])
  })

  it('searches the title, the path and the root name', () => {
    expect(filterDiscoveries(items, { query: 'arc' }).map((entry) => entry.id)).toEqual(['2'])
    expect(filterDiscoveries(items, { query: 'cartoons' }).map((entry) => entry.id)).toEqual(['3'])
  })

  it('ignores case and surrounding space in the query', () => {
    expect(filterDiscoveries(items, { query: '  ARRIVAL ' }).map((entry) => entry.id)).toEqual(['3'])
  })

  it('combines filters', () => {
    expect(filterDiscoveries(items, { mediaType: 'movies', query: 'dune' }).map((entry) => entry.id)).toEqual(['1'])
  })
})

describe('reviewRoots', () => {
  it('lists each root once, sorted, with its pending count', () => {
    const items = [
      item({ rootName: 'Shows', mediaType: 'shows' }),
      item({ rootName: 'Movies' }),
      item({ rootName: 'Shows', mediaType: 'shows' }),
    ]
    expect(reviewRoots(items)).toEqual([
      { name: 'Movies', mediaType: 'movies', count: 1 },
      { name: 'Shows', mediaType: 'shows', count: 2 },
    ])
  })

  it('ignores an item with no root', () => {
    expect(reviewRoots([item({ rootName: '' })])).toEqual([])
  })

  it('has nothing to show for an empty queue', () => {
    expect(reviewRoots([])).toEqual([])
  })
})

describe('nextFocusId', () => {
  const items = [item({ id: '1' }), item({ id: '2' }), item({ id: '3' })]

  it('moves to the following item', () => {
    expect(nextFocusId(items, '2')).toBe('3')
  })

  it('falls back to the previous item at the end of the list', () => {
    expect(nextFocusId(items, '3')).toBe('2')
  })

  it('starts at the top when the current item is unknown', () => {
    expect(nextFocusId(items, 'missing')).toBe('1')
  })

  it('returns nothing when the list is empty', () => {
    expect(nextFocusId([], 'anything')).toBe('')
  })
})
