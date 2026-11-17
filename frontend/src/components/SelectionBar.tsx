import type { SelectionModifiers } from '../lib/useLibrarySelection'
import { Icon } from './Icon'

/** The toolbar's selection summary: how many, select all, and clear. Bulk
 *  actions open with a right-click on a selected title. */
export function SelectionBar({ count, total, noun, onSelectAll, onClear }: {
  count: number
  total: number
  noun: string
  onSelectAll: () => void
  onClear: () => void
}) {
  if (!count) return null
  return <span className="selection-bar" role="status">
    <strong>{count} selected</strong>
    {count < total && <button type="button" onClick={onSelectAll}>Select all {total}</button>}
    <button type="button" onClick={onClear}>Clear</button>
  </span>
}

/** A square select button: on a card's corner, or inline in a table row. Shift
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

/** A column head's select-all box for a list of rows with their own boxes:
 *  checked when every selectable row is, a dash when some are. Clicking selects
 *  every row, or clears them when all are already selected. */
export function HeadCheck({ selected, total, noun, onSelectAll, onClear }: {
  selected: number
  total: number
  noun: string
  onSelectAll: () => void
  onClear: () => void
}) {
  const all = total > 0 && selected >= total
  const some = selected > 0 && !all
  return <button
    type="button"
    role="checkbox"
    aria-checked={all ? true : some ? 'mixed' : false}
    aria-label={all ? `Deselect all ${noun}` : `Select all ${noun}`}
    title={all ? 'Deselect all' : 'Select all'}
    className={`new-media-check head-check ${all || some ? 'on' : ''}`}
    disabled={!total}
    onClick={(event) => { event.stopPropagation(); if (all) onClear(); else onSelectAll() }}
  >{all ? <Icon name="check" size={11} /> : some ? <span className="select-check-dash" /> : null}</button>
}
