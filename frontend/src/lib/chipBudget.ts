
export function fitChips(labels: readonly string[], maxItems = 9, maxChars = 96): number {
  if (!labels.length) return 0

  let used = 0
  for (let index = 0; index < labels.length && index < maxItems; index += 1) {
    // Every chip carries its own padding and score, so a name is never free.
    used += labels[index].length + 5
    if (used > maxChars && index > 0) return index
  }
  return Math.min(labels.length, maxItems)
}
