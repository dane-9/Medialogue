import { describe, expect, it } from 'vitest'
import { rankResults, rankScore, reachScore, titleScore, type Rankable } from './searchRank'

const item = (over: Partial<Rankable> & { title: string }): Rankable => ({
  present: false, index: 0, ...over,
})
const titles = (list: Rankable[]) => list.map((entry) => entry.title)

describe('titleScore', () => {
  it('ranks an exact title above a prefix above a scattered match', () => {
    const exact = titleScore('the thing', 'The Thing')
    const prefix = titleScore('the thing', 'The Thing About Pam')
    const scattered = titleScore('the thing', 'Thing of Beauty, The')
    expect(exact).toBeGreaterThan(prefix)
    expect(prefix).toBeGreaterThan(scattered)
  })

  it('ignores case and punctuation', () => {
    expect(titleScore("wall-e", 'WALL·E')).toBe(1)
    expect(titleScore('spider man', 'Spider-Man')).toBe(1)
  })

  it('scores an unrelated title zero', () => {
    expect(titleScore('the thing', 'Casablanca')).toBe(0)
  })

  it('gives partial credit when only some query words appear', () => {
    const some = titleScore('the dark knight', 'The Dark Tower')
    expect(some).toBeGreaterThan(0)
    expect(some).toBeLessThan(0.7)
  })
})

describe('reachScore', () => {
  it('rises with the rating count but compresses the long tail', () => {
    const small = reachScore(1_000)
    const big = reachScore(1_000_000)
    expect(big).toBeGreaterThan(small)
    // A thousand times the ratings must not be a thousand times the score.
    expect(big).toBeLessThan(small * 3)
  })

  it('reaches the top of the scale for a title everyone has rated', () => {
    expect(reachScore(1_000_000)).toBeCloseTo(1, 1)
    expect(reachScore(10_000_000)).toBe(1)
  })

  it('still scores a well-known title well above an obscure one', () => {
    expect(reachScore(10_000)).toBeGreaterThan(0.5)
    expect(reachScore(200)).toBeLessThan(reachScore(10_000))
  })

  it('is zero when IMDb reports no ratings', () => {
    expect(reachScore(undefined)).toBe(0)
    expect(reachScore(0)).toBe(0)
  })
})

describe('rankResults', () => {
  it('puts a strong show match above a weak movie match', () => {
    const list = [
      item({ title: 'The Thing About Pam', index: 0 }),
      item({ title: 'The Thing', index: 4 }),
    ]
    expect(titles(rankResults(list, 'the thing'))).toEqual(['The Thing', 'The Thing About Pam'])
  })

  it('separates equal titles by how many people rated them', () => {
    const list = [
      item({ title: 'The Thing', index: 0, imdbVotes: 400 }),
      item({ title: 'The Thing', index: 3, imdbVotes: 420_000 }),
    ]
    expect(rankResults(list, 'the thing')[0].imdbVotes).toBe(420_000)
  })

  it('never lets rating count lift a weak match over a strong one', () => {
    const list = [
      item({ title: 'Thingamajig', index: 0, imdbVotes: 5_000_000 }),
      item({ title: 'The Thing', index: 9, imdbVotes: 0 }),
    ]
    expect(titles(rankResults(list, 'the thing'))[0]).toBe('The Thing')
  })

  it('pins library items above everything', () => {
    const list = [
      item({ title: 'The Thing', index: 0, imdbVotes: 500_000 }),
      item({ title: 'Some Unrelated Film', present: true, index: 7 }),
    ]
    expect(titles(rankResults(list, 'the thing'))[0]).toBe('Some Unrelated Film')
  })

  it('orders deterministically when everything ties', () => {
    const list = [
      item({ title: 'Alpha', index: 1 }),
      item({ title: 'Alpha', index: 0 }),
    ]
    const once = rankResults(list, 'alpha')
    const twice = rankResults([...list].reverse(), 'alpha')
    expect(once.map((entry) => entry.index)).toEqual(twice.map((entry) => entry.index))
  })

  it('does not mutate the list it was given', () => {
    const list = [item({ title: 'B', index: 0 }), item({ title: 'A', index: 1 })]
    rankResults(list, 'a')
    expect(titles(list)).toEqual(['B', 'A'])
  })

  it('prefers a dated result over an undated one that is otherwise equal', () => {
    const list = [
      item({ title: 'The Thing', index: 0 }),
      item({ title: 'The Thing', index: 0, year: 1982 }),
    ]
    expect(rankResults(list, 'the thing')[0].year).toBe(1982)
  })
})

describe('rankScore', () => {
  it('is dominated by the title match', () => {
    const strong = rankScore(item({ title: 'The Thing', index: 20 }), 'the thing')
    const weak = rankScore(item({ title: 'Thing Time', index: 0, imdbVotes: 2_000_000 }), 'the thing')
    expect(strong).toBeGreaterThan(weak)
  })
})

describe('the TMDB fallback', () => {
  it('uses the TMDB rating count when IMDb has nothing', () => {
    expect(reachScore(undefined, 5_000)).toBeGreaterThan(0)
  })

  it('prefers IMDb when both are present', () => {
    // Same call, two sources: the IMDb figure must decide it.
    const imdbLed = reachScore(1_000_000, 1)
    const tmdbOnly = reachScore(undefined, 1)
    expect(imdbLed).toBeGreaterThan(tmdbOnly)
  })

  it('puts the two scales on comparable footing', () => {
    const tmdbKnown = reachScore(undefined, 20_000)
    const imdbKnown = reachScore(20_000)
    expect(Math.abs(tmdbKnown - imdbKnown)).toBeLessThan(0.35)
  })

  it('scores zero only when neither source knows anything', () => {
    expect(reachScore(undefined, undefined)).toBe(0)
    expect(reachScore(0, 0)).toBe(0)
  })

  it('still cannot lift a weak title match', () => {
    const list = [
      item({ title: 'Thingamajig', index: 0, voteCount: 90_000 }),
      item({ title: 'The Thing', index: 9 }),
    ]
    expect(titles(rankResults(list, 'the thing'))[0]).toBe('The Thing')
  })
})
