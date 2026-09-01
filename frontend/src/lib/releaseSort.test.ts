import { describe, expect, it } from 'vitest'
import type { InteractiveSearchResult } from '../types'
import { DEFAULT_DESC, DEFAULT_SORT, sortReleases } from './releaseSort'

const release = (over: Partial<InteractiveSearchResult> & { id: string }): InteractiveSearchResult => ({
  title: over.id,
  qualityAllowed: true,
  qualityPreference: 0,
  customFormatScore: 0,
  customFormatSnapshot: {},
  indexerName: 'indexer',
  mediaType: 'movies',
  targetEntityType: 'movie',
  jobId: 'job',
  ...over,
} as InteractiveSearchResult)

const ids = (list: InteractiveSearchResult[]) => list.map((item) => item.id)

const asOpened = (list: InteractiveSearchResult[]) =>
  sortReleases(list, DEFAULT_SORT, DEFAULT_DESC[DEFAULT_SORT])

const asRanked = (list: InteractiveSearchResult[]) =>
  sortReleases(list, 'rank', DEFAULT_DESC.rank)

const REMUX = 10
const WEBDL = 4

describe('quality outranks Custom Format score', () => {
  it('never lets a lower quality outrank a higher one, however it scores', () => {
    const list = [
      release({ id: 'webdl', qualityPreference: WEBDL, customFormatScore: 9999 }),
      release({ id: 'remux', qualityPreference: REMUX, customFormatScore: -50 }),
    ]
    expect(ids(asRanked(list))).toEqual(['remux', 'webdl'])
  })

  it('holds no matter how large the score gap is', () => {
    const list = [
      release({ id: 'webdl', qualityPreference: WEBDL, customFormatScore: 1_000_000 }),
      release({ id: 'remux', qualityPreference: REMUX, customFormatScore: -1_000_000 }),
    ]
    expect(ids(asRanked(list))[0]).toBe('remux')
  })

  it('uses score to decide between releases of the same quality', () => {
    const list = [
      release({ id: 'plain', qualityPreference: REMUX, customFormatScore: 10 }),
      release({ id: 'best', qualityPreference: REMUX, customFormatScore: 500 }),
      release({ id: 'mid', qualityPreference: REMUX, customFormatScore: 120 }),
    ]
    expect(ids(asRanked(list))).toEqual(['best', 'mid', 'plain'])
  })

  it('uses seeders only when quality and score both tie', () => {
    const list = [
      release({ id: 'thin', qualityPreference: REMUX, customFormatScore: 100, seeders: 2 }),
      release({ id: 'healthy', qualityPreference: REMUX, customFormatScore: 100, seeders: 200 }),
    ]
    expect(ids(asRanked(list))).toEqual(['healthy', 'thin'])
  })

  it('orders a full mixed list by quality tier first', () => {
    const list = [
      release({ id: 'webdl-great', qualityPreference: WEBDL, customFormatScore: 900 }),
      release({ id: 'remux-poor', qualityPreference: REMUX, customFormatScore: 0 }),
      release({ id: 'webdl-poor', qualityPreference: WEBDL, customFormatScore: 5 }),
      release({ id: 'remux-great', qualityPreference: REMUX, customFormatScore: 400 }),
    ]
    expect(ids(asRanked(list))).toEqual(['remux-great', 'remux-poor', 'webdl-great', 'webdl-poor'])
  })
})

describe('default interactive-search order', () => {
  it('opens with the highest Custom Format score first regardless of quality tier', () => {
    const list = [
      release({ id: 'remux', qualityPreference: REMUX, customFormatScore: 10 }),
      release({ id: 'webdl', qualityPreference: WEBDL, customFormatScore: 900 }),
    ]
    expect(DEFAULT_SORT).toBe('score')
    expect(ids(asOpened(list))).toEqual(['webdl', 'remux'])
  })
})

