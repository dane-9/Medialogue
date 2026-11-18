// @vitest-environment jsdom
import { act, cleanup, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installLiveEvents, renderPage } from '../test/pageHarness'
import { clearListCache } from '../lib/listCache'

const api = vi.hoisted(() => ({ movies: vi.fn(), firstMoviePage: vi.fn(), tags: vi.fn(), qualityProfiles: vi.fn() }))
vi.mock('../api/modules/movies', () => ({ moviesApi: { movies: api.movies, firstMoviePage: api.firstMoviePage, tags: api.tags, movieEditionOptions: () => Promise.resolve([]) } }))
vi.mock('../api/modules/settings', () => ({ settingsApi: { qualityProfiles: api.qualityProfiles } }))

import { MoviesPage } from './movies'

let live: ReturnType<typeof installLiveEvents>
const inception = { id: 'movie-1', resourceId: '1', title: 'Inception', year: 2010, state: 'Present', tags: [], problems: 0, torrentStatus: { state: 'seeding', trackerState: 'healthy' } }

beforeEach(() => {
  live = installLiveEvents()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  api.movies.mockResolvedValue([inception])
  api.firstMoviePage.mockResolvedValue({ items: [inception], total: 1 })
  api.tags.mockResolvedValue([])
  api.qualityProfiles.mockResolvedValue([])
})
afterEach(() => { cleanup(); clearListCache(); live.uninstall(); vi.clearAllMocks(); vi.unstubAllGlobals() })

describe('Movies page', () => {
  it('shows the first page while the full library loads', async () => {
    api.firstMoviePage.mockResolvedValue({ items: [inception], total: 2 })
    let finish = (_items: unknown) => {}
    api.movies.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    renderPage(<MoviesPage />, '/movies')
    await screen.findAllByText('Inception')
    expect(screen.getByText('Loading more movies…')).toBeTruthy()
    expect(screen.queryByText('Memento')).toBeNull()

    act(() => finish([inception, { ...inception, id: 'movie-2', title: 'Memento' }]))
    await screen.findAllByText('Memento')
    expect(screen.queryByText('Loading more movies…')).toBeNull()
  })

  it('re-reads the library when it changes, but not for download progress', async () => {
    renderPage(<MoviesPage />, '/movies')
    await screen.findAllByText('Inception')
    expect(api.firstMoviePage).toHaveBeenCalledTimes(1)
    act(() => live.emit('download.progress', { data: { torrent_id: 't', progress: 0.5 } }))
    await new Promise((resolve) => { setTimeout(resolve, 650) })
    expect(api.movies).toHaveBeenCalledTimes(0)
    act(() => live.emit('media.present'))
    await waitFor(() => expect(api.movies).toHaveBeenCalledTimes(1), { timeout: 2000 })
    // The server announces a rebuilt list; the page picks it up.
    act(() => live.emit('results.movies'))
    await waitFor(() => expect(api.movies).toHaveBeenCalledTimes(2), { timeout: 2000 })
  })
})
