import type { MediaDiscovery } from '../types'

export const CONFIDENT = 0.85

type NewMediaLane = 'ready' | 'attention'

export function laneFor(item: MediaDiscovery): NewMediaLane {
  if (!item.exists) return 'attention'
  if (!item.selected) return 'attention'
  if (item.confidence !== undefined && item.confidence < CONFIDENT) return 'attention'
  return 'ready'
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
