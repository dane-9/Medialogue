

const LINE_HEIGHT = 16

interface WheelLike {
  deltaX: number
  deltaY: number

  deltaMode?: number
}

interface ScrollBox {
  scrollLeft: number
  scrollWidth: number
  clientWidth: number
}

export function horizontalWheelDelta(wheel: WheelLike, box: ScrollBox): number {
  const overflow = box.scrollWidth - box.clientWidth
  if (overflow <= 0) return 0
  if (Math.abs(wheel.deltaX) > Math.abs(wheel.deltaY)) return 0
  if (wheel.deltaY === 0) return 0

  const scale = wheel.deltaMode === 1 ? LINE_HEIGHT : wheel.deltaMode === 2 ? box.clientWidth : 1
  const wanted = wheel.deltaY * scale

  const room = wanted > 0 ? overflow - box.scrollLeft : -box.scrollLeft
  if (room === 0) return 0
  return wanted > 0 ? Math.min(wanted, room) : Math.max(wanted, room)
}
