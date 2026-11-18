// A set of row ids kept on the server, for choices like "leave this one out"
// that a reload, or another device, should not undo.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { uiSelectionsApi } from '../api/modules/uiSelections'
import type { UiSelectionList } from '../api/modules/uiSelections'

const SAVE_DELAY = 400

type Change = (current: Set<string>) => Set<string>

/**
 * Like useState<Set<string>>, stored on the server under `list`. Changes are
 * saved shortly after they stop, and at once when the page is left.
 * `prune(ids)` drops rows that no longer exist; call it only with a complete
 * list of current rows.
 */
export function usePersistentSet(list: UiSelectionList): [Set<string>, Dispatch<SetStateAction<Set<string>>>, (ids: Iterable<string>) => void] {
  const [value, setValue] = useState<Set<string>>(() => new Set())
  const latest = useRef<Set<string>>(value)
  const loaded = useRef(false)
  // Changes made before the stored set arrived. They are replayed onto it,
  // rather than the empty set they were made on replacing it: one tick made
  // while the page loaded used to wipe every other saved choice.
  const early = useRef<Change[]>([])
  const timer = useRef<number | undefined>(undefined)
  const unsaved = useRef(false)

  const send = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = undefined
    if (!unsaved.current || !loaded.current) return
    unsaved.current = false
    void uiSelectionsApi.replace(list, [...latest.current]).catch(() => undefined)
  }, [list])

  const save = useCallback(() => {
    unsaved.current = true
    window.clearTimeout(timer.current)
    if (!loaded.current) return
    timer.current = window.setTimeout(send, SAVE_DELAY)
  }, [send])

  const change = useCallback((apply: Change) => {
    if (!loaded.current) early.current.push(apply)
    const next = apply(latest.current)
    latest.current = next
    setValue(next)
    save()
  }, [save])

  useEffect(() => {
    let alive = true
    loaded.current = false
    early.current = []
    unsaved.current = false
    uiSelectionsApi.get(list).then((ids) => {
      if (!alive) return
      const changes = early.current
      early.current = []
      latest.current = changes.reduce((current, apply) => apply(current), new Set(ids))
      loaded.current = true
      setValue(latest.current)
      if (changes.length) save()
    }).catch(() => { if (alive) { loaded.current = true; early.current = [] } })
    // A change still waiting to be saved goes now, not never: leaving the page
    // within the save delay used to drop it.
    const flush = () => send()
    window.addEventListener('pagehide', flush)
    return () => { alive = false; window.removeEventListener('pagehide', flush); send() }
  }, [list, save, send])

  const update = useCallback<Dispatch<SetStateAction<Set<string>>>>((action) => {
    change((current) => typeof action === 'function' ? action(current) : action)
  }, [change])

  const prune = useCallback((ids: Iterable<string>) => {
    const keep = new Set(ids)
    if (![...latest.current].some((id) => !keep.has(id))) return
    change((current) => new Set([...current].filter((id) => keep.has(id))))
  }, [change])

  return [value, update, prune]
}
