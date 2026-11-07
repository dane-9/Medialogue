import { splitQuality } from './quality'

interface UpgradeFacts {
  currentQuality?: string
  quality?: string
  upgradeTypes: string[]
  addedCustomFormats: { name: string; delta: number }[]
  scoreDelta: number
}

const REVISIONS: Record<string, string> = { proper: 'PROPER', repack: 'REPACK', revision: 'RERIP' }
/** Custom Formats about the picture rather than the sound. */
const PICTURE = /(^|[^a-z])(dv|dolby\s*vision|hdr(10\+?)?|hlg|imax)($|[^a-z0-9])/i

/**
 * What an upgrade improves, biggest first: a higher resolution, a better
 * source at the same resolution, a full season instead of episodes, a better
 * picture (Dolby Vision, HDR10+), a fixed re-release, then any other Custom
 * Format gain (audio and the like), which leads only when nothing else does. `label` is the one to show;
 * `details` lists every improvement for the hover.
 */
export function primaryUpgrade(facts: UpgradeFacts): { label: string; details: string[] } | null {
  const types = new Set(facts.upgradeTypes)
  const found: { rank: number; label: string; detail: string }[] = []
  if (types.has('quality')) {
    const from = splitQuality(facts.currentQuality)
    const to = splitQuality(facts.quality)
    if (from.resolution && to.resolution && from.resolution !== to.resolution) {
      // Only what changes is named: the source too when it differs.
      const label = from.type && to.type && from.type !== to.type
        ? `${from.resolution} ${from.type} → ${to.resolution} ${to.type}`
        : `${from.resolution} → ${to.resolution}`
      found.push({ rank: 0, label, detail: label })
    } else if (from.type && to.type && from.type !== to.type) {
      found.push({ rank: 1, label: `${from.type} → ${to.type}`, detail: `${from.type} → ${to.type}` })
    } else if (facts.currentQuality && facts.quality) {
      found.push({ rank: 1, label: 'Better quality', detail: `${facts.currentQuality} → ${facts.quality}` })
    }
  }
  if (types.has('season_pack')) found.push({ rank: 2, label: 'SEASON PACK', detail: 'Single episodes → full season pack' })
  if (types.has('custom_format')) {
    const gained = [...facts.addedCustomFormats].filter((item) => item.delta > 0).sort((a, b) => b.delta - a.delta)
    const picture = gained.find((item) => PICTURE.test(item.name))
    // A better picture (Dolby Vision, HDR10+) is worth the badge on its own;
    // audio and other formats only lead when nothing else improved.
    found.push({
      rank: picture ? 3 : 5,
      label: picture ? picture.name : gained[0] ? gained[0].name : `CF +${facts.scoreDelta}`,
      detail: gained.length ? `Custom Formats +${facts.scoreDelta} (${gained.map((item) => item.name).join(', ')})` : `Custom Formats +${facts.scoreDelta}`,
    })
  }
  for (const [type, word] of Object.entries(REVISIONS)) {
    if (types.has(type)) found.push({ rank: 4, label: word, detail: `${word}: a fixed re-release` })
  }
  if (!found.length) return null
  found.sort((a, b) => a.rank - b.rank)
  return { label: found[0].label, details: found.map((item) => item.detail) }
}

interface ComparedFormat { id: string; name: string; contribution: number; group?: string }

export interface UpgradeChange {
  label: string
  from: string
  to: string
  /** better, worse, or a plain fact such as size. */
  tone: 'better' | 'worse' | 'neutral'
}

/**
 * Everything that differs between the current release and the upgrade, one
 * row per aspect: resolution, source, the release itself (season pack,
 * PROPER), then each Custom Format group (Dynamic Range, Audio, ...).
 * Aspects that stay the same are left out.
 */
export function upgradeChanges(item: {
  currentQuality?: string
  quality?: string
  upgradeTypes: string[]
  currentMatchedFormats: ComparedFormat[]
  candidateMatchedFormats: ComparedFormat[]
  currentCustomFormatScore: number
  customFormatScore: number
}): UpgradeChange[] {
  const rows: UpgradeChange[] = []
  const from = splitQuality(item.currentQuality)
  const to = splitQuality(item.quality)
  const better = item.upgradeTypes.includes('quality')
  // Resolution and source read as one quality: 1080p WEB-DL → 2160p REMUX.
  const full = (parts: { resolution?: string; type: string }) => [parts.resolution, parts.type].filter(Boolean).join(' ')
  if ((from.resolution && to.resolution && from.resolution !== to.resolution) || (from.type && to.type && from.type !== to.type)) {
    rows.push({ label: 'Quality', from: full(from), to: full(to), tone: better ? 'better' : 'neutral' })
  }

  if (item.upgradeTypes.includes('season_pack')) rows.push({ label: 'Release', from: 'Episodes', to: 'SEASON PACK', tone: 'better' })
  const revision = Object.entries(REVISIONS).find(([type]) => item.upgradeTypes.includes(type))
  if (revision) rows.push({ label: 'Release', from: 'Original', to: revision[1], tone: 'better' })

  // Formats of one group fill the same part of a release, so they compare as
  // a set; a format with no group compares on its own.
  const groups = new Map<string, { label: string; current: ComparedFormat[]; candidate: ComparedFormat[] }>()
  const add = (format: ComparedFormat, side: 'current' | 'candidate') => {
    const key = format.group ? `group:${format.group}` : `format:${format.name}`
    const entry = groups.get(key) ?? { label: format.group ?? format.name, current: [], candidate: [] }
    entry[side].push(format)
    groups.set(key, entry)
  }
  // Re-release formats (Proper, Repack, Rerip) are the Release row below.
  const revisionFormat = (format: ComparedFormat) => /^(proper|repack\d*|rerip\d*|revision)$/i.test(format.name.trim())
  item.currentMatchedFormats.filter((format) => !revisionFormat(format)).forEach((format) => add(format, 'current'))
  item.candidateMatchedFormats.filter((format) => !revisionFormat(format)).forEach((format) => add(format, 'candidate'))
  const total = (formats: ComparedFormat[]) => formats.reduce((sum, format) => sum + format.contribution, 0)
  const names = (formats: ComparedFormat[]) => formats.map((format) => format.name).join(' · ') || '—'
  // A format that only names a source (Remux, WEB-DL) repeats the Source row.
  const sources = new Set([from.type, to.type].filter(Boolean).map((value) => value.toLowerCase()))
  const formatRows = [...groups.entries()]
    .filter(([key, entry]) => !(key.startsWith('format:') && sources.has(entry.label.toLowerCase())))
    .map(([, entry]) => entry)
    .filter((entry) => names(entry.current) !== names(entry.candidate))
    .map((entry) => {
      const delta = total(entry.candidate) - total(entry.current)
      return { label: entry.label, from: names(entry.current), to: names(entry.candidate), delta }
    })
    .sort((a, b) => b.delta - a.delta)
  for (const row of formatRows) {
    // A format matching one side only, at no score difference, is a naming
    // gap (AVC is h264 too), not a change worth listing.
    if (row.delta === 0 && (row.from === '—' || row.to === '—')) continue
    rows.push({ label: row.label, from: row.from, to: row.to, tone: row.delta > 0 ? 'better' : row.delta < 0 ? 'worse' : 'neutral' })
  }

  return rows
}
