import { afterEach, describe, expect, it } from 'vitest'
import { openLiveEvents, setLiveEventSourceFactory } from './liveEvents'

class FakeSource extends EventTarget {
  closed = false
  close() { this.closed = true }
  emit(name: string, data: string) { this.dispatchEvent(new MessageEvent(name, { data })) }
}

function install() {
  const created: FakeSource[] = []
  setLiveEventSourceFactory(() => {
    const fake = new FakeSource()
    created.push(fake)
    return fake as unknown as EventSource
  })
  return created
}

afterEach(() => setLiveEventSourceFactory(null))

describe('openLiveEvents', () => {
  it('shares one connection between subscribers', () => {
    const created = install()
    const a = openLiveEvents()
    const b = openLiveEvents()
    expect(created).toHaveLength(1)
    a.close(); b.close()
  })

  it('delivers events to every subscriber and stops after close', () => {
    const created = install()
    const a = openLiveEvents()
    const b = openLiveEvents()
    const seen: string[] = []
    a.addEventListener('job.status', (event) => { seen.push(`a:${event.data}`) })
    b.addEventListener('job.status', (event) => { seen.push(`b:${event.data}`) })
    created[0].emit('job.status', '1')
    a.close()
    created[0].emit('job.status', '2')
    expect(seen).toEqual(['a:1', 'b:1', 'b:2'])
    b.close()
  })

  it('closes with the last subscriber and reopens on demand', () => {
    const created = install()
    const a = openLiveEvents()
    const b = openLiveEvents()
    a.close()
    expect(created[0].closed).toBe(false)
    a.close() // a second close is a no-op, not a stolen reference
    expect(created[0].closed).toBe(false)
    b.close()
    expect(created[0].closed).toBe(true)
    const c = openLiveEvents()
    expect(created).toHaveLength(2)
    c.close()
  })

  it('removes a single listener', () => {
    const created = install()
    const a = openLiveEvents()
    let count = 0
    const listener = () => { count += 1 }
    a.addEventListener('x', listener)
    created[0].emit('x', '')
    a.removeEventListener('x', listener)
    created[0].emit('x', '')
    expect(count).toBe(1)
    a.close()
  })
})
