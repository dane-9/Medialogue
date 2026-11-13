import { Icon } from './Icon'

/** The right-click menu on Movies and Shows: what is selected, and Remove. */
export function LibraryContextMenu({ position, count, noun, onRemove, onClear }: {
  position: { x: number; y: number }
  count: number
  noun: 'movie' | 'show'
  onRemove: () => void
  onClear: () => void
}) {
  const left = Math.max(12, Math.min(position.x, window.innerWidth - 232))
  const top = Math.max(12, Math.min(position.y, window.innerHeight - 120))
  return <div className="bulk-context-menu library-context-menu" role="menu" style={{ left, top }} onClick={(event) => event.stopPropagation()}>
    <div className="bulk-context-head">
      <strong>{count} {noun}{count === 1 ? '' : 's'} selected</strong>
      <button type="button" className="icon-button" onClick={onClear} aria-label="Clear selection"><Icon name="close" size={14} /></button>
    </div>
    <button type="button" role="menuitem" className="download-menu-item tone-red" onClick={onRemove}><Icon name="close" size={13} />Remove…</button>
  </div>
}
