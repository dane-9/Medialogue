import { describe, expect, it } from 'vitest'
import { downloadPayloadGroups } from './pages'
import type { Download } from './types'

function torrent(id: string, trackerState: Download['trackerState']): Download {
  return {
    id,
    infoHash: id.repeat(40).slice(0, 40),
    name: 'Shared.Release.2026.1080p.WEB-DL-GRP',
    client: 'qbit',
    clientId: 'client',
    kind: 'Movie',
    state: 'Seeding',
    activity: 'seeding',
    progress: 100,
    size: '1 GiB',
    eta: '-',
    speed: '-',
    path: '/movies/Shared Release',
    payloadPath: '/movies/Shared Release',
    releaseId: 'release',
    uploadSpeed: '-',
    downloadSpeedBytes: 0,
    uploadSpeedBytes: 0,
    sizeBytes: 1024,
    etaSeconds: Number.MAX_SAFE_INTEGER,
    archiveState: 'archived',
    trackerState,
  }
}

describe('downloadPayloadGroups', () => {
  it('renders cross-seeds as one healthy payload while any source remains healthy', () => {
    const unassociatedHealthyCrossSeed = torrent('b', 'healthy')
    delete unassociatedHealthyCrossSeed.releaseId
    const groups = downloadPayloadGroups([
      torrent('a', 'unregistered'),
      unassociatedHealthyCrossSeed,
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].members).toHaveLength(2)
    expect(groups[0].download.trackerState).toBe('healthy')
    expect(groups[0].download.state).toBe('Seeding')
  })

  it('reports the payload unavailable only after every cross-seed is unavailable', () => {
    const groups = downloadPayloadGroups([
      torrent('a', 'unregistered'),
      torrent('b', 'unregistered'),
    ])

    expect(groups[0].download.trackerState).toBe('unregistered')
  })
})
