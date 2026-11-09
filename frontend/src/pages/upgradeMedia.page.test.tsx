// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UpgradeCandidate } from '../types'
import { installLiveEvents, renderPage } from '../test/pageHarness'

const api = vi.hoisted(() => ({
  upgradeCandidates: vi.fn(),
  upgradeCandidateCount: vi.fn(),
  downloadClients: vi.fn(),
  qualityProfiles: vi.fn(),
  customFormats: vi.fn(),
  customFormatLayout: vi.fn(),
  markNotAnUpgrade: vi.fn(),
}))
vi.mock('../api/modules/upgrades', () => ({
  upgradesApi: new Proxy({ upgradeCandidates: api.upgradeCandidates, upgradeCandidateCount: api.upgradeCandidateCount, markNotAnUpgrade: api.markNotAnUpgrade }, {
    get: (target, key) => (key in target ? target[key as keyof typeof target] : vi.fn().mockResolvedValue(undefined)),
  }),
}))
vi.mock('../api/modules/integrations', () => ({
  integrationsApi: { downloadClients: api.downloadClients, downloadClientCategories: vi.fn().mockResolvedValue([]) },
}))
vi.mock('../api/modules/settings', () => ({
  settingsApi: { qualityProfiles: api.qualityProfiles, customFormats: api.customFormats, customFormatLayout: api.customFormatLayout },
}))

import { UpgradeMediaPage } from './upgradeMedia'
import { clearListCache } from '../lib/listCache'

function candidate(overrides: Partial<UpgradeCandidate> = {}): UpgradeCandidate {
  return {
    id: 'cand-1', mediaType: 'movies', movieId: 'movie-1', mediaTitle: 'Inception', mediaYear: 2010,
    currentPaths: ['/media/movies/Inception (2010)'], rootName: 'Movies', currentReleaseIds: ['rel-1'],
    currentReleaseName: 'Inception.2010.1080p.WEB-DL-GRP', currentQuality: 'WEBDL-1080p',
    movieHasMultipleEditions: false, currentCustomFormatScore: 10, qualityProfileInherited: true,
    indexerName: 'Prowlarr', title: 'Inception.2010.2160p.UHD.BluRay.REMUX-FGT', quality: 'Remux-2160p',
    resultCount: 3, customFormatScore: 120, scoreDelta: 110, size: 60e9, seeders: 25, upgradeTypes: ['quality'],
    addedCustomFormats: [], currentReleases: [], currentMatchedFormats: [], candidateMatchedFormats: [],
    executionEligible: true, comparisonSnapshot: {}, lastSearchedAt: '2026-11-07T10:00:00Z',
    createdAt: '2026-11-07T10:00:00Z', updatedAt: '2026-11-07T10:00:00Z',
    ...overrides,
  }
}

let live: ReturnType<typeof installLiveEvents>
const text = () => document.body.textContent ?? ''

beforeEach(() => {
  live = installLiveEvents()
  try { window.localStorage.clear() } catch { /* jsdom always has storage */ }
  api.upgradeCandidates.mockResolvedValue([candidate()])
  api.upgradeCandidateCount.mockResolvedValue({ count: 1, readOnlyRoots: [] })
  api.downloadClients.mockResolvedValue([])
  api.qualityProfiles.mockResolvedValue([])
  api.customFormats.mockResolvedValue([])
  api.customFormatLayout.mockResolvedValue([])
})
afterEach(() => { clearListCache(); cleanup(); live.uninstall(); vi.clearAllMocks() })

describe('Upgrade Media page', () => {
  it('lists a ready upgrade with the release it would replace', async () => {
    renderPage(<UpgradeMediaPage />, '/upgrade-media')
    await waitFor(() => expect(text()).toContain('Inception'))
    expect(text()).toContain('REMUX')
    expect(text()).toContain('WEB-DL')
  })

  it('rejects the offered release from a right-click on the upgrade side', async () => {
    api.markNotAnUpgrade.mockResolvedValue({ next_title: 'Inception.2010.2160p.WEB-DL-NEXT' })
    renderPage(<UpgradeMediaPage />, '/upgrade-media')
    await waitFor(() => expect(text()).toContain('Inception'))
    fireEvent.contextMenu(document.querySelector('.upgrade-v2-change')!)
    fireEvent.click(screen.getByRole('button', { name: 'Not an upgrade' }))
    await waitFor(() => expect(api.markNotAnUpgrade).toHaveBeenCalledWith('cand-1'))
    expect(await screen.findByText(/Now offering Inception\.2010\.2160p\.WEB-DL-NEXT/)).toBeTruthy()
  })

  it('offers no rejection once an upgrade is under way', async () => {
    api.upgradeCandidates.mockResolvedValue([candidate({ executionState: 'downloading', torrentId: 'torrent-9', downloadProgress: 0.2 })])
    renderPage(<UpgradeMediaPage />, '/upgrade-media')
    await screen.findByText('20%')
    fireEvent.contextMenu(document.querySelector('.upgrade-v2-change')!)
    expect(screen.queryByRole('button', { name: 'Not an upgrade' })).toBeNull()
  })

  it('opens on the in-progress list when that is all there is, and patches live progress', async () => {
    api.upgradeCandidates.mockResolvedValue([candidate({ executionState: 'downloading', torrentId: 'torrent-9', downloadProgress: 0.2 })])
    renderPage(<UpgradeMediaPage />, '/upgrade-media')
    expect(await screen.findByText('20%')).toBeTruthy()
    const reads = api.upgradeCandidates.mock.calls.length
    act(() => live.emit('download.progress', { data: { torrent_id: 'torrent-9', progress: 0.65 } }))
    expect(await screen.findByText('65%')).toBeTruthy()
    expect(api.upgradeCandidates.mock.calls.length).toBe(reads)
  })

  it('re-reads only the list and its count when an upgrade changes state', async () => {
    renderPage(<UpgradeMediaPage />, '/upgrade-media')
    await waitFor(() => expect(text()).toContain('Inception'))
    const clientReads = api.downloadClients.mock.calls.length
    api.upgradeCandidates.mockResolvedValue([candidate({ executionState: 'downloading', torrentId: 'torrent-9', downloadProgress: 0.5 })])
    act(() => live.emit('upgrade.execution'))
    expect(await screen.findByText('50%', {}, { timeout: 3000 })).toBeTruthy()
    expect(api.downloadClients.mock.calls.length).toBe(clientReads)
  })

  it('shows why the list could not be read', async () => {
    api.upgradeCandidates.mockRejectedValue(new Error('Upgrade search is unavailable'))
    renderPage(<UpgradeMediaPage />, '/upgrade-media')
    expect(await screen.findByText('Upgrade search is unavailable')).toBeTruthy()
  })
})
