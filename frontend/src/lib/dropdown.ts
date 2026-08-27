

export function nextIndex(current: number, count: number, delta: number): number {
  if (count <= 0) return -1
  if (current < 0) return delta > 0 ? 0 : count - 1
  return (current + delta + count) % count
}

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

  const position = matches.findIndex((entry) => entry.index === current)
  return matches[(position + 1) % matches.length].index
}
