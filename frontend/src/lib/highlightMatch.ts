export type HighlightedMatch = { before: string; hit: string; after: string }

/** Split `subject` around the first occurrence of `hit`.
 *
 * `hit` comes from the evaluator as the exact text the regex consumed out of
 * `subject`, so a plain index lookup is exact rather than a fuzzy search. The
 * evaluator reports no position, so a pattern that matched a later of several
 * identical runs highlights the first one; they read the same either way.
 */
export function highlightMatch(subject: string, hit?: string | null): HighlightedMatch | null {
  if (!subject || !hit) return null
  const at = subject.indexOf(hit)
  if (at < 0) return null
  return {
    before: subject.slice(0, at),
    hit: subject.slice(at, at + hit.length),
    after: subject.slice(at + hit.length),
  }
}
