/**
 * A free name for a duplicate: "Bravia Core Copy", then "Bravia Core Copy 2".
 *
 * Custom Format names are unique, so handing the server one that is already
 * taken only produces a save error the operator has to decipher. Duplicating
 * the same format twice is the common case, not an edge case, which is why the
 * numbering matters.
 */
export function copyName(name: string, existing: Iterable<string>): string {
  const taken = new Set<string>()
  for (const item of existing) taken.add(item.trim().toLowerCase())

  const base = `${name.trim()} Copy`
  if (!taken.has(base.toLowerCase())) return base
  for (let suffix = 2; suffix < 1000; suffix += 1) {
    const candidate = `${base} ${suffix}`
    if (!taken.has(candidate.toLowerCase())) return candidate
  }
  return base
}
