import { describe, expect, it } from 'vitest'
import { reuseUnchanged } from './reuseUnchanged'

describe('reuseUnchanged', () => {
  it('returns the previous array when nothing changed', () => {
    const previous = [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }]
    const next = [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }]
    expect(reuseUnchanged(previous, next)).toBe(previous)
  })

  it('keeps unchanged items and takes changed ones', () => {
    const previous = [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }]
    const next = [{ id: 'a', title: 'A' }, { id: 'b', title: 'B2' }]
    const merged = reuseUnchanged(previous, next)
    expect(merged).not.toBe(previous)
    expect(merged[0]).toBe(previous[0])
    expect(merged[1]).toBe(next[1])
  })

  it('follows a new order and new or removed items', () => {
    const previous = [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }]
    const reordered = reuseUnchanged(previous, [{ id: 'b', title: 'B' }, { id: 'a', title: 'A' }])
    expect(reordered).toEqual([previous[1], previous[0]])
    expect(reordered[0]).toBe(previous[1])
    expect(reuseUnchanged(previous, [{ id: 'a', title: 'A' }])).toEqual([previous[0]])
    expect(reuseUnchanged(previous, [...previous, { id: 'c', title: 'C' }])).toHaveLength(3)
  })
})
