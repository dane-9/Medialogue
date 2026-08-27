

export interface Rankable {
  title: string
  year?: number
  present: boolean

  index: number

  imdbVotes?: number

  voteCount?: number
}

function normalize(value: string): string {
  return value
    .toLowerCase()
    .replace(/[\u2018\u2019']/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

const words = (value: string) => normalize(value).split(' ').filter(Boolean)

export function titleScore(query: string, title: string): number {
  const q = normalize(query)
  const t = normalize(title)
  if (!q || !t) return 0
  if (t === q) return 1
  if (t.startsWith(q + ' ')) return 0.85

  const queryWords = words(query)
  const titleWords = words(title)
  if (!queryWords.length) return 0

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

export function reachScore(imdbVotes?: number, tmdbVotes?: number): number {
  const imdb = Math.max(imdbVotes ?? 0, 0)
  if (imdb) return Math.min(Math.log10(imdb + 1) / 6, 1)

  const tmdb = Math.max(tmdbVotes ?? 0, 0)
  if (!tmdb) return 0
  return Math.min(Math.log10(tmdb + 1) / 4.2, 1)
}

export function rankScore(item: Rankable, query: string): number {
  const title = titleScore(query, item.title)
  const reach = reachScore(item.imdbVotes, item.voteCount)
  const position = 1 / (1 + item.index)
  const dated = item.year ? 0.02 : 0
  return title * 1 + reach * 0.18 + position * 0.06 + dated
}

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
