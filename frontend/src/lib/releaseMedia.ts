/** Facts the parser recorded about a release, read back out of its snapshot.
 *
 * Two copies of one movie routinely share a quality string ("2160p BluRay
 * REMUX") and differ only in HDR flavour, audio track, or whether one is a
 * repack. Those are the differences that decide which copy to keep, so they
 * have to come off the snapshot rather than staying buried in it.
 *
 * Reads the same `parse_snapshot` shape as lib/quality.ts and lib/languages.ts.
 */

type Snapshot = Record<string, unknown> | undefined

function section(parser: Snapshot, key: string): Record<string, unknown> | undefined {
  const value = parser?.[key]
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

/** Dolby Vision / HDR10+ / HLG …, in the order the parser reports them. */
export function releaseHdr(parser: Snapshot): string[] {
  const hdr = section(parser, 'hdr')
  const values = hdr?.values
  if (!Array.isArray(values)) return []
  return values.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
}

export function releaseVideoCodec(parser: Snapshot): string | undefined {
  return text(section(parser, 'video')?.codec)
}

/** "TrueHD Atmos 7.1" — codec, object audio, then channels, skipping unknowns. */
export function releaseAudio(parser: Snapshot): string | undefined {
  const audio = section(parser, 'audio')
  const codec = text(audio?.codec)
  const channels = text(audio?.channels)
  const parts = [codec, audio?.atmos === true ? 'Atmos' : audio?.dts_x === true ? 'DTS:X' : undefined, channels]
  const label = parts.filter(Boolean).join(' ')
  return label || undefined
}

/** Why one copy supersedes another: Proper, Repack, Hybrid. */
export function releaseFlags(parser: Snapshot): string[] {
  const attributes = section(parser, 'attributes')
  if (!attributes) return []
  const flags: string[] = []
  if (attributes.proper === true) flags.push('Proper')
  if (attributes.repack === true) flags.push('Repack')
  if (attributes.hybrid === true) flags.push('Hybrid')
  return flags
}
