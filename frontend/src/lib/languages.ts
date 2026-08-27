

export const UNKNOWN_LANGUAGE = 'Unknown'

export function releaseLanguages(parser: Record<string, unknown> | undefined): string[] {
  const raw = parser?.languages
  if (!Array.isArray(raw)) return []
  const seen = new Set<string>()
  const names: string[] = []
  for (const item of raw) {
    if (typeof item !== 'string') continue
    const name = item.trim()
    if (!name || seen.has(name.toLowerCase())) continue
    seen.add(name.toLowerCase())
    names.push(name)
  }
  return names
}

export function languageLabel(names: string[]): string | null {
  if (!names.length) return null
  return names.length === 1 ? names[0] : 'Multi-Language'
}
