/**
 * The card's one-line statement of what a file actually is: "2160p BluRay
 * Remux", "1080p WEB-DL".
 *
 * Quality names arrive in more than one shape, hyphenated like "Remux-2160p"
 * and spaced like "2160p BluRay REMUX", so tokens are read out of the string
 * rather than assuming a separator. Matching keywords rather than positions is
 * what keeps "WEB-DL" intact, since splitting on the hyphen would shred it.
 */

/** Where the file came from. First match wins, most specific first. */
const SOURCES: [RegExp, string][] = [
  [/\bfull[\s-]?disc\b/i, 'Full Disc'],
  [/\bweb[\s-]?dl\b/i, 'WEB-DL'],
  [/\bweb[\s-]?rip\b/i, 'WEBRip'],
  [/\bblu[\s-]?ray\b/i, 'BluRay'],
  [/\bhdtv\b/i, 'HDTV'],
  [/\bsdtv\b/i, 'SDTV'],
  [/\bdvd\b/i, 'DVD'],
]

export function qualityMark(quality?: string, edition?: string): string {
  if (!quality) return ''

  const tokens = quality.split(/[\s-]+/).map((part) => part.trim()).filter(Boolean)
  // Lower case throughout: "1080p" is how a resolution is written.
  const resolution = tokens.find((token) => /^\d{3,4}[pi]$/i.test(token))?.toLowerCase()
  const source = SOURCES.find(([pattern]) => pattern.test(quality))?.[1]

  // Remux is a property of the rip, not a source, so it sits alongside the disc
  // it came from rather than replacing it. An edition is more specific still
  // and takes the slot when both are present.
  const remux = /\bremux\b/i.test(quality) ? 'Remux' : undefined
  const modifier = edition?.split('·')[0].trim() || remux

  return [resolution, source, modifier].filter(Boolean).join(' ')
}
