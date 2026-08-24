/**
 * Moving one item of a list to another index.
 *
 * Splice-based reordering is easy to get subtly wrong: removing the item first
 * shifts every later index down by one, so a naive `splice(to, 0, item)` lands
 * one slot short whenever the item moves downwards. Doing the removal and the
 * insertion against the same already-shortened array is what makes that correct,
 * which is why this lives here with tests rather than inline in a drag handler.
 */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  if (from === to) return items.slice()
  if (from < 0 || from >= items.length) return items.slice()
  const next = items.slice()
  const [moved] = next.splice(from, 1)
  // Clamp after the removal: the valid insertion range is one shorter now.
  next.splice(Math.max(0, Math.min(next.length, to)), 0, moved)
  return next
}
