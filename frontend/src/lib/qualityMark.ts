

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
  const resolution = tokens.find((token) => /^\d{3,4}[pi]$/i.test(token))?.toLowerCase()
  const source = SOURCES.find(([pattern]) => pattern.test(quality))?.[1]

  const remux = /\bremux\b/i.test(quality) ? 'Remux' : undefined
  const modifier = edition?.split('·')[0].trim() || remux

  return [resolution, source, modifier].filter(Boolean).join(' ')
}
