import { useCallback, useRef } from 'react'

// Pages reload on live events, on timers and after their own actions, and
// those reads can overlap. Without a guard, a read that started before a
// change could finish after it and put the old data back. Every change sent to
// the server is counted when it starts and when it ends, and a page only takes
// a read that is its newest and saw no change while it was in flight.

let changes = 0
let lastChangeAt = 0

/** A change was sent or answered (see api/transport). */
export function noteChange(): void {
  changes += 1
  lastChangeAt = Date.now()
}

/** When this tab last changed something: a list cached before then may be out of date. */
export function lastChangeTime(): number {
  return lastChangeAt
}

/** Reads one page's data so that only a current answer is shown.
 *
 * Returns a runner: `run(fetch, apply)` fetches and applies, unless a newer
 * read has started meanwhile (it then owns the page), or a change was made
 * while this one was in flight (it reads again, so the change shows).
 */
export function useFreshLoad() {
  const sequence = useRef(0)
  return useCallback(async <T,>(fetch: () => Promise<T>, apply: (value: T) => void): Promise<boolean> => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const mine = ++sequence.current
      const before = changes
      const value = await fetch()
      if (mine !== sequence.current) return false
      if (before === changes) { apply(value); return true }
    }
    return false
  }, [])
}
