/** Wheel-to-horizontal-scroll maths, kept pure so its edge cases are testable. */

/** Rough pixel height of a line, for wheels that report lines rather than pixels. */
const LINE_HEIGHT = 16

export interface WheelLike {
  deltaX: number
  deltaY: number
  /** 0 pixels, 1 lines, 2 pages. */
  deltaMode?: number
}

export interface ScrollBox {
  scrollLeft: number
  scrollWidth: number
  clientWidth: number
}

/**
 * How far a vertical wheel gesture should scroll a horizontal strip.
 *
 * Returns 0 to mean "leave this event alone", which matters as much as the
 * scrolling itself:
 *
 *  - Nothing to scroll, so the strip has no business swallowing the gesture.
 *  - The gesture is already horizontal (a trackpad swipe), which the browser
 *    handles natively and better than we would.
 *  - The strip is at the end the gesture points to, so the scroll belongs to
 *    whatever is behind it rather than dying here.
 */
export function horizontalWheelDelta(wheel: WheelLike, box: ScrollBox): number {
  const overflow = box.scrollWidth - box.clientWidth
  if (overflow <= 0) return 0
  if (Math.abs(wheel.deltaX) > Math.abs(wheel.deltaY)) return 0
  if (wheel.deltaY === 0) return 0

  const scale = wheel.deltaMode === 1 ? LINE_HEIGHT : wheel.deltaMode === 2 ? box.clientWidth : 1
  const wanted = wheel.deltaY * scale

  // Clamp to the room actually left, so the last notch stops at the edge
  // instead of overshooting.
  const room = wanted > 0 ? overflow - box.scrollLeft : -box.scrollLeft
  if (room === 0) return 0
  return wanted > 0 ? Math.min(wanted, room) : Math.max(wanted, room)
}
