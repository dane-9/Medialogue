/** Reading languages out of the parser snapshot, kept pure so it is testable. */

/** What the parser reports when a release name declares no language. */
export const UNKNOWN_LANGUAGE = 'Unknown'

/**
 * The languages a release name declared.
 *
 * The parser already returns display-ready names and guarantees at least one
 * entry ("Unknown"), matching Radarr. The snapshot itself is an untyped record
 * from the API, so every layer is checked rather than assumed.
 */
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

/**
 * The label for a release's language badge, as Radarr shows it.
 *
 * One language is named outright; several collapse to "Multi-Language" with the
 * full list left to a tooltip, because a row of five language names crowds out
 * the release name itself.
 */
export function languageLabel(names: string[]): string | null {
  if (!names.length) return null
  return names.length === 1 ? names[0] : 'Multi-Language'
}
