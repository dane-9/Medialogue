import { describe, expect, it } from 'vitest'
import { replaceReasonDetail } from './replaceReason'

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
  })
})
