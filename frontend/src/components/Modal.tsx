import { useEffect } from 'react'
import type { PropsWithChildren, ReactNode } from 'react'
import { Icon } from './Icon'

/**
 * A centred dialog with a fixed header and footer and a scrolling body.
 *
 * Editing happens here rather than in a permanently-mounted side pane so the
 * page behind it can stay a simple overview: you see everything at once, then
 * open exactly the one thing you are changing.
 */
export function Modal({ title, eyebrow, icon, onClose, footer, headerAction, wide, className, children }: PropsWithChildren<{
  title: string
  eyebrow?: string
  /** Shown before the title, matching the icon of the section it belongs to. */
  icon?: Parameters<typeof Icon>[0]['name']
  onClose: () => void
  footer?: ReactNode
  /** Shown beside the title, for state that belongs to the whole dialog. */
  headerAction?: ReactNode
  wide?: boolean
  className?: string
}>) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    // The page behind must not scroll while a dialog owns the viewport.
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])

  return <div className="modal-backdrop" onClick={onClose}>
    <div className={`modal ${wide ? 'modal-wide' : ''} ${className ?? ''}`} onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-head">
        {icon && <span className="modal-head-icon"><Icon name={icon} size={18} /></span>}
        <div className="modal-head-copy">
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h2>{title}</h2>
        </div>
        {headerAction && <div className="modal-head-action">{headerAction}</div>}
        <button className="icon-button" onClick={onClose} aria-label="Close"><Icon name="close" size={18} /></button>
      </div>
      <div className="modal-body">{children}</div>
      {footer && <div className="modal-foot">{footer}</div>}
    </div>
  </div>
}
