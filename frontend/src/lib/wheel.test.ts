import { describe, expect, it } from 'vitest'
import { horizontalWheelDelta } from './wheel'

/** A strip 600px wide holding 1000px of tabs, scrolled to the start. */
const box = (scrollLeft = 0) => ({ scrollLeft, scrollWidth: 1000, clientWidth: 600 })

describe('horizontalWheelDelta', () => {
  it('turns a downward wheel into a rightward scroll', () => {
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: 120 }, box())).toBe(120)
  })

  it('turns an upward wheel into a leftward scroll', () => {
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: -120 }, box(300))).toBe(-120)
  })

  it('ignores a strip that has nothing to scroll', () => {
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: 120 }, { scrollLeft: 0, scrollWidth: 600, clientWidth: 600 })).toBe(0)
  })

  it('leaves an already-horizontal gesture to the browser', () => {
    // A trackpad swipe scrolls sideways natively; intercepting it would double up.
    expect(horizontalWheelDelta({ deltaX: -80, deltaY: 5 }, box(300))).toBe(0)
  })

  it('stops exactly at the end rather than overshooting', () => {
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: 500 }, box(300))).toBe(100)
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: -500 }, box(60))).toBe(-60)
  })

  it('releases the gesture once the strip is at that end', () => {
    // Nothing left to give, so the scroll should pass through to the page.
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: 120 }, box(400))).toBe(0)
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: -120 }, box(0))).toBe(0)
  })

  it('scales wheels that report lines or pages instead of pixels', () => {
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: 3, deltaMode: 1 }, box())).toBe(48)
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: 1, deltaMode: 2 }, box())).toBe(400)
  })

  it('ignores a gesture with no vertical component at all', () => {
    expect(horizontalWheelDelta({ deltaX: 0, deltaY: 0 }, box(200))).toBe(0)
  })
})
