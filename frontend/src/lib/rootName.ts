

export type RootNameBase = 'Movies' | 'Shows'

const GENERATED = /^(Movies|Shows)(\s+\d+)?$/i

export function isGeneratedRootName(value: string): boolean {
  const trimmed = value.trim()
  return trimmed === '' || GENERATED.test(trimmed)
}

export function nextRootName(base: RootNameBase, existing: Iterable<string>): string {
  const taken = new Set<string>()
  for (const name of existing) taken.add(name.trim().toLowerCase())
  if (!taken.has(base.toLowerCase())) return base
  for (let suffix = 2; suffix < 1000; suffix += 1) {
    const candidate = `${base} ${suffix}`
    if (!taken.has(candidate.toLowerCase())) return candidate
  }
  return base
}
