import type { MediaDiscovery } from '../types'

export const CONFIDENT = 0.85

type NewMediaLane = 'ready' | 'attention'

export function laneFor(item: MediaDiscovery): NewMediaLane {
  if (!item.exists) return 'attention'
  if (!item.selected) return 'attention'
  if (item.confidence !== undefined && item.confidence < CONFIDENT) return 'attention'
  return 'ready'
}

interface NewMediaFilters {
  mediaType?: 'movies' | 'shows'
  rootName?: string
  query?: string
}

function matchesQuery(item: MediaDiscovery, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  const haystacks = [item.selected?.title, item.parsedTitle, item.path, item.rootName]
  return haystacks.some((value) => (value ?? '').toLowerCase().includes(needle))
}

export function filterDiscoveries(items: readonly MediaDiscovery[], filters: NewMediaFilters): MediaDiscovery[] {
  return items.filter((item) => {
    if (filters.mediaType && item.mediaType !== filters.mediaType) return false
    if (filters.rootName && item.rootName !== filters.rootName) return false
    return matchesQuery(item, filters.query ?? '')
  })
}

interface Triage {
  ready: MediaDiscovery[]
  attention: MediaDiscovery[]
}

export function triage(items: readonly MediaDiscovery[]): Triage {
  const result: Triage = { ready: [], attention: [] }
  for (const item of items) result[laneFor(item)].push(item)
  return result
}

interface NewMediaRoot {
  name: string
  mediaType: 'movies' | 'shows'
  count: number
}

export function newMediaRoots(items: readonly MediaDiscovery[]): NewMediaRoot[] {
  const roots = new Map<string, NewMediaRoot>()
  for (const item of items) {
    if (!item.rootName) continue
    const existing = roots.get(item.rootName)
    if (existing) existing.count += 1
    else roots.set(item.rootName, { name: item.rootName, mediaType: item.mediaType, count: 1 })
  }
  return [...roots.values()].sort((a, b) => a.name.localeCompare(b.name))
}

export function nextFocusId(items: readonly MediaDiscovery[], currentId: string): string {
  const index = items.findIndex((item) => item.id === currentId)
  if (index < 0) return items[0]?.id ?? ''
  return items[index + 1]?.id ?? items[index - 1]?.id ?? ''
}
