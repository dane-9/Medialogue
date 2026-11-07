// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/transport'

const api = vi.hoisted(() => ({ deletePlan: vi.fn(), deleteDownloads: vi.fn() }))
vi.mock('../api/modules/downloads', () => ({
  downloadsApi: { deletePlan: api.deletePlan, deleteDownloads: api.deleteDownloads },
}))

import { DeleteDownloadsModal } from './DeleteDownloadsModal'

const plan = {
  folders: ['/media/movies/Gone (2020)'],
  torrents: [{ observation_id: 'dl-1', torrent_id: 't-1', client_name: 'Main', info_hash: 'a'.repeat(40), name: 'Gone', selected: true }],
  titles: [{ kind: 'movie', title: 'Gone', year: 2020, season_numbers: [] }],
  blocked: [],
}

beforeEach(() => { api.deletePlan.mockResolvedValue(plan) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function clickDelete() {
  const button = await screen.findByRole('button', { name: /^Delete$/ })
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(button)
}

describe('DeleteDownloadsModal', () => {
  it('finishes with an error notice when some files could not be deleted', async () => {
    const message = 'The torrents were removed, but some files could not be deleted: /media/movies/Gone (2020): Permission denied'
    api.deleteDownloads.mockRejectedValue(new ApiError(message, 500, 'DELETE_INCOMPLETE'))
    const onDone = vi.fn()
    render(<DeleteDownloadsModal downloadIds={['dl-1']} onClose={vi.fn()} onDone={onDone} />)
    await clickDelete()
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(message, 'error'))
  })

  it('stays open on other failures so the delete can be retried', async () => {
    api.deleteDownloads.mockRejectedValue(new ApiError('Main could not remove the torrents', 502, 'UPSTREAM_ERROR'))
    const onDone = vi.fn()
    render(<DeleteDownloadsModal downloadIds={['dl-1']} onClose={vi.fn()} onDone={onDone} />)
    await clickDelete()
    expect(await screen.findByText('Main could not remove the torrents')).toBeTruthy()
    expect(onDone).not.toHaveBeenCalled()
  })
})
