// The last loaded copy of a large list (the whole Movies or Shows library).
// Returning to the page shows it at once while a fresh copy loads, instead of a
// blank page for as long as the server takes. The default Movies and Shows
// views are also saved in the browser, so this works after a reload too.

const lists = new Map<string, unknown[]>()

const PERSISTED_KEYS = ['movies::title', 'shows:title']
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
  const reading = withStore('readonly', (store) => Promise.all(PERSISTED_KEYS.map((key) => result(store.get(key)))))
    .then((saved) => saved.forEach((entry, index) => {
      const key = PERSISTED_KEYS[index]
      if (entry?.build === __BUILD_ID__ && Array.isArray(entry.items) && !lists.has(key)) lists.set(key, entry.items)
    }))
    .catch(() => undefined)
  return Promise.race([reading, new Promise<void>((resolve) => { window.setTimeout(resolve, HYDRATE_TIMEOUT_MS) })])
}

export function cachedList<T>(key: string): T[] | undefined {
  return lists.get(key) as T[] | undefined
}

export function rememberList<T>(key: string, items: T[]): void {
  lists.set(key, items)
  if (!canPersist() || !PERSISTED_KEYS.includes(key)) return
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
  saves.forEach((timer) => window.clearTimeout(timer))
  saves.clear()
  if (canPersist()) void withStore('readwrite', (store) => result(store.clear())).catch(() => undefined)
}
