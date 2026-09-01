/** Shorten a quality definition name for display.
 *
 * A REMUX is only ever taken from a disc, and the only disc the library names
 * that way is BluRay - a DVD rip is called "480p DVD REMUX". So "BluRay" adds
 * nothing to "2160p BluRay REMUX", while "DVD" genuinely distinguishes its
 * source and is kept.
 *
 * Only that one redundancy is removed. "Full Disc BluRay" and "Full Disc UHD
 * BluRay" are whole-disc qualities where BluRay is the entire point, and they
 * are left alone.
 */
export function qualityLabel(value: string | null | undefined): string {
  if (!value) return ''
  return value.replace(/\bBluRay REMUX\b/i, 'REMUX')
}
