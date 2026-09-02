import { useEffect } from 'react'
import type { PropsWithChildren, ReactNode } from 'react'
import { Icon } from './Icon'

export function Modal({ title, titleSuffix, eyebrow, icon, brand, onClose, footer, titleAction, headerAction, headerCenter, wide, className, children }: PropsWithChildren<{
  title: string
  titleSuffix?: ReactNode
  eyebrow?: string

  icon?: Parameters<typeof Icon>[0]['name']

  /** Provider artwork shown ahead of the title, where a built-in icon would sit. */
  brand?: ReactNode
  onClose: () => void
  footer?: ReactNode

  titleAction?: ReactNode

  headerAction?: ReactNode

  headerCenter?: ReactNode
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
        {brand && <span className="modal-head-brand">{brand}</span>}
        {icon && <span className="modal-head-icon"><Icon name={icon} size={18} /></span>}
        <div className="modal-head-copy">
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h2>{title}{titleSuffix}</h2>
        </div>
        {titleAction && <div className="modal-head-title-action">{titleAction}</div>}
        <span className="modal-head-spacer" />
        {headerCenter && <div className="modal-head-center">{headerCenter}</div>}
        {headerAction && <div className="modal-head-action">{headerAction}</div>}
        <button className="icon-button" onClick={onClose} aria-label="Close"><Icon name="close" size={18} /></button>
      </div>
      <div className="modal-body">{children}</div>
      {footer && <div className="modal-foot">{footer}</div>}
    </div>
  </div>
}
