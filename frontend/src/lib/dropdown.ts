/**
 * Keyboard behaviour for a listbox, kept out of the component.
 *
 * A custom dropdown only earns its place if it behaves like the native one it
 * replaces, and that behaviour is all edge cases: wrapping at the ends, opening
 * on the current value rather than the top, and type-ahead that finds the right
 * option when several share a first letter. Those are worth testing directly.
 */

/** The index to move to, wrapping at both ends. */
export function nextIndex(current: number, count: number, delta: number): number {
  if (count <= 0) return -1
  if (current < 0) return delta > 0 ? 0 : count - 1
  return (current + delta + count) % count
}

/**
 * The option a typed prefix selects.
 *
 * Repeating one letter cycles through the options starting with it, which is
 * what a native select does and what people expect when they tap "d" twice.
 * Anything longer is treated as a prefix search from the top.
 */
export function typeAheadIndex(labels: readonly string[], query: string, current: number): number {
  if (!query) return -1
  const needle = query.toLowerCase()
  const repeated = needle.length > 1 && needle.split('').every((character) => character === needle[0])
  const prefix = repeated ? needle[0] : needle

  const matches = labels
    .map((label, index) => ({ label: label.toLowerCase(), index }))
    .filter((entry) => entry.label.startsWith(prefix))
  if (!matches.length) return -1
  if (!repeated) return matches[0].index

  // Cycling: step to the match after the current one.
  const position = matches.findIndex((entry) => entry.index === current)
  return matches[(position + 1) % matches.length].index
}
