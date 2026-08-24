/** Splitting a canonical quality into the two lines of the Format badge. */

export interface QualityParts {
  resolution?: string
  type: string
}

/**
 * Source words the badge drops.
 *
 * The badge answers "what kind of release is this", and the disc or scan the
 * bits came off does not change that answer — "2160p BluRay REMUX" and
 * "480i NTSC DVD REMUX" are both remuxes, and the resolution beside it already
 * distinguishes them.
 */
const SOURCE_WORDS = new Set(['bluray', 'dvd', 'ntsc', 'pal'])

/** Every Full Disc variant reads the same; the DVD5/DVD9 split is capacity. */
const FULL_DISC = 'Full Disc'

/** A leading resolution such as "2160p" or "576i". */
const LEADING_RESOLUTION = /^(\d{3,4}[pi])\s+(.*)$/

/**
 * Split a quality into the resolution and kind shown in the Format column.
 *
 * `parsedResolution` comes from the parser snapshot and takes precedence over
 * the canonical name, because some canonical names carry no resolution at all.
 * Full Disc is the case that matters: its quality definition has no resolution
 * component, so the resolution the release name stated was being dropped even
 * though the parser had read it.
 */
export function splitQuality(quality?: string, parsedResolution?: string): QualityParts {
  const name = (quality ?? '').trim()
  if (!name) return { type: parsedResolution ? '' : 'Unknown', resolution: parsedResolution }

  if (name.startsWith(FULL_DISC)) {
    return { resolution: parsedResolution, type: FULL_DISC }
  }

  const match = LEADING_RESOLUTION.exec(name)
  const resolution = parsedResolution || match?.[1]
  let rest = match ? match[2] : name

  // Filtering whole tokens rather than substrings: "DVD" must not be clipped
  // out of a word that merely contains it.
  rest = rest
    .split(/\s+/)
    .filter((token) => token && !SOURCE_WORDS.has(token.toLowerCase()))
    .join(' ')
    .trim()

  // A quality that is nothing but its source still needs a label.
  return { resolution, type: rest || match?.[2] || name }
}

/** Read the resolution the parser found, whatever the canonical name says. */
export function parsedResolution(parser: Record<string, unknown> | undefined): string | undefined {
  const quality = parser?.quality
  if (!quality || typeof quality !== 'object' || Array.isArray(quality)) return undefined
  const value = (quality as Record<string, unknown>).resolution
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}
