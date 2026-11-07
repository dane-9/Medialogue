import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadHiddenReasons, reasonShown, replaceReasonDetail, saveHiddenReasons } from './replaceReason'

describe('replaceReasonDetail', () => {
  const twoDaysAgo = new Date(Date.now() - 2 * 24 * 3600 * 1000).toISOString()

  it('says when the problem started', () => {
    expect(replaceReasonDetail('not_watched', twoDaysAgo).note).toBe('Left qBittorrent 2 days ago')
    expect(replaceReasonDetail('no_seeders', twoDaysAgo).note).toBe('Only seeder for 2 days')
    expect(replaceReasonDetail('tracker_gone', twoDaysAgo).note).toBe('Last answered 2 days ago')
  })

  it('falls back to plain words without a date', () => {
    expect(replaceReasonDetail('not_watched').note).toBe('Not in any qBittorrent client')
    expect(replaceReasonDetail('no_torrent').note).toBe('Never linked to a torrent')
    expect(replaceReasonDetail('not_watched', twoDaysAgo, false).note).toBe('Left qBittorrent 2 days ago · not archived')
  })
})

describe('reason filter', () => {
  it('hides switched-off reasons but always shows rows without one', () => {
    const hidden = new Set(['no_torrent'] as const)
    expect(reasonShown('no_torrent', hidden)).toBe(false)
    expect(reasonShown('not_watched', hidden)).toBe(true)
    expect(reasonShown(null, hidden)).toBe(true)
  })

  afterEach(() => { vi.unstubAllGlobals() })

  it('remembers the choice and drops unknown codes', () => {
    const store = new Map<string, string>()
    vi.stubGlobal('window', { localStorage: { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => store.set(key, value) } })
    saveHiddenReasons(new Set(['tracker_gone']))
    expect([...loadHiddenReasons()]).toEqual(['tracker_gone'])
    window.localStorage.setItem('medialogue.replace.hiddenReasons', '["bogus","no_seeders"]')
    expect([...loadHiddenReasons()]).toEqual(['no_seeders'])
    window.localStorage.setItem('medialogue.replace.hiddenReasons', 'not json')
    expect(loadHiddenReasons().size).toBe(0)
  })
})

describe('reason filter without storage', () => {
  it('starts with every reason on', () => {
    vi.stubGlobal('window', { localStorage: { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } } })
    expect(loadHiddenReasons().size).toBe(0)
    expect(() => saveHiddenReasons(new Set(['no_torrent']))).not.toThrow()
    vi.unstubAllGlobals()
  })
})
