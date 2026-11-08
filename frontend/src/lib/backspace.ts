// Some browsers (LibreWolf, Firefox with browser.backspace_action=0, Opera)
// treat Backspace outside a text field as Back. Medialogue keeps tabs and
// filters in the URL, so that silently undoes the last tab switch. Backspace
// here only ever deletes text.

const TEXT_INPUT_TYPES = new Set([
  '', 'text', 'search', 'url', 'tel', 'email', 'password', 'number',
  'date', 'datetime-local', 'month', 'time', 'week',
])

/** Whether Backspace in this element edits text (and must keep working). */
export function editsText(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  if (target instanceof HTMLTextAreaElement) return !target.readOnly && !target.disabled
  if (target instanceof HTMLInputElement) {
    return TEXT_INPUT_TYPES.has(target.type.toLowerCase()) && !target.readOnly && !target.disabled
  }
  return false
}

export function blockBackspaceNavigation(root: Pick<Window, 'addEventListener' | 'removeEventListener'> = window): () => void {
  const onKey = (event: KeyboardEvent) => {
    if (event.key !== 'Backspace' || event.defaultPrevented) return
    if (event.altKey || event.ctrlKey || event.metaKey) return
    if (!editsText(event.target)) event.preventDefault()
  }
  root.addEventListener('keydown', onKey as EventListener)
  return () => root.removeEventListener('keydown', onKey as EventListener)
}
