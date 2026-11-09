interface ReleaseToken {
  text: string
  /** The separator that precedes this token; empty for the first one. */
  sep: string
}

/** Split a release name into its words plus the trailing group.
 *
 * Dots and spaces separate, since trackers write names both ways, but hyphens
 * do not: tokens carry their own (`DTS-HD`, `WEB-DL`). The one exception is the
 * release group at the end: leaving `-GROUP` fused to the last token makes a
 * group change read as a change to whatever it is stuck to, so the final hyphen
 * is cut when the name ends in one.
 */
export function releaseTokens(name: string): ReleaseToken[] {
  if (!name.length) return []
  // Splitting on a captured group keeps the separators, at the odd positions.
  const parts = name.split(/([. ]+)/)
  const tokens: ReleaseToken[] = []
  for (let index = 0; index < parts.length; index += 2) tokens.push({ text: parts[index], sep: index ? parts[index - 1] : '' })
  const last = tokens[tokens.length - 1]
  const cut = last.text.lastIndexOf('-')
  if (cut > 0 && cut < last.text.length - 1) {
    const group = last.text.slice(cut + 1)
    last.text = last.text.slice(0, cut)
    tokens.push({ text: group, sep: '-' })
  }
  return tokens
}

export interface ReleaseTokenDiff extends ReleaseToken {
  /** True when this token has no counterpart in the release compared against. */
  changed: boolean
}

/** Mark the tokens of `candidate` that the `current` release does not have.
 *
 * A multiset, not a set: a token repeated twice in the candidate but once in
 * the current release leaves the second occurrence marked, which is what a
 * reader would call a difference. Comparison ignores case, because scene names
 * are inconsistent about it and a case flip is not an upgrade.
 */
export function diffReleaseTokens(candidate: string, current: string): ReleaseTokenDiff[] {
  const remaining = new Map<string, number>()
  for (const token of releaseTokens(current)) {
    const key = token.text.toLowerCase()
    remaining.set(key, (remaining.get(key) ?? 0) + 1)
  }
  return releaseTokens(candidate).map((token) => {
    const key = token.text.toLowerCase()
    const left = remaining.get(key) ?? 0
    if (left > 0) {
      remaining.set(key, left - 1)
      return { ...token, changed: false }
    }
    return { ...token, changed: true }
  })
}
