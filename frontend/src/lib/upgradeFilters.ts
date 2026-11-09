// The filters beside the Upgrade Media list. Inside a section any picked option
// may match; a row must satisfy every section that has one picked.
import { splitQuality } from './quality'
import { swarmHealth } from './swarm'
import type { UpgradeCandidate, UpgradeMatchedFormat } from '../types'

const SECTIONS = [
  { key: 'improves', title: 'Improves' },
  { key: 'resolution', title: 'Resolution' },
  { key: 'source', title: 'Source' },
  { key: 'indexer', title: 'Indexer' },
  { key: 'seeds', title: 'Seeds' },
] as const

export type UpgradeFilterKey = typeof SECTIONS[number]['key']
export type UpgradeFilters = Partial<Record<UpgradeFilterKey, string[]>>
type Facets = Record<UpgradeFilterKey, string[]>

export interface UpgradeFilterSection {
  key: UpgradeFilterKey
  title: string
  options: Array<{ value: string; count: number; on: boolean }>
}

const RELEASE_GROUP = 'Release Group'
const OTHER_FORMATS = 'Other formats'
const NAMED_GROUPS = new Set(['Audio', 'Dynamic Range'])

const IMPROVES_ORDER = ['Quality', 'Audio', 'Dynamic Range', 'Repack/Proper', 'Season pack', OTHER_FORMATS]
const SOURCE_ORDER = ['REMUX', 'BluRay', 'WEB-DL', 'WEBRip']
const SEEDS_ORDER = ['Well seeded', 'Low seeds', 'Unknown']

/** What the candidate's Custom Formats improved, compared the way the score
 *  hover compares them: formats of one group fill the same part of a release,
 *  so a group is judged on its total. A better release group alone is not an
 *  upgrade, so it is never a reason here either. */
function improvedFormats(item: UpgradeCandidate): string[] {
  const change = new Map<string, number>()
  const add = (format: UpgradeMatchedFormat, sign: number) => {
    const key = format.group ? `group:${format.group}` : `format:${format.id}`
    change.set(key, (change.get(key) ?? 0) + sign * format.contribution)
  }
  item.currentMatchedFormats.forEach((format) => add(format, -1))
  item.candidateMatchedFormats.forEach((format) => add(format, 1))

  const improved = new Set<string>()
  for (const [key, delta] of change) {
    if (delta <= 0 || key === `group:${RELEASE_GROUP}`) continue
    const group = key.startsWith('group:') ? key.slice('group:'.length) : ''
    improved.add(NAMED_GROUPS.has(group) ? group : OTHER_FORMATS)
  }
  return [...improved]
}

function facetsOf(item: UpgradeCandidate): Facets {
  const types = item.upgradeTypes as string[]
  const { resolution, type } = splitQuality(item.quality)
  const health = swarmHealth(item.seeders)
  return {
    improves: [
      ...(types.includes('quality') ? ['Quality'] : []),
      ...(types.some((value) => value === 'repack' || value === 'proper' || value === 'revision') ? ['Repack/Proper'] : []),
      ...(types.includes('season_pack') ? ['Season pack'] : []),
      ...improvedFormats(item),
    ],
    resolution: resolution ? [resolution] : [],
    source: !item.quality ? [] : [/encode/i.test(type) ? 'Encode' : type],
    indexer: item.indexerName ? [item.indexerName] : [],
    seeds: [health === 'healthy' ? 'Well seeded' : health === 'unknown' ? 'Unknown' : 'Low seeds'],
  }
}

/** The picks that exist among these rows. A remembered pick this list has no
 *  row for has no button to undo it, so it must not empty the list. */
function activePicks(facets: Facets[], filters: UpgradeFilters): Facets {
  const picks = {} as Facets
  for (const { key } of SECTIONS) {
    const available = new Set(facets.flatMap((row) => row[key]))
    picks[key] = (filters[key] ?? []).filter((value) => available.has(value))
  }
  return picks
}

function matches(row: Facets, picks: Facets, ignoring?: UpgradeFilterKey) {
  return SECTIONS.every(({ key }) => key === ignoring || !picks[key].length || row[key].some((value) => picks[key].includes(value)))
}

export function filterUpgrades(items: UpgradeCandidate[], filters: UpgradeFilters): UpgradeCandidate[] {
  const facets = items.map(facetsOf)
  const picks = activePicks(facets, filters)
  return items.filter((_, index) => matches(facets[index], picks))
}

function orderOptions(key: UpgradeFilterKey, values: string[]) {
  const ranked = (order: string[]) => (value: string) => {
    const index = order.indexOf(value)
    return index < 0 ? order.length : index
  }
  const rank = key === 'improves' ? ranked(IMPROVES_ORDER)
    : key === 'source' ? ranked(SOURCE_ORDER)
      : key === 'seeds' ? ranked(SEEDS_ORDER)
        : key === 'resolution' ? (value: string) => -Number.parseInt(value, 10) || 0
          : () => 0
  return [...values].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
}

/** The rail's sections. An option's count is what picking it would show, so it
 *  respects the other sections but not its own, whose picks only add rows. */
export function upgradeFilterSections(items: UpgradeCandidate[], filters: UpgradeFilters): UpgradeFilterSection[] {
  const facets = items.map(facetsOf)
  const picks = activePicks(facets, filters)
  return SECTIONS.flatMap(({ key, title }) => {
    const counts = new Map<string, number>(facets.flatMap((row) => row[key]).map((value) => [value, 0]))
    for (const row of facets) {
      if (!matches(row, picks, key)) continue
      for (const value of row[key]) counts.set(value, (counts.get(value) ?? 0) + 1)
    }
    if (!counts.size) return []
    return [{
      key,
      title,
      options: orderOptions(key, [...counts.keys()]).map((value) => ({
        value,
        count: counts.get(value) ?? 0,
        on: picks[key].includes(value),
      })),
    }]
  })
}

const STORAGE_KEY = 'upgrade-media-filters'

export function loadUpgradeFilters(): UpgradeFilters {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    if (!stored || typeof stored !== 'object') return {}
    const filters: UpgradeFilters = {}
    for (const { key } of SECTIONS) {
      const value = (stored as Record<string, unknown>)[key]
      if (Array.isArray(value)) filters[key] = value.filter((entry): entry is string => typeof entry === 'string')
    }
    return filters
  } catch {
    return {}
  }
}

export function saveUpgradeFilters(filters: UpgradeFilters) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filters))
  } catch {
    // Remembering the filters is a convenience; the page works without it.
  }
}
