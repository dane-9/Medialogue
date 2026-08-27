import { cloneElement, isValidElement, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactElement, ReactNode, Ref } from 'react'

type Position = { left: number; top: number; below: boolean }

function mergeRefs<T>(...refs: Array<Ref<T> | undefined>) {
  return (node: T) => {
    for (const ref of refs) {
      if (typeof ref === 'function') ref(node)
      else if (ref && typeof ref === 'object') (ref as { current: T | null }).current = node
    }
  }
}

export const TOOLTIP_DELAY = 1000

export function Tooltip({ content, children, className, focusable = false, asChild = false, delay = TOOLTIP_DELAY }: {
  content: ReactNode
  children: ReactNode
  className?: string

  delay?: number

  focusable?: boolean

  asChild?: boolean
}) {
  const triggerRef = useRef<HTMLElement>(null)
  const bubbleRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const timer = useRef<number>()
  const [position, setPosition] = useState<Position>({ left: 0, top: 0, below: false })

  const place = useCallback(() => {
    const trigger = triggerRef.current
    const bubble = bubbleRef.current
    if (!trigger || !bubble) return
    const rect = trigger.getBoundingClientRect()
    const bubbleRect = bubble.getBoundingClientRect()
    const gap = 8

    const below = rect.top < bubbleRect.height + gap
    const top = below ? rect.bottom + gap : rect.top - gap - bubbleRect.height

    const half = bubbleRect.width / 2
    const centre = rect.left + rect.width / 2
    const left = Math.min(Math.max(centre, half + gap), window.innerWidth - half - gap)
    setPosition({ left, top, below })
  }, [])

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
  // Opening on hover is delayed but closing never is: a tooltip that lingers
  // after the cursor leaves is worse than one that is slow to appear.
  const show = () => {
    cancel()
    if (!delay) { setOpen(true); return }
    timer.current = window.setTimeout(() => setOpen(true), delay)
  }
  const hide = () => { cancel(); setOpen(false) }

  const showNow = () => { cancel(); setOpen(true) }

  useEffect(() => cancel, [])

  const bubble = open ? createPortal(
    <div
      ref={bubbleRef}
      className={`tooltip ${position.below ? 'tooltip-below' : ''}`}
      role="tooltip"
      style={{ left: position.left, top: position.top }}
    >{content}</div>,
    document.body,
  ) : null

  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<Record<string, unknown>> & { ref?: Ref<HTMLElement> }
    return <>
      {cloneElement(child, {
        ref: mergeRefs(triggerRef, child.ref),
        onMouseEnter: (event: never) => { (child.props.onMouseEnter as ((e: never) => void) | undefined)?.(event); show() },
        onMouseLeave: (event: never) => { (child.props.onMouseLeave as ((e: never) => void) | undefined)?.(event); hide() },
        onFocus: (event: never) => { (child.props.onFocus as ((e: never) => void) | undefined)?.(event); showNow() },
        onBlur: (event: never) => { (child.props.onBlur as ((e: never) => void) | undefined)?.(event); hide() },
      })}
      {bubble}
    </>
  }

  return <>
    <span
      ref={triggerRef as Ref<HTMLSpanElement>}
      className={className}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={showNow}
      onBlur={hide}
      tabIndex={focusable ? 0 : undefined}
    >{children}</span>
    {bubble}
  </>
}
