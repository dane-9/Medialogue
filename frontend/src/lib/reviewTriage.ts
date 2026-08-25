import type { MediaDiscovery } from '../types'

/**
 * A confidence at or above this reads as "the parser and TMDB agree".
 *
 * The number matters less than having one: below it a match is a suggestion
 * worth a glance, at or above it the item belongs in the batch that gets
 * rubber-stamped. Items with no confidence at all were matched some other way
 * and are treated as ready if something is actually selected.
 */
export const CONFIDENT = 0.85

export type ReviewLane = 'ready' | 'attention'

/**
 * Which lane an item belongs in.
 *
 * The point of the split is that a review queue is mostly boring. Anything
 * with a selected identity, a directory still on disk, and a confident match
 * needs one click for the whole batch; everything else is why the page exists.
 */
export function laneFor(item: MediaDiscovery): ReviewLane {
  if (!item.exists) return 'attention'
  if (!item.selected) return 'attention'
  if (item.confidence !== undefined && item.confidence < CONFIDENT) return 'attention'
  return 'ready'
}

export interface ReviewFilters {
  mediaType?: 'movies' | 'shows'
  rootName?: string
  query?: string
}

/** Case-insensitive match against the title, folder path and root name. */
function matchesQuery(item: MediaDiscovery, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  const haystacks = [item.selected?.title, item.parsedTitle, item.path, item.rootName]
  return haystacks.some((value) => (value ?? '').toLowerCase().includes(needle))
}

export function filterDiscoveries(items: readonly MediaDiscovery[], filters: ReviewFilters): MediaDiscovery[] {
  return items.filter((item) => {
    if (filters.mediaType && item.mediaType !== filters.mediaType) return false
    if (filters.rootName && item.rootName !== filters.rootName) return false
    return matchesQuery(item, filters.query ?? '')
  })
}

export interface Triage {
  ready: MediaDiscovery[]
  attention: MediaDiscovery[]
}

/** Split a filtered queue into the batch lane and the one needing a person. */
export function triage(items: readonly MediaDiscovery[]): Triage {
  const result: Triage = { ready: [], attention: [] }
  for (const item of items) result[laneFor(item)].push(item)
  return result
}

export interface ReviewRoot {
  name: string
  mediaType: 'movies' | 'shows'
  count: number
}

/**
 * The roots with something waiting, for the root tabs.
 *
 * Counts come from the queue itself rather than a second request: a tab that
 * says 12 while the list below shows 11 is worse than no count at all.
 */
export function reviewRoots(items: readonly MediaDiscovery[]): ReviewRoot[] {
  const roots = new Map<string, ReviewRoot>()
  for (const item of items) {
    if (!item.rootName) continue
    const existing = roots.get(item.rootName)
    if (existing) existing.count += 1
    else roots.set(item.rootName, { name: item.rootName, mediaType: item.mediaType, count: 1 })
  }
  return [...roots.values()].sort((a, b) => a.name.localeCompare(b.name))
}

/**
 * The id to focus after acting on one.
 *
 * Keyboard review falls apart if the cursor jumps to the top every time an
 * item leaves the list, so it moves to the next sibling, or the previous one
 * when the last item was just handled.
 */
export function nextFocusId(items: readonly MediaDiscovery[], currentId: string): string {
  const index = items.findIndex((item) => item.id === currentId)
  if (index < 0) return items[0]?.id ?? ''
  return items[index + 1]?.id ?? items[index - 1]?.id ?? ''
}
