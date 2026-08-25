/** Default naming for Media Folders, kept pure so the numbering is testable. */

/** The two names a root is offered automatically. */
export type RootNameBase = 'Movies' | 'Shows'

/** "Movies", "Shows", "Movies 2" …, anything this file would have produced. */
const GENERATED = /^(Movies|Shows)(\s+\d+)?$/i

/**
 * Whether a name is still one Medialogue chose, rather than one the operator did.
 *
 * An empty box counts: it is an absent name, not a chosen one, so it may be
 * filled in. Anything else is left alone, switching library is not permission
 * to rewrite a name someone typed.
 */
export function isGeneratedRootName(value: string): boolean {
  const trimmed = value.trim()
  return trimmed === '' || GENERATED.test(trimmed)
}

/**
 * The first free name in the "Movies", "Movies 2", "Movies 3" … sequence.
 *
 * Root names are unique, so offering one that is already taken would only
 * produce a save error the operator has to decipher.
 */
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
