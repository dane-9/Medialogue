import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { ConfirmProvider } from '../components/Confirm'
import { setLiveEventSourceFactory } from '../lib/liveEvents'

/** A fake live event stream: tests emit server events into the page. */
export function installLiveEvents() {
  let source: (EventTarget & { close(): void }) | null = null
  setLiveEventSourceFactory(() => {
    const fake = Object.assign(new EventTarget(), { close() { source = null } })
    source = fake
    return fake as unknown as EventSource
  })
  return {
    emit(name: string, data: unknown = {}) {
      source?.dispatchEvent(new MessageEvent(name, { data: typeof data === 'string' ? data : JSON.stringify(data) }))
    },
    get open() { return source !== null },
    uninstall() { setLiveEventSourceFactory(null) },
  }
}

export function renderPage(page: ReactElement, route = '/') {
  return render(<ConfirmProvider><MemoryRouter initialEntries={[route]}>{page}</MemoryRouter></ConfirmProvider>)
}
