// The last loaded copy of a large list (the whole Movies or Shows library).
// Returning to the page shows it at once while a fresh copy loads, instead of a
// blank page for as long as the server takes. The default Movies and Shows
// views are also saved in the browser, so this works after a reload too.

import { lastChangeTime } from './freshness'

const lists = new Map<string, unknown[]>()
// When each list was last loaded in this tab; one restored from the browser has none.
const rememberedAt = new Map<string, number>()

// The default view of each library: movies:<library>::title, shows:<library>:title.
const persisted = (key: string) => /^movies:[^:]*::title$/.test(key) || /^shows:[^:]*:title$/.test(key)
const DATABASE = 'medialogue-lists'
const STORE = 'lists'
// A burst of refreshes saves once.
const SAVE_DELAY_MS = 1500
// However the browser's storage is doing, the app does not wait on it for long.
const HYDRATE_TIMEOUT_MS = 400

const saves = new Map<string, number>()
const canPersist = () => typeof indexedDB !== 'undefined'

function result<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function withStore<T>(mode: IDBTransactionMode, work: (store: IDBObjectStore) => Promise<T>): Promise<T> {
  const opening = indexedDB.open(DATABASE, 1)
  opening.onupgradeneeded = () => { opening.result.createObjectStore(STORE) }
  const database = await result(opening)
  try { return await work(database.transaction(STORE, mode).objectStore(STORE)) }
  finally { database.close() }
}

/** Load the saved lists. Lists saved by another build may have another shape, so those are left out. */
export function hydrateListCache(): Promise<void> {
  if (!canPersist()) return Promise.resolve()
  // Both requests go out at once, inside the one transaction.
  const reading = withStore('readonly', (store) => Promise.all([result(store.getAllKeys()), result(store.getAll())]))
    .then(([keys, entries]) => keys.forEach((rawKey, index) => {
      const key = String(rawKey)
      const entry = entries[index]
      if (!persisted(key)) return
      if (entry?.build === __BUILD_ID__ && Array.isArray(entry.items) && !lists.has(key)) lists.set(key, entry.items)
    }))
    .catch(() => undefined)
  return Promise.race([reading, new Promise<void>((resolve) => { window.setTimeout(resolve, HYDRATE_TIMEOUT_MS) })])
}

/** The last loaded copy, unless something was changed since: then it may be out of date and the page loads afresh. */
export function cachedList<T>(key: string): T[] | undefined {
  if ((rememberedAt.get(key) ?? 0) < lastChangeTime()) return undefined
  return lists.get(key) as T[] | undefined
}

export function rememberList<T>(key: string, items: T[]): void {
  lists.set(key, items)
  rememberedAt.set(key, Date.now())
  if (!canPersist() || !persisted(key)) return
  window.clearTimeout(saves.get(key))
  saves.set(key, window.setTimeout(() => {
    saves.delete(key)
    // Only a convenience: a full or blocked store just means a slower next load.
    void withStore('readwrite', (store) => result(store.put({ build: __BUILD_ID__, items }, key))).catch(() => undefined)
  }, SAVE_DELAY_MS))
}

/** Forget every list, here and in the browser (on sign-out, and between tests). */
export function clearListCache(): void {
  lists.clear()
  rememberedAt.clear()
  saves.forEach((timer) => window.clearTimeout(timer))
  saves.clear()
  if (canPersist()) void withStore('readwrite', (store) => result(store.clear())).catch(() => undefined)
}
