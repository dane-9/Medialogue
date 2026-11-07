import { useCallback, useEffect, useRef, useState } from 'react'

export interface SelectionModifiers { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean }

/**
 * One click on a title, as selection: a plain toggle, or with Shift a range
 * from the last title clicked to this one. The range takes the clicked title's
 * new state, so Shift-clicking a selected title clears the range again. Only
 * `order` (the titles on screen, in order) is ever selected.
 */
export function applySelectionClick(
  current: ReadonlySet<string>,
  anchor: string | null,
  id: string,
  order: readonly string[],
  modifiers: SelectionModifiers,
): { selected: Set<string>; anchor: string } {
  const next = new Set(current)
  const selecting = !current.has(id)
  if (modifiers.shiftKey && anchor && order.includes(anchor)) {
    const from = order.indexOf(anchor)
    const to = order.indexOf(id)
    const [start, end] = from <= to ? [from, to] : [to, from]
    for (const key of order.slice(start, end + 1)) {
      if (selecting) next.add(key)
      else next.delete(key)
    }
    return { selected: next, anchor: id }
  }
  if (selecting) next.add(id)
  else next.delete(id)
  return { selected: next, anchor: id }
}

/**
 * Selection for a library page: click, Shift-click ranges, Ctrl/Cmd+A for every
 * title on screen, Escape to clear. Titles that a search or filter hides drop
 * out of the selection, so an action never reaches what you cannot see.
 */
export function useLibrarySelection(visibleIds: readonly string[], { enabled = true }: { enabled?: boolean } = {}) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const anchor = useRef<string | null>(null)
  const order = useRef(visibleIds)
  order.current = visibleIds

  // Whatever is no longer on screen is no longer selected.
  useEffect(() => {
    setSelected((current) => {
      if (!current.size) return current
      const visible = new Set(visibleIds)
      const kept = [...current].filter((id) => visible.has(id))
      return kept.length === current.size ? current : new Set(kept)
    })
  }, [visibleIds])

  const click = useCallback((id: string, modifiers: SelectionModifiers = {}) => {
    setSelected((current) => {
      const result = applySelectionClick(current, anchor.current, id, order.current, modifiers)
      anchor.current = result.anchor
      return result.selected
    })
  }, [])
  /** Right-click: keep a selection that includes this title, else select just it. */
  const focus = useCallback((id: string) => {
    setSelected((current) => current.has(id) ? current : new Set([id]))
    anchor.current = id
  }, [])
  const selectAll = useCallback(() => setSelected(new Set(order.current)), [])
  const clear = useCallback(() => { setSelected(new Set()); anchor.current = null }, [])

  useEffect(() => {
    if (!enabled) return
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && (target.closest('input, textarea, select, [contenteditable="true"]') || target.closest('[role="dialog"], .modal'))) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
        event.preventDefault()
        setSelected(new Set(order.current))
      } else if (event.key === 'Escape') {
        setSelected((current) => current.size ? new Set() : current)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])

  return { selected, click, focus, selectAll, clear }
}
