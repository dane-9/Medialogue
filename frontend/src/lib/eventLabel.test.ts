import { describe, expect, it } from 'vitest'
import { eventLabel, eventStamp } from './eventLabel'

describe('eventLabel', () => {
  it('names the types the app emits', () => {
    expect(eventLabel('storage_root.unavailable')).toBe('Folder unavailable')
    expect(eventLabel('release.replaced')).toBe('Release replaced')
  })

  it('renders an unknown type readably rather than hiding it', () => {
    expect(eventLabel('widget.exploded_twice')).toBe('Widget exploded twice')
  })

  it('survives an empty type', () => {
    expect(eventLabel('')).toBe('Event')
  })
})

describe('eventStamp', () => {
  it('carries the date as well as the time, since rows can be reordered', () => {
    const stamp = eventStamp(new Date(2026, 7, 27, 14, 32).toISOString())
    expect(stamp).toContain('27')
    expect(stamp).toMatch(/\d{2}:\d{2}/)
  })

  it('names the year only when it is not the current one', () => {
    expect(eventStamp(new Date(2019, 0, 5, 9, 0).toISOString())).toContain('2019')
    expect(eventStamp(new Date().toISOString())).not.toContain(String(new Date().getFullYear()))
  })

  it('does not throw on a malformed timestamp', () => {
    expect(eventStamp('not a date')).toBe('—')
  })
})