describe('blocked releases', () => {
  it('sinks score-rejected releases without removing them', () => {
    const list = [
      release({ id: 'rejected', qualityPreference: REMUX, customFormatScore: 9999, minimumCustomFormatScoreMet: false }),
      release({ id: 'accepted', qualityPreference: WEBDL, customFormatScore: 1, minimumCustomFormatScoreMet: true }),
    ]
    expect(ids(asOpened(list))).toEqual(['accepted', 'rejected'])
  })

  it('sink below every allowed release even with the best quality and score', () => {
    const list = [
      release({ id: 'blocked', qualityPreference: REMUX, customFormatScore: 9999, qualityAllowed: false }),
      release({ id: 'allowed', qualityPreference: WEBDL, customFormatScore: 1 }),
    ]
    expect(ids(asOpened(list))).toEqual(['allowed', 'blocked'])
  })

  it('stay at the bottom in every column and direction', () => {
    const list = [
      release({ id: 'blocked', qualityPreference: REMUX, customFormatScore: 9999, size: 9e12, seeders: 9999, qualityAllowed: false }),
      release({ id: 'allowed', qualityPreference: WEBDL, customFormatScore: 1, size: 1, seeders: 1 }),
    ]
    for (const key of ['rank', 'score', 'size', 'seeders', 'quality', 'age', 'title'] as const) {
      for (const desc of [true, false]) {
        expect(ids(sortReleases(list, key, desc))).toEqual(['allowed', 'blocked'])
      }
    }
  })
})

describe('explicit column sorts', () => {
  it('sorts by score alone when the score column is chosen', () => {
    const list = [
      release({ id: 'remux', qualityPreference: REMUX, customFormatScore: 10 }),
      release({ id: 'webdl', qualityPreference: WEBDL, customFormatScore: 900 }),
    ]
    expect(ids(sortReleases(list, 'score', true))).toEqual(['webdl', 'remux'])
  })

  it('sorts largest first by size, smallest first when reversed', () => {
    const list = [release({ id: 'small', size: 1 }), release({ id: 'big', size: 100 })]
    expect(ids(sortReleases(list, 'size', true))).toEqual(['big', 'small'])
    expect(ids(sortReleases(list, 'size', false))).toEqual(['small', 'big'])
  })

  it('sorts newest first by age, with unknown dates last', () => {
    const list = [
      release({ id: 'old', publishedAt: '2020-01-01T00:00:00Z' }),
      release({ id: 'undated' }),
      release({ id: 'new', publishedAt: '2026-01-01T00:00:00Z' }),
    ]
    expect(ids(sortReleases(list, 'age', true))).toEqual(['new', 'old', 'undated'])
  })

  it('sorts titles alphabetically rather than by magnitude', () => {
    const list = [release({ id: 'c', title: 'Charlie' }), release({ id: 'a', title: 'Alpha' })]
    expect(ids(sortReleases(list, 'title', false))).toEqual(['a', 'c'])
  })

  it('reverses the ranking when descending is turned off', () => {
    const list = [
      release({ id: 'remux', qualityPreference: REMUX }),
      release({ id: 'webdl', qualityPreference: WEBDL }),
    ]
    expect(ids(sortReleases(list, 'rank', false))).toEqual(['webdl', 'remux'])
  })
})

describe('sortReleases', () => {
  it('does not mutate the list it was given', () => {
    const list = [release({ id: 'a', customFormatScore: 1 }), release({ id: 'b', customFormatScore: 2 })]
    sortReleases(list, 'rank', true)
    expect(ids(list)).toEqual(['a', 'b'])
  })

  it('keeps the server order for releases that tie completely', () => {
    const list = [
      release({ id: 'first', qualityPreference: REMUX, customFormatScore: 100, seeders: 5 }),
      release({ id: 'second', qualityPreference: REMUX, customFormatScore: 100, seeders: 5 }),
    ]
    expect(ids(asOpened(list))).toEqual(['first', 'second'])
  })
})
