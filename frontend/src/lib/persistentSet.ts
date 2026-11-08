// A set of row ids kept on the server, for choices like "leave this one out"
// that a reload, or another device, should not undo.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { uiSelectionsApi } from '../api/modules/uiSelections'
import type { UiSelectionList } from '../api/modules/uiSelections'

const SAVE_DELAY = 400

/**
 * Like useState<Set<string>>, stored on the server under `list`. Changes are
 * saved shortly after they stop. `prune(ids)` drops rows that no longer exist;
 * call it only with a complete list of current rows.
 */
export function usePersistentSet(list: UiSelectionList): [Set<string>, Dispatch<SetStateAction<Set<string>>>, (ids: Iterable<string>) => void] {
  const [value, setValue] = useState<Set<string>>(() => new Set())
  const latest = useRef<Set<string>>(value)
  const loaded = useRef(false)
  const changedBeforeLoad = useRef(false)
  const timer = useRef<number | undefined>(undefined)

  const save = useCallback(() => {
    window.clearTimeout(timer.current)
    if (!loaded.current) { changedBeforeLoad.current = true; return }
    timer.current = window.setTimeout(() => { void uiSelectionsApi.replace(list, [...latest.current]).catch(() => undefined) }, SAVE_DELAY)
  }, [list])

  useEffect(() => {
    let alive = true
    loaded.current = false
    changedBeforeLoad.current = false
    uiSelectionsApi.get(list).then((ids) => {
      if (!alive) return
      loaded.current = true
      // A change made while loading wins, and is saved now.
      if (changedBeforeLoad.current) { save(); return }
      latest.current = new Set(ids)
      setValue(latest.current)
    }).catch(() => { if (alive) loaded.current = true })
    return () => { alive = false; window.clearTimeout(timer.current) }
  }, [list, save])

  const update = useCallback<Dispatch<SetStateAction<Set<string>>>>((action) => {
    setValue((current) => {
      const next = typeof action === 'function' ? action(current) : action
      latest.current = next
      return next
    })
    save()
  }, [save])

  const prune = useCallback((ids: Iterable<string>) => {
    const keep = new Set(ids)
    const current = latest.current
    const next = new Set([...current].filter((id) => keep.has(id)))
    if (next.size === current.size) return
    latest.current = next
    setValue(next)
    save()
  }, [save])

  return [value, update, prune]
}
