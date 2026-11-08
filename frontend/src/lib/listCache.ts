// The last loaded copy of a large list (the whole Movies or Shows library),
// kept for this browser tab. Returning to the page shows it at once while a
// fresh copy loads, instead of a blank page for as long as the server takes.

const lists = new Map<string, unknown[]>()

export function cachedList<T>(key: string): T[] | undefined {
  return lists.get(key) as T[] | undefined
}

export function rememberList<T>(key: string, items: T[]): void {
  lists.set(key, items)
}
