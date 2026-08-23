import type { CustomFormatConditionType, Problem } from '../types'

export type MediaView = 'cards' | 'table'

export function normalizeMediaView(value: string | null | undefined, fallback: MediaView = 'cards'): MediaView {
  return value === 'table' || value === 'cards' ? value : fallback
}

export function toggleIdSelection(current: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(current)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

export function contextMenuSelection(current: ReadonlySet<string>, id: string): Set<string> {
  return current.has(id) ? new Set(current) : new Set([id])
}

export function problemMatchesFilter(
  problem: Pick<Problem, 'code' | 'severity'>,
  reasonFilter: string,
  severityFilter: string,
): boolean {
  const reasonMatches = reasonFilter === 'all'
    || (reasonFilter === 'duplicates' && problem.code.includes('DUPLICATE'))
    || (reasonFilter === 'identity' && (
      problem.code.includes('IDENTITY')
      || problem.code.includes('CONFIDENCE')
      || problem.code.includes('TMDB')
    ))
    || (reasonFilter === 'paths' && (problem.code.includes('PATH') || problem.code.includes('ROOT')))
    || problem.code === reasonFilter
  return reasonMatches && (severityFilter === 'all' || problem.severity === severityFilter)
}

export function duplicateLoserIds(releaseIds: readonly string[], winnerReleaseId: string): string[] {
  if (!winnerReleaseId) return []
  return releaseIds.filter((id) => id !== winnerReleaseId)
}

export function duplicatePreviewReady(releaseIds: readonly string[], winnerReleaseId: string): boolean {
  return Boolean(winnerReleaseId) && releaseIds.includes(winnerReleaseId) && duplicateLoserIds(releaseIds, winnerReleaseId).length > 0
}

export function isRegexConditionType(_type: CustomFormatConditionType): boolean {
  return true
}


let fallbackIdCounter = 0

export function browserSafeId(randomUuid: (() => string) | null | undefined = undefined): string {
  const generator = randomUuid === undefined
    ? (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID === 'function'
      ? globalThis.crypto.randomUUID.bind(globalThis.crypto)
      : null)
    : randomUuid
  if (generator) {
    try { return generator() } catch { /* insecure/unsupported context: fall back below */ }
  }
  fallbackIdCounter += 1
  return `local-${Date.now().toString(36)}-${fallbackIdCounter.toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
