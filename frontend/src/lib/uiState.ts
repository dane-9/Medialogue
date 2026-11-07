type MediaView = 'cards' | 'table'

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

let fallbackIdCounter = 0

export function browserSafeId(randomUuid: (() => string) | null | undefined = undefined): string {
  const generator = randomUuid === undefined
    ? (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID === 'function'
      ? globalThis.crypto.randomUUID.bind(globalThis.crypto)
      : null)
    : randomUuid
  if (generator) {
    try { return generator() } catch {}
  }
  fallbackIdCounter += 1
  return `local-${Date.now().toString(36)}-${fallbackIdCounter.toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
