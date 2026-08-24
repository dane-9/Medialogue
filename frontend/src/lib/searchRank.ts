/** Ranking movies and shows into one list, kept pure so its tuning is testable. */

export interface Rankable {
  title: string
  year?: number
  present: boolean
  /** Position in the list its own type came back in, 0 first. */
  index: number
  /** How many people have rated it on IMDb. */
  imdbVotes?: number
  /** TMDB's rating count, the fallback where IMDb has nothing. */
  voteCount?: number
}

/** Strip case, punctuation and articles so "The Thing" matches "the thing". */
function normalize(value: string): string {
  return value
    .toLowerCase()
    .replace(/[\u2018\u2019']/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

const words = (value: string) => normalize(value).split(' ').filter(Boolean)

/**
 * How well a title answers the query, from 0 to 1.
 *
 * This is the only signal that is genuinely comparable between a movie and a
 * show, because it is computed the same way for both. TMDB's own result
 * position is not: it ranks within a type and says nothing across types.
 */
export function titleScore(query: string, title: string): number {
  const q = normalize(query)
  const t = normalize(title)
  if (!q || !t) return 0
  if (t === q) return 1
  if (t.startsWith(q + ' ')) return 0.85

  const queryWords = words(query)
  const titleWords = words(title)
  if (!queryWords.length) return 0

  // Every query word present as a whole word, in order.
  let cursor = 0
  let ordered = true
  for (const word of queryWords) {
    const found = titleWords.indexOf(word, cursor)
    if (found < 0) { ordered = false; break }
    cursor = found + 1
  }
  if (ordered) return 0.7

  const present = queryWords.filter((word) => titleWords.includes(word)).length
  if (present === queryWords.length) return 0.55
  if (present > 0) return 0.35 * (present / queryWords.length)
  return t.includes(q) ? 0.2 : 0
}

/**
 * How widely known a title is, from its IMDb rating count, compressed to 0..1.
 *
 * The count of ratings is a better signal than a rating or a popularity score:
 * a score says how much people liked it, and TMDB popularity is a trending
 * figure that moves week to week. How many people have bothered to rate it is
 * a stable measure of how well known it is, which is what tells the famous
 * title apart from the obscure one sharing its name.
 *
 * The scale is extreme, a blockbuster has millions of ratings, a small film a
 * few hundred, so it is compressed logarithmically rather than scaled. A
 * million ratings reaches 1; ten thousand still scores well over half.
 */
export function reachScore(imdbVotes?: number, tmdbVotes?: number): number {
  const imdb = Math.max(imdbVotes ?? 0, 0)
  if (imdb) return Math.min(Math.log10(imdb + 1) / 6, 1)

  // TMDB's counts run roughly two decades smaller than IMDb's, tens of
  // thousands where IMDb has millions, so they get a shorter scale. Without
  // it, a well-known title with no IMDb data would score as though nobody had
  // heard of it, which is worse than no fallback at all.
  const tmdb = Math.max(tmdbVotes ?? 0, 0)
  if (!tmdb) return 0
  return Math.min(Math.log10(tmdb + 1) / 4.2, 1)
}

/**
 * Total desirability of one result.
 *
 * The title score dominates deliberately. Popularity separates two titles that
 * match equally well, the real film from an obscure short of the same name,
 * but must never lift a weak match above a strong one, which is the failure the
 * old alternating merge had.
 */
export function rankScore(item: Rankable, query: string): number {
  const title = titleScore(query, item.title)
  const reach = reachScore(item.imdbVotes, item.voteCount)
  // Own-list position is a weak tiebreaker; it is only meaningful within a type.
  const position = 1 / (1 + item.index)
  const dated = item.year ? 0.02 : 0
  return title * 1 + reach * 0.18 + position * 0.06 + dated
}

/**
 * Order a mixed list of movies and shows.
 *
 * Library items come first regardless of score: that you already have it is a
 * stronger signal than any ranking. Ties fall back to title then index so the
 * order never flickers between identical searches.
 */
export function rankResults<T extends Rankable>(items: T[], query: string): T[] {
  const scored = items.map((item) => ({ item, score: rankScore(item, query) }))
  return scored
    .sort((left, right) => {
      if (left.item.present !== right.item.present) return left.item.present ? -1 : 1
      if (right.score !== left.score) return right.score - left.score
      const byTitle = left.item.title.localeCompare(right.item.title)
      return byTitle || left.item.index - right.item.index
    })
    .map((entry) => entry.item)
}
