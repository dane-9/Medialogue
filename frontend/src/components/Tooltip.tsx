import { cloneElement, isValidElement, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactElement, ReactNode, Ref } from 'react'

type Position = { left: number; top: number; below: boolean; right: boolean; leftward: boolean }

function mergeRefs<T>(...refs: Array<Ref<T> | undefined>) {
  return (node: T) => {
    for (const ref of refs) {
      if (typeof ref === 'function') ref(node)
      else if (ref && typeof ref === 'object') (ref as { current: T | null }).current = node
    }
  }
}

export const TOOLTIP_DELAY = 1000

export function Tooltip({ content, children, className, focusable = false, asChild = false, delay = TOOLTIP_DELAY, placement = 'auto', interactive = false }: {
  content: ReactNode
  children: ReactNode
  className?: string

  delay?: number

  focusable?: boolean

  asChild?: boolean

  placement?: 'auto' | 'right' | 'left' | 'below'

  interactive?: boolean
}) {
  const triggerRef = useRef<HTMLElement>(null)
  const bubbleRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const timer = useRef<number>()
  const closeTimer = useRef<number>()
  const [position, setPosition] = useState<Position>({ left: 0, top: 0, below: false, right: false, leftward: false })

  const place = useCallback(() => {
    const trigger = triggerRef.current
    const bubble = bubbleRef.current
    if (!trigger || !bubble) return
    const rect = trigger.getBoundingClientRect()
    const bubbleRect = bubble.getBoundingClientRect()
    const gap = 8

    if (placement === 'right' || placement === 'left') {
      const halfHeight = bubbleRect.height / 2
      const centre = Math.min(Math.max(rect.top + rect.height / 2, halfHeight + gap), window.innerHeight - halfHeight - gap)
      // Opening leftward is measured from the bubble's own right edge, and
      // clamped so a trigger near the window edge does not push it off screen.
      const left = placement === 'left'
        ? Math.max(rect.left - gap, bubbleRect.width + gap)
        : rect.right + gap
      setPosition({ left, top: centre, below: false, right: placement === 'right', leftward: placement === 'left' })
      return
    }

    const below = placement === 'below' || rect.top < bubbleRect.height + gap
    const top = below ? rect.bottom + gap : rect.top - gap - bubbleRect.height

    const half = bubbleRect.width / 2
    const centre = rect.left + rect.width / 2
    const left = Math.min(Math.max(centre, half + gap), window.innerWidth - half - gap)
    setPosition({ left, top, below, right: false, leftward: false })
  }, [placement])

  useLayoutEffect(() => { if (open) place() }, [open, place])

  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  const cancel = () => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = undefined }
  }
  const cancelClose = () => {
    if (closeTimer.current) { window.clearTimeout(closeTimer.current); closeTimer.current = undefined }
  }
  const show = () => {
    cancel()
    cancelClose()
    if (!delay) { setOpen(true); return }
    timer.current = window.setTimeout(() => setOpen(true), delay)
  }
  const hide = () => { cancel(); cancelClose(); setOpen(false) }
  const leave = () => {
    cancel()
    if (!interactive) { hide(); return }
    cancelClose()
    closeTimer.current = window.setTimeout(() => setOpen(false), 140)
  }

  const showNow = () => { cancel(); cancelClose(); setOpen(true) }

  useEffect(() => () => { cancel(); cancelClose() }, [])

  const bubble = open ? createPortal(
    <div
      ref={bubbleRef}
      className={`tooltip ${position.below ? 'tooltip-below' : ''} ${position.right ? 'tooltip-right' : ''} ${position.leftward ? 'tooltip-left' : ''} ${interactive ? 'tooltip-interactive' : ''}`}
      role={interactive ? 'dialog' : 'tooltip'}
      style={{ left: position.left, top: position.top }}
      onMouseEnter={interactive ? cancelClose : undefined}
      onMouseLeave={interactive ? leave : undefined}
      onFocusCapture={interactive ? cancelClose : undefined}
      onBlurCapture={interactive ? leave : undefined}
      onClick={interactive ? hide : undefined}
    >{content}</div>,
    document.body,
  ) : null

  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<Record<string, unknown>> & { ref?: Ref<HTMLElement> }
    return <>
      {cloneElement(child, {
        ref: mergeRefs(triggerRef, child.ref),
        onMouseEnter: (event: never) => { (child.props.onMouseEnter as ((e: never) => void) | undefined)?.(event); show() },
        onMouseLeave: (event: never) => { (child.props.onMouseLeave as ((e: never) => void) | undefined)?.(event); leave() },
        onFocus: (event: never) => { (child.props.onFocus as ((e: never) => void) | undefined)?.(event); showNow() },
        onBlur: (event: never) => { (child.props.onBlur as ((e: never) => void) | undefined)?.(event); leave() },
      })}
      {bubble}
    </>
  }

  return <>
    <span
      ref={triggerRef as Ref<HTMLSpanElement>}
      className={className}
      onMouseEnter={show}
      onMouseLeave={leave}
      onFocus={showNow}
      onBlur={leave}
      tabIndex={focusable ? 0 : undefined}
    >{children}</span>
    {bubble}
  </>
}
