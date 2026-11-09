// @vitest-environment jsdom
import { act, cleanup, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installLiveEvents, renderPage } from '../test/pageHarness'
import { clearListCache } from '../lib/listCache'

const api = vi.hoisted(() => ({ movies: vi.fn(), tags: vi.fn(), qualityProfiles: vi.fn() }))
vi.mock('../api/modules/movies', () => ({ moviesApi: { movies: api.movies, tags: api.tags } }))
vi.mock('../api/modules/settings', () => ({ settingsApi: { qualityProfiles: api.qualityProfiles } }))

import { MoviesPage } from './movies'

let live: ReturnType<typeof installLiveEvents>

beforeEach(() => {
  live = installLiveEvents()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  api.movies.mockResolvedValue([{ id: 'movie-1', resourceId: '1', title: 'Inception', year: 2010, state: 'Present', tags: [], problems: 0, torrentStatus: { state: 'seeding', trackerState: 'healthy' } }])
  api.tags.mockResolvedValue([])
  api.qualityProfiles.mockResolvedValue([])
})
afterEach(() => { cleanup(); clearListCache(); live.uninstall(); vi.clearAllMocks(); vi.unstubAllGlobals() })

describe('Movies page', () => {
  it('re-reads the library when it changes, but not for download progress', async () => {
    renderPage(<MoviesPage />, '/movies')
    await waitFor(() => expect(api.movies).toHaveBeenCalledTimes(1))
    await screen.findAllByText('Inception')
    act(() => live.emit('download.progress', { data: { torrent_id: 't', progress: 0.5 } }))
    await new Promise((resolve) => { setTimeout(resolve, 650) })
    expect(api.movies).toHaveBeenCalledTimes(1)
    act(() => live.emit('media.present'))
    await waitFor(() => expect(api.movies).toHaveBeenCalledTimes(2), { timeout: 2000 })
  })
})
