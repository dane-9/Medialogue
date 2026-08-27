import { describe, expect, it } from 'vitest'
import { DOUBLE_TAP_MS, evaluateShiftTap, type TapEvent } from './doubleTap'

const shift = (timeStamp: number, extra: Partial<TapEvent> = {}): TapEvent => ({ key: 'Shift', timeStamp, ...extra })
const other = (key: string, timeStamp: number): TapEvent => ({ key, timeStamp })

function run(events: TapEvent[]): boolean {
  let last: number | null = null
  let triggered = false
  for (const event of events) {
    const result = evaluateShiftTap(event, last)
    last = result.lastTapAt
    if (result.triggered) triggered = true
  }
  return triggered
}

describe('evaluateShiftTap', () => {
  it('triggers on two quick Shift presses', () => {
    expect(run([shift(0), shift(150)])).toBe(true)
  })

  it('triggers exactly at the window boundary but not past it', () => {
    expect(run([shift(0), shift(DOUBLE_TAP_MS)])).toBe(true)
    expect(run([shift(0), shift(DOUBLE_TAP_MS + 1)])).toBe(false)
  })

  it('does not trigger on a single press', () => {
    expect(run([shift(0)])).toBe(false)
  })

  it('starts a fresh sequence after a slow second press', () => {
    expect(run([shift(0), shift(5000), shift(5100)])).toBe(true)
  })

  it('ignores auto-repeat from holding Shift down', () => {
    expect(run([shift(0), shift(50, { repeat: true }), shift(100, { repeat: true })])).toBe(false)
  })

  it('does not trigger while typing capitals', () => {
    expect(run([shift(0), other('A', 20), shift(120), other('B', 140)])).toBe(false)
  })

  it('does not trigger when Shift is part of a modifier combination', () => {
    expect(run([shift(0, { ctrlKey: true }), shift(100, { ctrlKey: true })])).toBe(false)
    expect(run([shift(0, { altKey: true }), shift(100, { altKey: true })])).toBe(false)
    expect(run([shift(0, { metaKey: true }), shift(100, { metaKey: true })])).toBe(false)
  })

  it('resets after triggering so a third press does not fire again', () => {
    let last: number | null = null
    const first = evaluateShiftTap(shift(0), last); last = first.lastTapAt
    const second = evaluateShiftTap(shift(100), last); last = second.lastTapAt
    expect(second.triggered).toBe(true)
    const third = evaluateShiftTap(shift(200), last)
    expect(third.triggered).toBe(false)
  })

  it('any non-Shift key clears a pending tap', () => {
    expect(evaluateShiftTap(other('a', 10), 5).lastTapAt).toBeNull()
  })
})
