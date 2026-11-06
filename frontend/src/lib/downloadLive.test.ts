import { describe, expect, it } from 'vitest'
import { activityFor, applyProgressUpdate } from '../api/mappers/downloads'
import type { Download } from '../types'

const row = (overrides: Partial<Download> = {}): Download => ({
  id: 'obs', torrentId: 't1', name: 'X', client: 'q', clientId: 'c1', kind: 'Movie',
  state: 'Downloading', activity: 'downloading', progress: 40, size: '1 GB', eta: '1m', speed: '-', path: '/',
  uploadSpeed: '-', downloadSpeedBytes: 0, uploadSpeedBytes: 0, sizeBytes: 0, etaSeconds: 0,
  archiveState: 'archived', trackerState: 'healthy', ...overrides,
} as Download)

describe('live download updates', () => {
  it('mirrors the backend activity rules', () => {
    expect(activityFor('downloading', 0.5)).toBe('downloading')
    expect(activityFor('stalledDL', 0.5)).toBe('stalled')
    expect(activityFor('uploading', 1)).toBe('seeding')
    expect(activityFor('moving', 1)).toBe('moving')
    expect(activityFor('forcedMetaDL', 0)).toBe('metadata')
    expect(activityFor('missingFiles', 0.3)).toBe('error')
    expect(activityFor('stoppedDL', 0.2)).toBe('paused')
  })

  it('patches progress and state from an event without a reload', () => {
    const updated = applyProgressUpdate(row(), { state: 'uploading', progress: 1 })
    expect(updated.progress).toBe(100)
    expect(updated.activity).toBe('seeding')
    expect(updated.state).toBe('Seeding')
  })

  it('leaves stale rows alone', () => {
    const stale = row({ stale: true })
    expect(applyProgressUpdate(stale, { state: 'uploading', progress: 1 })).toBe(stale)
  })
})
