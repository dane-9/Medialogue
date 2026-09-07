const RESOLUTION = /^\d{3,4}[pi]$/i

function normaliseToken(token: string): string {
  if (RESOLUTION.test(token)) return token.toLowerCase()
  // REMUX is an initialism and the single most load-bearing word in the ladder,
  // so it is never title-cased down into "Remux".
  if (/^remux$/i.test(token)) return 'REMUX'
  return token
}

/** Label a file's quality tier.
 *
 * `quality` is a canonical tier name from the server ("1080p BluRay REMUX",
 * "1080p Encode", "2160p WEB-DL"), which already reads correctly; this only
 * normalises its casing.
 *
 * It deliberately does not rebuild the label out of resolution + source +
 * modifier, and it never folds the edition in. That gave the edition and REMUX
 * one shared trailing slot, so an Extended Cut remux rendered as "1080p BluRay"
 * — indistinguishable from an encode — and an encode lost its source entirely,
 * since the "1080p Encode" tier name contains no source word to detect.
 */
export function qualityMark(quality?: string): string {
  return (quality ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map(normaliseToken)
    .join(' ')
}

/** The part of an edition worth showing. A compound edition
    ("Extended · Hybrid") contributes only its first segment. */
export function editionLabel(edition?: string): string {
  return edition?.split('·')[0].trim() ?? ''
}
