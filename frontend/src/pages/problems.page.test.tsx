// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Movie, Problem } from '../types'
import { installLiveEvents, renderPage } from '../test/pageHarness'

const api = vi.hoisted(() => ({
  problemsPage: vi.fn(),
  problemSummary: vi.fn(),
  resolveProblem: vi.fn(),
  movie: vi.fn(),
}))
vi.mock('../api/modules/problems', () => ({
  problemsApi: { problemsPage: api.problemsPage, problemSummary: api.problemSummary, resolveProblem: api.resolveProblem, deleteProblem: vi.fn(), recheckProblems: vi.fn() },
}))
vi.mock('../api/modules/movies', () => ({
  moviesApi: { movie: api.movie, movieEditionOptions: vi.fn().mockResolvedValue([]), lookupMovies: vi.fn().mockResolvedValue([]), updateMovieReleaseEditions: vi.fn() },
}))
vi.mock('../api/modules/shows', () => ({ showsApi: { lookupShows: vi.fn().mockResolvedValue([]) } }))
vi.mock('../api/modules/integrations', () => ({ integrationsApi: { plexConfiguration: vi.fn().mockResolvedValue(null) } }))
vi.mock('../api/modules/system', () => ({ systemApi: { waitForJobs: vi.fn() } }))

import { ProblemsPage } from './problems'

function problem(overrides: Partial<Problem>): Problem {
  return {
    id: 'problem-1', code: 'AMBIGUOUS_REPLACEMENT_TARGET', reason: 'AMBIGUOUS_REPLACEMENT_TARGET',
    title: 'Medialogue cannot tell which movie release to replace', subject: 'Inception (2010)', detail: 'Pick one.',
    severity: 'medium', created: 'now', status: 'open', workflow: 'waiting', entityType: 'movie', entityId: 'movie-1',
    details: {}, availableActions: ['choose_replacement_target', 'dismiss', 'recheck'],
    ...overrides,
  } as Problem
}

const release = (id: string, edition: string) => ({
  id, name: `Inception.2010.${edition}`, edition, editionOverrideActive: false, state: 'missing', confidence: 100,
  firstSeenAt: '', directories: [{ path: `/movies/gone-${edition}` }], torrentStatus: {},
})

let live: ReturnType<typeof installLiveEvents>

beforeEach(() => {
  live = installLiveEvents()
  api.problemSummary.mockResolvedValue({ open: 1, suppressed: 0, workflows: {} })
})
afterEach(() => { cleanup(); live.uninstall(); vi.clearAllMocks() })

describe('Problems page', () => {
  it('lets the administrator choose which missing release a download replaces', async () => {
    api.problemsPage.mockResolvedValue({ items: [problem({ details: { candidate_release_ids: ['r-imax', 'r-ext'], path: '/movies/Inception.2010.Directors.Cut' } })], total: 1, pages: 1, page: 1, pageSize: 100 })
    api.movie.mockResolvedValue({ id: 'movie-1', title: 'Inception', year: 2010, releasesDetail: [release('r-imax', 'IMAX'), release('r-ext', 'Extended Cut'), release('r-current', 'Theatrical')] } as unknown as Movie)
    api.resolveProblem.mockResolvedValue(problem({ status: 'resolved' }))

    renderPage(<ProblemsPage />, '/problems')
    const extended = await screen.findByRole('radio', { name: /Extended Cut/ })
    // Only the releases the Problem lists are offered.
    expect(screen.queryByRole('radio', { name: /Theatrical/ })).toBeNull()
    const replace = screen.getByRole('button', { name: 'Replace this release' }) as HTMLButtonElement
    expect(replace.disabled).toBe(true)

    fireEvent.click(extended)
    fireEvent.click(replace)
    await waitFor(() => expect(api.resolveProblem).toHaveBeenCalledWith('problem-1', 'choose_replacement_target', { release_id: 'r-ext' }))
  })

  it('lists every download a client cannot find under one Problem', async () => {
    api.problemsPage.mockResolvedValue({ items: [problem({
      code: 'TORRENT_PATH_NOT_FOUND', reason: 'TORRENT_PATH_NOT_FOUND', title: 'Medialogue cannot access the completed download',
      subject: 'Main', workflow: 'config', entityType: 'download_client', entityId: 'client-1', availableActions: ['dismiss', 'recheck'],
      details: { client_name: 'Main', count: 2, torrents: [
        { torrent_id: 't-a', name: 'Alpha', reported_path: '/remote/Alpha', resolved_path: '/local/Alpha' },
        { torrent_id: 't-b', name: 'Beta', reported_path: '/remote/Beta', resolved_path: '/local/Beta' },
      ] },
    })], total: 1, pages: 1, page: 1, pageSize: 100 })

    renderPage(<ProblemsPage />, '/problems')
    expect(await screen.findByText('Alpha')).toBeTruthy()
    expect(screen.getByText('Beta')).toBeTruthy()
    expect(screen.getByText('/remote/Alpha')).toBeTruthy()
    expect(screen.getByText('/local/Beta')).toBeTruthy()
    expect(screen.getByText('2 completed downloads not found on disk')).toBeTruthy()
  })
})
