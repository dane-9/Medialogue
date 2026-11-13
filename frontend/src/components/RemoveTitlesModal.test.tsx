// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/transport'

const api = vi.hoisted(() => ({ removePlan: vi.fn(), remove: vi.fn() }))
vi.mock('../api/modules/library', () => ({ libraryApi: { removePlan: api.removePlan, remove: api.remove } }))

import { RemoveTitlesModal } from './RemoveTitlesModal'

const plan = (titles = 1, blocked: string[] = []) => ({
  folders: Array.from({ length: titles }, (_, index) => `/media/movies/Movie ${index}`),
  torrents: [
    { download_id: 'dl-1', client_name: 'Main', name: 'Movie.0.1080p', selected: true },
    { download_id: 'dl-2', client_name: 'Main', name: 'Movie.0.1080p', selected: false },
  ],
  titles: Array.from({ length: titles }, (_, index) => ({ kind: 'movie', title: `Movie ${index}`, year: 2020, season_numbers: [] })),
  blocked,
})

beforeEach(() => { api.removePlan.mockResolvedValue(plan()) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

const confirmButton = () => screen.findByRole('button', { name: /^(Delete|Remove) \d+ movies?$/ }) as Promise<HTMLButtonElement>

describe('RemoveTitlesModal', () => {
  it('lists the folder and every torrent, cross-seeds included, before deleting', async () => {
    render(<RemoveTitlesModal kind="movie" ids={['m-1']} onClose={vi.fn()} onDone={vi.fn()} />)
    expect(await screen.findByText('/media/movies/Movie 0')).toBeTruthy()
    expect(screen.getByText('Deleted from disk')).toBeTruthy()
    expect(screen.getByText('Removed from qBittorrent')).toBeTruthy()
    expect(screen.getByText(/cross-seed of the same files/)).toBeTruthy()
  })

  it('deletes and forgets by default', async () => {
    api.remove.mockResolvedValue(plan())
    const onDone = vi.fn()
    render(<RemoveTitlesModal kind="movie" ids={['m-1']} onClose={vi.fn()} onDone={onDone} />)
    const button = await confirmButton()
    await waitFor(() => expect(button.disabled).toBe(false))
    fireEvent.click(button)
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith({ movieIds: ['m-1'] }, 'forget'))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })

  it('keeping the files leaves qBittorrent out of it and is not stopped by a read-only folder', async () => {
    api.removePlan.mockResolvedValue(plan(1, ['/media/movies/Movie 0 is in Movies, which Medialogue may only read.']))
    api.remove.mockResolvedValue(plan())
    render(<RemoveTitlesModal kind="movie" ids={['m-1']} onClose={vi.fn()} onDone={vi.fn()} />)
    expect((await confirmButton()).disabled).toBe(true)
    fireEvent.click(screen.getByRole('radio', { name: /Keep files, remove from Medialogue/ }))
    expect(screen.getByText('Left on disk, skipped by scans')).toBeTruthy()
    expect(screen.queryByText('Removed from qBittorrent')).toBeNull()
    const button = await confirmButton()
    expect(button.disabled).toBe(false)
    fireEvent.click(button)
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith({ movieIds: ['m-1'] }, 'keep_files'))
  })

  it('asks for an acknowledgement before deleting many titles', async () => {
    const ids = Array.from({ length: 6 }, (_, index) => `m-${index}`)
    api.removePlan.mockResolvedValue(plan(6))
    render(<RemoveTitlesModal kind="movie" ids={ids} onClose={vi.fn()} onDone={vi.fn()} />)
    const button = await confirmButton()
    await screen.findByText('/media/movies/Movie 5')
    expect(button.disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(button.disabled).toBe(false)
  })

  it('stays open on a failure so the removal can be retried', async () => {
    api.remove.mockRejectedValue(new ApiError('Main could not remove the torrents', 502, 'UPSTREAM_ERROR'))
    const onDone = vi.fn()
    render(<RemoveTitlesModal kind="movie" ids={['m-1']} onClose={vi.fn()} onDone={onDone} />)
    const button = await confirmButton()
    await waitFor(() => expect(button.disabled).toBe(false))
    fireEvent.click(button)
    expect(await screen.findByText('Main could not remove the torrents')).toBeTruthy()
    expect(onDone).not.toHaveBeenCalled()
  })
})
