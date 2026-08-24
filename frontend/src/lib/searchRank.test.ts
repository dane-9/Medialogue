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
  it('rises with popularity but compresses the long tail', () => {
    const small = reachScore(5, 100)
    const big = reachScore(500, 100)
    expect(big).toBeGreaterThan(small)
    // 100x the popularity must not be 100x the score.
    expect(big).toBeLessThan(small * 4)
  })

  it('holds back a popularity figure backed by almost no votes', () => {
    expect(reachScore(100, 1)).toBeLessThan(reachScore(100, 500))
  })

  it('is zero for a result with no popularity at all', () => {
    expect(reachScore(undefined, undefined)).toBe(0)
  })
})

describe('rankResults', () => {
  it('puts a strong show match above a weak movie match', () => {
    // The failure of the old alternating merge: type decided the order.
    const list = [
      item({ title: 'The Thing About Pam', index: 0 }),
      item({ title: 'The Thing', index: 4 }),
    ]
    expect(titles(rankResults(list, 'the thing'))).toEqual(['The Thing', 'The Thing About Pam'])
  })

  it('separates equal titles by popularity', () => {
    const list = [
      item({ title: 'The Thing', index: 0, popularity: 1, voteCount: 8 }),
      item({ title: 'The Thing', index: 3, popularity: 90, voteCount: 900 }),
    ]
    expect(rankResults(list, 'the thing')[0].popularity).toBe(90)
  })

  it('never lets popularity lift a weak match over a strong one', () => {
    const list = [
      item({ title: 'Thingamajig', index: 0, popularity: 9999, voteCount: 9999 }),
      item({ title: 'The Thing', index: 9, popularity: 0, voteCount: 0 }),
    ]
    expect(titles(rankResults(list, 'the thing'))[0]).toBe('The Thing')
  })

  it('pins library items above everything', () => {
    const list = [
      item({ title: 'The Thing', index: 0, popularity: 500, voteCount: 900 }),
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
    const weak = rankScore(item({ title: 'Thing Time', index: 0, popularity: 999, voteCount: 999 }), 'the thing')
    expect(strong).toBeGreaterThan(weak)
  })
})
