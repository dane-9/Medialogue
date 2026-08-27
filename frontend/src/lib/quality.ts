

export interface QualityParts {
  resolution?: string
  type: string
}

const SOURCE_WORDS = new Set(['bluray', 'dvd', 'ntsc', 'pal'])

const FULL_DISC = 'Full Disc'

const LEADING_RESOLUTION = /^(\d{3,4}[pi])\s+(.*)$/

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

  return { resolution, type: rest || match?.[2] || name }
}

export function parsedResolution(parser: Record<string, unknown> | undefined): string | undefined {
  const quality = parser?.quality
  if (!quality || typeof quality !== 'object' || Array.isArray(quality)) return undefined
  const value = (quality as Record<string, unknown>).resolution
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}
