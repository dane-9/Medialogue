

export const DOUBLE_TAP_MS = 400

export interface TapEvent {
  key: string
  timeStamp: number
  repeat?: boolean
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
}

export interface TapResult {

  triggered: boolean

  lastTapAt: number | null
}

export function evaluateShiftTap(event: TapEvent, lastTapAt: number | null): TapResult {
  if (event.key !== 'Shift') return { triggered: false, lastTapAt: null }
  if (event.repeat) return { triggered: false, lastTapAt }
  if (event.ctrlKey || event.metaKey || event.altKey) return { triggered: false, lastTapAt: null }

  if (lastTapAt !== null && event.timeStamp - lastTapAt <= DOUBLE_TAP_MS) {
    return { triggered: true, lastTapAt: null }
  }
  return { triggered: false, lastTapAt: event.timeStamp }
}
