/** Ranking movies and shows into one list, kept pure so its tuning is testable. */

export interface Rankable {
  title: string
  year?: number
  present: boolean
  /** Position in the list its own type came back in, 0 first. */
  index: number
  popularity?: number
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
 * Popularity, compressed to 0..1.
 *
 * TMDB popularity is unbounded and long-tailed — a blockbuster can be hundreds
 * of times a small title's value — so it is squashed rather than scaled, and a
 * result with few votes is held back because its popularity is noise.
 */
export function reachScore(popularity?: number, voteCount?: number): number {
  const raw = Math.max(popularity ?? 0, 0)
  const compressed = Math.log10(raw + 1) / 3
  const confidence = Math.min((voteCount ?? 0) / 50, 1)
  return Math.min(compressed, 1) * (0.4 + 0.6 * confidence)
}

/**
 * Total desirability of one result.
 *
 * The title score dominates deliberately. Popularity separates two titles that
 * match equally well — the real film from an obscure short of the same name —
 * but must never lift a weak match above a strong one, which is the failure the
 * old alternating merge had.
 */
export function rankScore(item: Rankable, query: string): number {
  const title = titleScore(query, item.title)
  const reach = reachScore(item.popularity, item.voteCount)
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
