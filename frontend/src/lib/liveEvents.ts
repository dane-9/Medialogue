/**
 * One shared connection to the live event stream per browser tab.
 *
 * Browsers allow only six HTTP/1.1 connections per origin, across every tab.
 * When the shell and each page held their own EventSource, three open tabs
 * were enough to leave ordinary API requests queued behind idle streams.
 * Every subscriber now shares one EventSource; it opens with the first
 * subscriber and closes with the last.
 */

export type LiveEventListener = (event: MessageEvent<string>) => unknown

export interface LiveEventSubscription {
  addEventListener(name: string, listener: LiveEventListener): void
  removeEventListener(name: string, listener: LiveEventListener): void
  /** Drops this subscriber's listeners; the stream closes with the last one. */
  close(): void
}

type SourceFactory = () => EventSource

const STREAM_URL = '/api/v1/events/stream'
const defaultFactory: SourceFactory = () => new EventSource(STREAM_URL, { withCredentials: true })

let factory: SourceFactory = defaultFactory
let source: EventSource | null = null
let subscribers = 0

/** Tests swap in a fake EventSource. */
export function setLiveEventSourceFactory(next: SourceFactory | null): void {
  factory = next ?? defaultFactory
}

export function openLiveEvents(): LiveEventSubscription {
  if (source === null) source = factory()
  subscribers += 1
  const stream = source
  const registered: Array<[string, EventListener]> = []
  let closed = false

  return {
    addEventListener(name, listener) {
      if (closed) return
      const wrapped = listener as unknown as EventListener
      registered.push([name, wrapped])
      stream.addEventListener(name, wrapped)
    },
    removeEventListener(name, listener) {
      const wrapped = listener as unknown as EventListener
      const index = registered.findIndex(([entry, fn]) => entry === name && fn === wrapped)
      if (index === -1) return
      registered.splice(index, 1)
      stream.removeEventListener(name, wrapped)
    },
    close() {
      if (closed) return
      closed = true
      for (const [name, fn] of registered) stream.removeEventListener(name, fn)
      registered.length = 0
      subscribers -= 1
      if (subscribers === 0 && source === stream) {
        stream.close()
        source = null
      }
    },
  }
}
