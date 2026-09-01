import type { ReleaseTokenDiff } from './releaseTokens'

export interface UpgradeTokenContext {
  upgradeTypes: string[]
  addedCustomFormats: Array<{ name: string; delta: number }>
  currentQuality?: string
  quality?: string
  currentReleaseName: string
}

/** Scene abbreviations, so a token can find the Custom Format it earned.
 *
 * A Custom Format is named for what it is ("Dolby Vision"), while a release
 * names it how the scene writes it ("DV"). Without the bridge, the token that
 * caused the score change reads as unexplained.
 */
const ALIASES: Record<string, string[]> = {
  dv: ['dolby', 'vision'],
  hdr: ['hdr'],
  'hdr10+': ['hdr10+'],
  truehd: ['truehd'],
  atmos: ['atmos'],
  remux: ['remux'],
  ddp: ['dolby', 'digital', 'plus'],
  dts: ['dts'],
  'dts-hd': ['dts', 'hd'],
  ma: ['ma'],
  hevc: ['hevc'],
  uhd: ['uhd'],
}

/** The scene re-release tags, each with the reason it outranks what you have. */
const SCENE_TAGS: Record<string, string> = {
  repack: 'REPACK - the release group re-released this to fix a flaw in its own rip.',
  proper: 'PROPER - another group released a corrected version because the original was broken.',
  real: 'REAL - issued to supersede a release that was itself wrong or was nuked.',
  rerip: 'RERIP - the source was ripped again to correct the first attempt.',
}

const words = (value: string) => value.toLowerCase().split(/[^a-z0-9+]+/i).filter(Boolean)

/** Explain why one token of a candidate title is an improvement.
 *
 * Returns undefined unless the token can be tied to a specific reason this
 * candidate outranks what you have. Merely differing is not a reason: a release
 * name is full of tokens that vary between groups without meaning anything, and
 * highlighting those buries the few that matter.
 */
export function upgradeTokenReason(token: ReleaseTokenDiff, context: UpgradeTokenContext): string | undefined {
  if (!token.changed) return undefined
  const key = token.text.toLowerCase()

  // The tag itself is the reason, and it is the strongest one a token can carry.
  const tag = SCENE_TAGS[key]
  if (tag && context.upgradeTypes.some((type) => type === key || (key === 'real' && type === 'revision'))) return tag

  // A gained Custom Format names the token that earned it, once the scene
  // abbreviation is expanded.
  const candidates = ALIASES[key] ?? words(token.text)
  for (const format of context.addedCustomFormats) {
    const named = words(format.name)
    if (candidates.some((part) => named.includes(part))) {
      const delta = format.delta >= 0 ? `+${format.delta}` : `${format.delta}`
      return `Custom Format: ${format.name} (${delta} to the score).`
    }
  }

  // Resolution and source tokens: the quality change already states itself.
  if (context.quality && context.quality !== context.currentQuality && words(context.quality).includes(key)) {
    return `Quality: ${context.currentQuality ?? 'unknown'} to ${context.quality}.`
  }

  // The group rides at the end of the name behind a hyphen.
  if (token.sep === '-') {
    const current = context.currentReleaseName.slice(context.currentReleaseName.lastIndexOf('-') + 1)
    return current && current !== context.currentReleaseName
      ? `Release group: ${current} to ${token.text}.`
      : `Release group: ${token.text}.`
  }

  return undefined
}
