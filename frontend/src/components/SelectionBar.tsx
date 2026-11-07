import type { MouseEvent } from 'react'
import type { SelectionModifiers } from '../lib/useLibrarySelection'
import { Icon } from './Icon'

/** The toolbar's selection summary: how many, select all, clear, and the
 *  actions menu, which right-click also opens. */
export function SelectionBar({ count, total, noun, onSelectAll, onClear, onActions }: {
  count: number
  total: number
  noun: string
  onSelectAll: () => void
  onClear: () => void
  onActions: (event: MouseEvent<HTMLButtonElement>) => void
}) {
  if (!count) return null
  return <span className="selection-bar" role="status">
    <strong>{count} selected</strong>
    {count < total && <button type="button" onClick={onSelectAll}>Select all {total}</button>}
    <button type="button" onClick={onClear}>Clear</button>
    <button type="button" className="selection-bar-actions" onClick={onActions}><Icon name="menu" size={13} />Actions</button>
  </span>
}

/** A round select button: on a card's corner, or inline in a table row. Shift
 *  extends the selection to a range. */
export function SelectCheck({ checked, mixed = false, label, inline = false, onToggle }: {
  checked: boolean
  mixed?: boolean
  label: string
  inline?: boolean
  onToggle: (modifiers: SelectionModifiers) => void
}) {
  return <button
    type="button"
    role="checkbox"
    aria-checked={mixed ? 'mixed' : checked}
    aria-label={`Select ${label}`}
    className={`select-check ${inline ? 'inline' : ''} ${checked ? 'on' : ''} ${mixed ? 'mixed' : ''}`}
    onMouseDown={(event) => { if (event.shiftKey) event.preventDefault() }}
    onClick={(event) => { event.preventDefault(); event.stopPropagation(); onToggle({ shiftKey: event.shiftKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey }) }}
  >{checked ? <Icon name="check" size={12} /> : mixed ? <span className="select-check-dash" /> : null}</button>
}
