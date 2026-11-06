/**
 * Keeps the previous object for every item whose content did not change.
 *
 * The library pages reload the whole list on every live event and poll. A
 * fresh array of fresh objects would re-render every card; reusing the old
 * object for an unchanged item lets memoised rows skip their render entirely.
 */
export function reuseUnchanged<T extends { id: string }>(previous: T[], next: T[]): T[] {
  if (!previous.length) return next
  const before = new Map(previous.map((item) => [item.id, item]))
  let changed = previous.length !== next.length
  const merged = next.map((item, index) => {
    const old = before.get(item.id)
    if (old && JSON.stringify(old) === JSON.stringify(item)) {
      if (previous[index] !== old) changed = true
      return old
    }
    changed = true
    return item
  })
  return changed ? merged : previous
}
