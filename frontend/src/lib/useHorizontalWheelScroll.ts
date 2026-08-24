import { useCallback, useRef } from 'react'
import { horizontalWheelDelta } from './wheel'

/**
 * Let a vertical wheel scroll a horizontally overflowing strip.
 *
 * Two details this depends on:
 *
 *  - The listener is registered natively rather than through React's onWheel,
 *    because React attaches wheel handlers passively and a passive listener
 *    cannot call preventDefault — the page would scroll underneath as well.
 *  - It is a callback ref, not an effect over a ref object. The strip may mount
 *    long after its component does (a later wizard step, a lazy tab), and an
 *    effect with no dependencies would have run once against a null ref and
 *    never attached anything.
 */
export function useHorizontalWheelScroll<T extends HTMLElement>() {
  const cleanup = useRef<(() => void) | null>(null)

  return useCallback((element: T | null) => {
    cleanup.current?.()
    cleanup.current = null
    if (!element) return

    const onWheel = (event: WheelEvent) => {
      const delta = horizontalWheelDelta(event, element)
      if (!delta) return
      event.preventDefault()
      element.scrollLeft += delta
    }

    element.addEventListener('wheel', onWheel, { passive: false })
    cleanup.current = () => element.removeEventListener('wheel', onWheel)
  }, [])
}
