

import type { InteractiveSearchResult } from '../types'
import { ageValue } from './age'

export type SortKey = 'rank' | 'title' | 'quality' | 'score' | 'size' | 'seeders' | 'age'

export const DEFAULT_DESC: Record<SortKey, boolean> = {
  rank: true, title: false, quality: true, score: true, size: true, seeders: true,
  age: true,
}

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

function compareRank(left: InteractiveSearchResult, right: InteractiveSearchResult): number {
  const quality = (right.qualityPreference ?? 0) - (left.qualityPreference ?? 0)
  if (quality) return quality
  const score = (right.customFormatScore ?? 0) - (left.customFormatScore ?? 0)
  if (score) return score
  return (right.seeders ?? 0) - (left.seeders ?? 0)
}

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
