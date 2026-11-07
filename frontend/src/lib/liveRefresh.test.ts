import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startLiveRefresh } from './liveRefresh'
import type { LiveRefreshEnvironment } from './liveRefresh'
import type { LiveEventListener, LiveEventSubscription } from './liveEvents'

function harness(initialVisibility: DocumentVisibilityState = 'visible') {
  const listeners = new Map<string, LiveEventListener[]>()
  let closed = 0
  const doc = new EventTarget() as EventTarget & { visibilityState: DocumentVisibilityState }
  doc.visibilityState = initialVisibility
  const stream: LiveEventSubscription = {
    addEventListener: (name, fn) => { listeners.set(name, [...(listeners.get(name) ?? []), fn]) },
    removeEventListener: () => undefined,
    close: () => { closed += 1 },
  }
  const env: LiveRefreshEnvironment = {
    openStream: () => stream,
    document: doc as unknown as LiveRefreshEnvironment['document'],
    setTimeout: (fn, ms) => setTimeout(fn, ms) as unknown as number,
    clearTimeout: (id) => clearTimeout(id),
    setInterval: (fn, ms) => setInterval(fn, ms) as unknown as number,
    clearInterval: (id) => clearInterval(id),
  }
  return {
    env,
    emit: (name: string, data = '{}') => listeners.get(name)?.forEach((fn) => fn(new MessageEvent(name, { data }))),
    setVisibility: (state: DocumentVisibilityState) => { doc.visibilityState = state; doc.dispatchEvent(new Event('visibilitychange')) },
    closed: () => closed,
    subscribed: () => [...listeners.keys()],
  }
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('startLiveRefresh', () => {
  it('refreshes on each listed event when not coalescing', () => {
    const h = harness()
    const refresh = vi.fn()
    const stop = startLiveRefresh(refresh, { events: ['a', 'b'] }, h.env)
    h.emit('a'); h.emit('b'); h.emit('c')
    expect(refresh).toHaveBeenCalledTimes(2)
    stop()
  })

  it('coalesces a burst into one refresh at the end of the window', () => {
    const h = harness()
    const refresh = vi.fn()
    const stop = startLiveRefresh(refresh, { events: ['a'], coalesceMs: 500 }, h.env)
    h.emit('a'); h.emit('a'); h.emit('a')
    expect(refresh).not.toHaveBeenCalled()
    vi.advanceTimersByTime(500)
    expect(refresh).toHaveBeenCalledTimes(1)
    h.emit('a')
    vi.advanceTimersByTime(500)
    expect(refresh).toHaveBeenCalledTimes(2)
    stop()
  })

  it('ignores events the gate rejects', () => {
    const h = harness()
    const refresh = vi.fn()
    const stop = startLiveRefresh(refresh, { events: ['job.status'], when: (event) => event.data === 'yes' }, h.env)
    h.emit('job.status', 'no'); h.emit('job.status', 'yes')
    expect(refresh).toHaveBeenCalledTimes(1)
    stop()
  })

  it('polls, but not while hidden unless asked to', () => {
    const h = harness()
    const visibleOnly = vi.fn()
    const always = vi.fn()
    const stopA = startLiveRefresh(visibleOnly, { pollMs: 1000 }, h.env)
    const stopB = startLiveRefresh(always, { pollMs: 1000, onlyWhenVisible: false }, h.env)
    vi.advanceTimersByTime(1000)
    h.setVisibility('hidden')
    vi.advanceTimersByTime(2000)
    expect(visibleOnly).toHaveBeenCalledTimes(1)
    expect(always).toHaveBeenCalledTimes(3)
    stopA(); stopB()
  })

  it('refreshes when the tab comes back into view', () => {
    const h = harness('hidden')
    const refresh = vi.fn()
    const stop = startLiveRefresh(refresh, { events: ['a'] }, h.env)
    h.emit('a')
    expect(refresh).not.toHaveBeenCalled()
    h.setVisibility('visible')
    expect(refresh).toHaveBeenCalledTimes(1)
    stop()
  })

  it('runs immediately when asked', () => {
    const h = harness()
    const refresh = vi.fn()
    const stop = startLiveRefresh(refresh, { immediate: true }, h.env)
    expect(refresh).toHaveBeenCalledTimes(1)
    stop()
  })

  it('opens no stream without events, and stops everything on stop', () => {
    const h = harness()
    const refresh = vi.fn()
    startLiveRefresh(refresh, { pollMs: 1000 }, h.env)()
    expect(h.subscribed()).toEqual([])
    const stop = startLiveRefresh(refresh, { events: ['a'], coalesceMs: 100, pollMs: 1000 }, h.env)
    h.emit('a')
    stop()
    vi.advanceTimersByTime(5000)
    h.setVisibility('hidden'); h.setVisibility('visible')
    expect(refresh).not.toHaveBeenCalled()
    expect(h.closed()).toBe(1)
  })
})
