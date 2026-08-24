/** Release list ordering, kept pure so its invariants are testable. */

import type { InteractiveSearchResult } from '../types'
import { ageValue } from './age'

/** 'rank' is the composite default; the rest sort by one column. */
export type SortKey = 'rank' | 'title' | 'quality' | 'score' | 'size' | 'seeders' | 'age'

/** Descending is the useful direction for every numeric column. */
export const DEFAULT_DESC: Record<SortKey, boolean> = {
  rank: true, title: false, quality: true, score: true, size: true, seeders: true,
  // Descending age means the newest first, which is the useful end.
  age: true,
}

/** The resting order, and the one the header cycle returns to. */
export const DEFAULT_SORT: SortKey = 'rank'

function value(item: InteractiveSearchResult, sort: SortKey): number {
  switch (sort) {
    case 'quality': return item.qualityPreference ?? 0
    case 'score': return item.customFormatScore ?? 0
    case 'size': return item.size ?? 0
    case 'seeders': return item.seeders ?? 0
    case 'age': return ageValue(item.publishedAt)
    default: return 0
  }
}

/**
 * Compare two releases by overall desirability.
 *
 * Quality outranks Custom Format score, and deliberately so: a profile places
 * 2160p Remux above 1080p WEB-DL because that is the better release, and no
 * amount of format score should let the lesser quality claim the top of the
 * list. Score only decides between releases of the same quality. This is the
 * order the server ranks by, and the order Radarr's DownloadDecisionComparer
 * uses.
 */
function compareRank(left: InteractiveSearchResult, right: InteractiveSearchResult): number {
  const quality = (right.qualityPreference ?? 0) - (left.qualityPreference ?? 0)
  if (quality) return quality
  const score = (right.customFormatScore ?? 0) - (left.customFormatScore ?? 0)
  if (score) return score
  return (right.seeders ?? 0) - (left.seeders ?? 0)
}

/**
 * Order releases for display.
 *
 * One rule outranks the chosen column: a release the profile blocks cannot be
 * downloaded, so it sinks to the bottom whatever you sort by — otherwise
 * sorting by score leads with an unusable release. Ties keep the order the
 * server sent.
 */
export function sortReleases(
  results: InteractiveSearchResult[],
  sort: SortKey,
  desc: boolean,
): InteractiveSearchResult[] {
  const direction = desc ? -1 : 1
  return [...results].sort((left, right) => {
    if (left.qualityAllowed !== right.qualityAllowed) return left.qualityAllowed ? -1 : 1
    if (sort === 'rank') return direction * compareRank(left, right) * -1
    if (sort === 'title') return direction * left.title.localeCompare(right.title)
    return direction * (value(left, sort) - value(right, sort))
  })
}
