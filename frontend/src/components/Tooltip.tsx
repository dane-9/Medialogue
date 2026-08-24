import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

type Position = { left: number; top: number; below: boolean }

/**
 * A hover tooltip rendered into document.body.
 *
 * It has to be a portal: the release list is an `overflow: auto` scroll
 * container, and an absolutely-positioned tooltip inside it gets clipped at the
 * container edge. Fixed positioning against the trigger's viewport rect avoids
 * that entirely.
 *
 * The trigger stays a plain inline element so it can sit inside a grid cell
 * without disturbing the row.
 */
export function Tooltip({ content, children, className, focusable = false }: {
  content: ReactNode
  children: ReactNode
  className?: string
  /** Only when the trigger is not already inside something focusable — a nested
   *  tab stop inside a button is worse than losing the keyboard hint. */
  focusable?: boolean
}) {
  const triggerRef = useRef<HTMLSpanElement>(null)
  const bubbleRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<Position>({ left: 0, top: 0, below: false })

  const place = useCallback(() => {
    const trigger = triggerRef.current
    const bubble = bubbleRef.current
    if (!trigger || !bubble) return
    const rect = trigger.getBoundingClientRect()
    const bubbleRect = bubble.getBoundingClientRect()
    const gap = 8

    // Prefer above; flip below only when there is not room for the real height.
    const below = rect.top < bubbleRect.height + gap
    const top = below ? rect.bottom + gap : rect.top - gap - bubbleRect.height

    // Centre on the trigger, then keep the whole bubble on screen.
    const half = bubbleRect.width / 2
    const centre = rect.left + rect.width / 2
    const left = Math.min(Math.max(centre, half + gap), window.innerWidth - half - gap)
    setPosition({ left, top, below })
  }, [])

  useLayoutEffect(() => { if (open) place() }, [open, place])

  useEffect(() => {
    if (!open) return
    // Any scroll moves the trigger out from under the bubble, so close rather
    // than chase it. Capture phase catches the list's own scrolling too.
    const close = () => setOpen(false)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  return <>
    <span
      ref={triggerRef}
      className={className}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      tabIndex={focusable ? 0 : undefined}
    >{children}</span>
    {open && createPortal(
      <div
        ref={bubbleRef}
        className={`tooltip ${position.below ? 'tooltip-below' : ''}`}
        role="tooltip"
        style={{ left: position.left, top: position.top }}
      >{content}</div>,
      document.body,
    )}
  </>
}
