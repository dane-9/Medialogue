/** Double-tap Shift detection, kept as pure logic so its edge cases are testable. */

/** Two Shift presses within this window count as a double tap. */
export const DOUBLE_TAP_MS = 400

/** The subset of a KeyboardEvent this decision depends on. */
export interface TapEvent {
  key: string
  timeStamp: number
  repeat?: boolean
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
}

export interface TapResult {
  /** Whether this press completes a double tap. */
  triggered: boolean
  /** Timestamp to carry into the next call; null means "sequence broken". */
  lastTapAt: number | null
}

/**
 * Decide whether a keypress completes a Shift double-tap.
 *
 * The awkward cases are all about telling a *tap* apart from Shift being held
 * as a modifier:
 *
 *  - Any other key resets the sequence, so typing "AB" (Shift A Shift B) never
 *    triggers, the letters break it.
 *  - Holding Shift repeats keydown; only a fresh press counts.
 *  - Ctrl+Shift / Alt+Shift are shortcuts in their own right, not taps.
 */
export function evaluateShiftTap(event: TapEvent, lastTapAt: number | null): TapResult {
  if (event.key !== 'Shift') return { triggered: false, lastTapAt: null }
  if (event.repeat) return { triggered: false, lastTapAt }
  if (event.ctrlKey || event.metaKey || event.altKey) return { triggered: false, lastTapAt: null }

  // Explicit null check, not a truthiness test: timeStamp 0 is a real press.
  if (lastTapAt !== null && event.timeStamp - lastTapAt <= DOUBLE_TAP_MS) {
    return { triggered: true, lastTapAt: null }
  }
  return { triggered: false, lastTapAt: event.timeStamp }
}
