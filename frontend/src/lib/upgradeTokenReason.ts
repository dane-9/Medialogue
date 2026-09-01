import type { ReleaseTokenDiff } from './releaseTokens'

export interface UpgradeTokenContext {
  upgradeTypes: string[]
  addedCustomFormats: Array<{ name: string; delta: number; matchedTokens?: string[] }>
  currentQuality?: string
  quality?: string
}

type FormatReason = { name: string; delta: number; matchedTokens?: string[] }

const tokenWords = (value: unknown): string[] => typeof value === 'string'
  ? value.toLowerCase().split(/[^a-z0-9+]+/i).filter(Boolean)
  : []

/** Combine score improvements with every format the candidate actually matched.
 *
 * `addedCustomFormats` intentionally contains only positive score deltas. A
 * specific detector such as DV HDR10 may still have matched with a zero score,
 * while the broader Dolby Vision detector contributed points. Token tooltips
 * need the specific matching detector and its real configured contribution.
 */
export function candidateFormatReasons(
  comparisonSnapshot: Record<string, unknown>,
  improvements: FormatReason[],
): FormatReason[] {
  const candidate = comparisonSnapshot.candidate
  const formats = candidate && typeof candidate === 'object'
    ? (candidate as Record<string, unknown>).formats
    : undefined
  const reasons = new Map<string, FormatReason>()
  if (Array.isArray(formats)) {
    for (const value of formats) {
      if (!value || typeof value !== 'object') continue
      const format = value as Record<string, unknown>
      if (!format.matched || format.suppressed_for_upgrade) continue
      const name = typeof format.custom_format_name === 'string' ? format.custom_format_name : ''
      if (!name) continue
      const contribution = Number(format.contribution ?? 0)
      const matchedTokens = new Set<string>()
      const conditions = Array.isArray(format.conditions) ? format.conditions : []
      for (const rawCondition of conditions) {
        if (!rawCondition || typeof rawCondition !== 'object') continue
        const condition = rawCondition as Record<string, unknown>
        if (!condition.matched || condition.negated) continue
        tokenWords(condition.regex_match).forEach((token) => matchedTokens.add(token))
        // A release-title condition's evidence is the entire title, which
        // would falsely attribute every token to the format. Its regex match,
        // when present, is the precise evidence and was already included.
        if (condition.condition_type !== 'release_title') {
          tokenWords(condition.evidence).forEach((token) => matchedTokens.add(token))
        }
      }
      reasons.set(name.toLowerCase(), {
        name,
        delta: Number.isFinite(contribution) ? contribution : 0,
        matchedTokens: [...matchedTokens],
      })
    }
  }
  // An improvement's delta is more meaningful than its total contribution.
  // Keep that delta when the same format also appeared in the full evidence.
  for (const improvement of improvements) {
    const existing = reasons.get(improvement.name.toLowerCase())
    reasons.set(improvement.name.toLowerCase(), { ...improvement, matchedTokens: existing?.matchedTokens })
  }
  return [...reasons.values()]
}

/** Link scene tokens that describe one combined dynamic-range format.
 *
 * Trackers conventionally spell these as adjacent dotted tokens (`DV.HDR` or
 * `DV.HDR10+`). Returning the same group for both lets the UI hover them as
 * one format without changing the release title that is displayed.
 */
export function combinedDynamicRangeTokenGroups(tokens: ReleaseTokenDiff[]): Map<number, string> {
  const groups = new Map<number, string>()
  for (let index = 0; index < tokens.length - 1; index += 1) {
    const first = tokens[index].text.toLowerCase()
    const second = tokens[index + 1].text.toLowerCase()
    if (!/^(?:dv|dovi)$/.test(first) || !/^hdr(?:10)?\+?$/.test(second)) continue
    const group = `${index}:${index + 1}`
    groups.set(index, group)
    groups.set(index + 1, group)
  }
  return groups
}

/** Group any consecutive tokens explained by the same upgrade reason. */
export function sharedUpgradeReasonTokenGroups(reasons: Array<string | undefined>): Map<number, string> {
  const groups = new Map<number, string>()
  let start = 0
  while (start < reasons.length) {
    const reason = reasons[start]
    let end = start + 1
    while (reason && end < reasons.length && reasons[end] === reason) end += 1
    if (reason && end - start > 1) {
      const group = `${start}:${end - 1}`
      for (let index = start; index < end; index += 1) groups.set(index, group)
    }
    start = end
  }
  return groups
}

/** Give both halves of a combined dynamic-range label the same explanation.
 *
 * An indexer evaluation can credit only `DV` (for example, to a Dolby Vision
 * format) even though the scene title writes the capability as `DV.HDR`. The
 * display must still treat the whole label as the scored improvement.
 */
export function shareCombinedDynamicRangeReasons(
  tokens: ReleaseTokenDiff[],
  reasons: Array<string | undefined>,
): Array<string | undefined> {
  const groups = combinedDynamicRangeTokenGroups(tokens)
  const shared = new Map<string, string>()
  reasons.forEach((reason, index) => {
    const group = groups.get(index)
    if (group && reason && !shared.has(group)) shared.set(group, reason)
  })
  return reasons.map((reason, index) => reason ?? shared.get(groups.get(index) ?? ''))
}

/** Scene abbreviations, so a token can find the Custom Format it earned.
 *
 * A Custom Format is named for what it is ("Dolby Vision"), while a release
 * names it how the scene writes it ("DV"). Without the bridge, the token that
 * caused the score change reads as unexplained.
 */
const ALIASES: Record<string, string[]> = {
  // Include both the scene token and expanded wording. Exact scene wording is
  // checked before expansions, so DV prefers DV HDR10 over Dolby Vision even
  // if the latter happens to appear first in the response.
  dv: ['dv', 'dolby', 'vision'],
  hdr: ['hdr', 'hdr10'],
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
  rerip: 'RERIP - the source was ripped again to correct the first attempt.',
}

const words = (value: string) => tokenWords(value)

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
  if (tag && context.upgradeTypes.some((type) => type === key || (key === 'rerip' && type === 'revision'))) return tag

  // A gained Custom Format names the token that earned it, once the scene
  // abbreviation is expanded.
  const candidates = ALIASES[key] ?? words(token.text)
  for (const candidate of candidates) {
    for (const format of context.addedCustomFormats) {
      const named = words(format.name)
      if (!named.includes(candidate)) continue
      const delta = format.delta >= 0 ? `+${format.delta}` : `${format.delta}`
      return `Custom Format: ${format.name} (${delta} to the score).`
    }
  }

  // A user-defined format can be named anything. Condition-level match
  // evidence ties its detected release tokens back to that arbitrary name.
  for (const format of context.addedCustomFormats) {
    if (!format.matchedTokens?.includes(key)) continue
    const delta = format.delta >= 0 ? `+${format.delta}` : `${format.delta}`
    return `Custom Format: ${format.name} (${delta} to the score).`
  }

  // Resolution and source tokens: the quality change already states itself.
  if (context.quality && context.quality !== context.currentQuality && words(context.quality).includes(key)) {
    return `Quality: ${context.currentQuality ?? 'unknown'} to ${context.quality}.`
  }

  return undefined
}
