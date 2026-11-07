import { describe, expect, it } from 'vitest'
import { CONFIDENT, laneFor, newMediaRoots } from './newMediaTriage'
import type { MediaDiscovery } from '../types'

const candidate = { tmdbId: 1, title: 'Dune', year: 2021 }

const item = (over: Partial<MediaDiscovery> = {}): MediaDiscovery => ({
  id: 'a', directoryId: 'd', path: '/movies/Dune 2021', rootName: 'Movies',
  exists: true, mediaType: 'movies', newMediaState: 'pending', hdr: [], candidates: [],
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

describe('newMediaRoots', () => {
  it('lists each root once, sorted, with its pending count', () => {
    const items = [
      item({ rootName: 'Shows', mediaType: 'shows' }),
      item({ rootName: 'Movies' }),
      item({ rootName: 'Shows', mediaType: 'shows' }),
    ]
    expect(newMediaRoots(items)).toEqual([
      { name: 'Movies', mediaType: 'movies', count: 1 },
      { name: 'Shows', mediaType: 'shows', count: 2 },
    ])
  })

  it('ignores an item with no root', () => {
    expect(newMediaRoots([item({ rootName: '' })])).toEqual([])
  })

  it('has nothing to show for an empty queue', () => {
    expect(newMediaRoots([])).toEqual([])
  })
})
