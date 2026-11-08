// Asks before leaving a view with unsaved changes: in-app links, the browser
// closing or reloading, and any switch that calls confirmLeave() first (the
// Settings tabs). BrowserRouter has no navigation blocker, so links are caught
// at the document before React Router handles them.
import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

type Guard = () => Promise<boolean>

const guards = new Set<{ current: Guard }>()

/** True when every active guard agrees to leave (each may ask first). */
export async function confirmLeave(): Promise<boolean> {
  for (const guard of [...guards]) {
    if (!await guard.current()) return false
  }
  return true
}

/** While `active`, leaving asks `ask`; it resolves true to leave (and discard). */
export function useLeaveGuard(active: boolean, ask: Guard) {
  const navigate = useNavigate()
  const latest = useRef(ask)
  latest.current = ask
  useEffect(() => {
    if (!active) return
    const guard = { current: () => latest.current() }
    guards.add(guard)
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const link = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
      if (!link || (link.target && link.target !== '_self') || link.hasAttribute('download')) return
      const url = new URL(link.href, window.location.href)
      if (url.origin !== window.location.origin) return
      if (url.pathname === window.location.pathname && url.search === window.location.search) return
      event.preventDefault()
      event.stopPropagation()
      void (async () => { if (await confirmLeave()) navigate(url.pathname + url.search + url.hash) })()
    }
    window.addEventListener('beforeunload', beforeUnload)
    document.addEventListener('click', click, true)
    return () => {
      guards.delete(guard)
      window.removeEventListener('beforeunload', beforeUnload)
      document.removeEventListener('click', click, true)
    }
  }, [active, navigate])
}
