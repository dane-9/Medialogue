import { useCallback, useRef } from 'react'
import { horizontalWheelDelta } from './wheel'

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
