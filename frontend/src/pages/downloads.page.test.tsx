// @vitest-environment jsdom
import { act, cleanup, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Download, DownloadClient } from '../types'
import { installLiveEvents, renderPage } from '../test/pageHarness'

const api = vi.hoisted(() => ({
  downloads: vi.fn(),
  downloadFacets: vi.fn(),
  applyDownloadAction: vi.fn(),
  downloadClients: vi.fn(),
}))
vi.mock('../api/modules/downloads', () => ({
  downloadsApi: { downloads: api.downloads, downloadFacets: api.downloadFacets, applyDownloadAction: api.applyDownloadAction },
}))
vi.mock('../api/modules/integrations', () => ({ integrationsApi: { downloadClients: api.downloadClients } }))

import { DownloadsPage } from './downloads'

const client: DownloadClient = {
  id: 'client-1', name: 'Main', url: 'http://qbit', password_configured: true, scope: 'movies', tags: [], enabled: true,
  health: 'healthy', revision: 1, pollIntervalSeconds: 5, recentPriority: 'first', olderPriority: 'last',
  sequentialOrder: false, firstLastFirst: false, contentLayout: 'default', completedDownloadHandling: true,
}

function download(overrides: Partial<Download> = {}): Download {
  return {
    id: 'dl-1', torrentId: 'torrent-1', name: 'Inception.2010.1080p.BluRay-GRP', client: 'Main', clientId: 'client-1',
    kind: 'Movie', state: 'Downloading', activity: 'downloading', progress: 42, size: '10 GB', eta: '5m',
    speed: '10 MB/s', path: '/downloads/movies', uploadSpeed: '0 B/s', downloadSpeedBytes: 10_000_000,
    uploadSpeedBytes: 0, sizeBytes: 10_000_000_000, etaSeconds: 300, archiveState: 'archived', trackerState: 'healthy',
    ...overrides,
  }
}

let live: ReturnType<typeof installLiveEvents>

beforeEach(() => {
  live = installLiveEvents()
  try { window.localStorage.clear() } catch { /* jsdom always has storage */ }
  api.downloadClients.mockResolvedValue([client])
  api.downloadFacets.mockResolvedValue({ counts: { all: 1, downloading: 1 }, categories: [], tags: [], trackers: [] })
  api.downloads.mockResolvedValue({ items: [download()], total: 1, pages: 1, page: 1 })
})
afterEach(() => { cleanup(); live.uninstall(); vi.clearAllMocks(); vi.useRealTimers() })

describe('Downloads page', () => {
  it('lists the client\'s torrents with their progress', async () => {
    renderPage(<DownloadsPage />, '/downloads')
    expect(await screen.findByText('Inception.2010.1080p.BluRay-GRP')).toBeTruthy()
    expect(screen.getByText('42%')).toBeTruthy()
  })

  it('patches progress from a live event without reloading the list', async () => {
    renderPage(<DownloadsPage />, '/downloads')
    await screen.findByText('42%')
    const reads = api.downloads.mock.calls.length
    act(() => live.emit('download.progress', { data: { torrent_id: 'torrent-1', client_id: 'client-1', progress: 0.75, state: 'downloading' } }))
    expect(await screen.findByText('75%')).toBeTruthy()
    expect(api.downloads.mock.calls.length).toBe(reads)
  })

  it('ignores progress for a torrent on another client', async () => {
    renderPage(<DownloadsPage />, '/downloads')
    await screen.findByText('42%')
    act(() => live.emit('download.progress', { data: { torrent_id: 'torrent-1', client_id: 'other-client', progress: 0.9 } }))
    expect(screen.queryByText('90%')).toBeNull()
    expect(screen.getByText('42%')).toBeTruthy()
  })

  it('reloads once after a burst of structural events', async () => {
    renderPage(<DownloadsPage />, '/downloads')
    await screen.findByText('42%')
    const reads = api.downloads.mock.calls.length
    api.downloads.mockResolvedValue({ items: [download({ progress: 100, state: 'Seeding', activity: 'seeding' })], total: 1, pages: 1, page: 1 })
    act(() => { live.emit('download.completed'); live.emit('torrent.detected'); live.emit('download.completed') })
    expect(await screen.findByText('100%', {}, { timeout: 3000 })).toBeTruthy()
    expect(api.downloads.mock.calls.length).toBe(reads + 1)
  })

  it('says so when the list cannot be read, and retries', async () => {
    api.downloads.mockRejectedValue(new Error('qBittorrent is not answering'))
    renderPage(<DownloadsPage />, '/downloads')
    expect(await screen.findByText('qBittorrent is not answering')).toBeTruthy()
    api.downloads.mockResolvedValue({ items: [download()], total: 1, pages: 1, page: 1 })
    act(() => screen.getByRole('button', { name: /Try again/ }).click())
    await waitFor(() => expect(screen.getByText('42%')).toBeTruthy())
  })
})
