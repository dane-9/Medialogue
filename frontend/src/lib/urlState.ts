import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export type UrlSetter<T> = (next: T | ((previous: T) => T)) => void

const resolve = <T,>(next: T | ((previous: T) => T), previous: T): T =>
  typeof next === 'function' ? (next as (previous: T) => T)(previous) : next

export function updateUrlParams(
  current: URLSearchParams,
  changes: Record<string, string | null | undefined>,
): URLSearchParams {
  const updated = new URLSearchParams(current)
  Object.entries(changes).forEach(([key, value]) => {
    if (value) updated.set(key, value)
    else updated.delete(key)
  })
  return updated
}

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
