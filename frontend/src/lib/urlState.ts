import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * Keeps one piece of view state — which rail item is open, which filter is
 * applied, which page you are on — in the query string instead of component
 * state, so a refresh, a bookmark or a pasted link all land where you left off.
 *
 * Writes use `replace` rather than `push`. Selecting rail items is browsing
 * within a page, not navigating between pages: pushing would make Back step
 * backwards through every item you clicked before it finally left the page.
 * A value equal to the fallback is removed rather than written, so the common
 * case stays a clean URL.
 */
/** Accepts a plain value or an updater, so it drops in for a useState setter. */
export type UrlSetter<T> = (next: T | ((previous: T) => T)) => void

const resolve = <T,>(next: T | ((previous: T) => T), previous: T): T =>
  typeof next === 'function' ? (next as (previous: T) => T)(previous) : next

export function useUrlState(key: string, fallback = ''): [string, UrlSetter<string>] {
  const [params, setParams] = useSearchParams()
  const value = params.get(key) ?? fallback
  const set = useCallback<UrlSetter<string>>((next) => {
    setParams((current) => {
      const updated = new URLSearchParams(current)
      const resolved = resolve(next, current.get(key) ?? fallback)
      if (resolved && resolved !== fallback) updated.set(key, resolved)
      else updated.delete(key)
      return updated
    }, { replace: true })
  }, [key, fallback, setParams])
  return [value, set]
}

/** Numeric companion to useUrlState, for pagination. */
export function useUrlNumber(key: string, fallback: number): [number, UrlSetter<number>] {
  const [raw, setRaw] = useUrlState(key, String(fallback))
  const toNumber = (text: string) => {
    const parsed = Number.parseInt(text, 10)
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
  }
  const set = useCallback<UrlSetter<number>>((next) => {
    setRaw((previous) => String(resolve(next, toNumber(previous))))
  }, [setRaw, fallback])
  return [toNumber(raw), set]
}
