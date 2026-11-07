import { useEffect, useRef } from 'react'
import { openLiveEvents } from './liveEvents'
import type { LiveEventListener, LiveEventSubscription } from './liveEvents'

/**
 * Keeping a view current: refresh on live events, on a fallback timer, and
 * when the tab comes back into view.
 *
 * Every page used to hand-roll this with its own EventSource, timers and
 * visibility listener, each with slightly different rules. This is the one
 * implementation; pages say which events matter and how often to poll.
 */
export interface LiveRefreshOptions {
  /** Live events that trigger a refresh. */
  events?: readonly string[]
  /** Gate an event: return false to ignore it. */
  when?: (event: MessageEvent<string>, name: string) => boolean
  /**
   * Events within this window share one refresh, run at its end. 0 runs
   * every event's refresh straight away.
   */
  coalesceMs?: number
  /** Fallback timer for anything events do not carry. */
  pollMs?: number
  /** Skip refreshes while the tab is hidden (default true). */
  onlyWhenVisible?: boolean
  /** Refresh when the tab becomes visible again (default: onlyWhenVisible). */
  refreshOnVisible?: boolean
  /** Also refresh once on start. */
  immediate?: boolean
  /** False stops everything until it is true again (default true). */
  enabled?: boolean
}

export interface LiveRefreshEnvironment {
  openStream: () => LiveEventSubscription
  document: Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'>
  setTimeout: (fn: () => void, ms: number) => number
  clearTimeout: (id: number) => void
  setInterval: (fn: () => void, ms: number) => number
  clearInterval: (id: number) => void
}

const browserEnvironment = (): LiveRefreshEnvironment => ({
  openStream: openLiveEvents,
  document,
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (id) => window.clearTimeout(id),
  setInterval: (fn, ms) => window.setInterval(fn, ms),
  clearInterval: (id) => window.clearInterval(id),
})

/** The framework-free core; returns a function that stops it. */
export function startLiveRefresh(
  refresh: () => unknown,
  options: Omit<LiveRefreshOptions, 'enabled'>,
  env: LiveRefreshEnvironment = browserEnvironment(),
): () => void {
  const {
    events = [],
    when,
    coalesceMs = 0,
    pollMs,
    onlyWhenVisible = true,
    refreshOnVisible = onlyWhenVisible,
    immediate = false,
  } = options
  let stopped = false
  let pending: number | undefined

  const visible = () => env.document.visibilityState !== 'hidden'
  const run = () => {
    if (stopped || (onlyWhenVisible && !visible())) return
    void refresh()
  }
  const schedule = () => {
    if (coalesceMs <= 0) { run(); return }
    if (pending !== undefined) return
    pending = env.setTimeout(() => { pending = undefined; run() }, coalesceMs)
  }

  const stream = events.length ? env.openStream() : null
  if (stream) {
    for (const name of events) {
      const listener: LiveEventListener = (event) => { if (!when || when(event, name)) schedule() }
      stream.addEventListener(name, listener)
    }
  }
  const poll = pollMs ? env.setInterval(run, pollMs) : undefined
  const onVisibility = () => { if (visible()) schedule() }
  if (refreshOnVisible) env.document.addEventListener('visibilitychange', onVisibility)
  if (immediate) run()

  return () => {
    stopped = true
    if (pending !== undefined) env.clearTimeout(pending)
    if (poll !== undefined) env.clearInterval(poll)
    if (refreshOnVisible) env.document.removeEventListener('visibilitychange', onVisibility)
    stream?.close()
  }
}

/**
 * React binding. `refresh` and `when` may change every render; the latest
 * ones are always used. The subscription restarts only when the options do.
 */
export function useLiveRefresh(refresh: () => unknown, options: LiveRefreshOptions = {}): void {
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  const whenRef = useRef(options.when)
  whenRef.current = options.when
  const { events = [], coalesceMs, pollMs, onlyWhenVisible, refreshOnVisible, immediate, enabled = true } = options
  const hasWhen = options.when !== undefined
  const eventKey = events.join('\n')

  useEffect(() => {
    if (!enabled) return
    return startLiveRefresh(() => refreshRef.current(), {
      events: eventKey ? eventKey.split('\n') : [],
      when: hasWhen ? (event, name) => whenRef.current?.(event, name) ?? true : undefined,
      coalesceMs,
      pollMs,
      onlyWhenVisible,
      refreshOnVisible,
      immediate,
    })
  }, [enabled, eventKey, hasWhen, coalesceMs, pollMs, onlyWhenVisible, refreshOnVisible, immediate])
}

/** Run a handler for live events that carry data, e.g. progress patches. */
export function useLiveEvent(events: readonly string[], handler: LiveEventListener): void {
  const handlerRef = useRef(handler)
  handlerRef.current = handler
  const eventKey = events.join('\n')

  useEffect(() => {
    if (!eventKey) return
    const stream = openLiveEvents()
    const listener: LiveEventListener = (event) => handlerRef.current(event)
    for (const name of eventKey.split('\n')) stream.addEventListener(name, listener)
    return () => stream.close()
  }, [eventKey])
}
