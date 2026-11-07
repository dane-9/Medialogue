// @vitest-environment jsdom
import { act, cleanup, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReplacementItem } from './api/modules/replacements'
import { installLiveEvents, renderPage } from './test/pageHarness'

const { api, stub } = vi.hoisted(() => ({
  api: { queue: vi.fn(), downloadClients: vi.fn() },
  // Every API call the test does not name resolves to nothing.
  stub: (known: Record<string, unknown>) => new Proxy(known, {
    get: (target, key) => (key in target ? target[key as string] : vi.fn().mockResolvedValue(undefined)),
  }),
}))
vi.mock('./api/modules/replacements', () => ({ replacementsApi: stub({ queue: api.queue }) }))
vi.mock('./api/modules/integrations', () => ({ integrationsApi: { downloadClients: api.downloadClients } }))
vi.mock('./api/modules/upgrades', () => ({ upgradesApi: stub({}) }))
vi.mock('./api/modules/system', () => ({ systemApi: stub({}) }))

import ReplaceMediaPage from './ReplaceMediaPage'

function item(overrides: Partial<ReplacementItem> = {}): ReplacementItem {
  return {
    target_key: 'movie:1', title: 'Inception', year: 2010, media_type: 'movies', movie_id: 'movie-1',
    reason: 'The tracker no longer lists this torrent.', reason_code: 'removed_from_tracker', tab: 'needs_search',
    dismissed: false, needs_attention: false, readdable: false, searching: false,
    current_release: 'Inception.2010.1080p.BluRay-GRP', current_quality: 'Bluray-1080p',
    ...overrides,
  }
}

let live: ReturnType<typeof installLiveEvents>
const text = () => document.body.textContent ?? ''
const jobStatus = (status: string, percent?: number, jobType = 'search_media_replacements') => ({
  event: 'job.status', entity_type: 'job', entity_id: 'job-1', timestamp: '2026-11-07T10:00:00Z',
  data: {
    job_id: 'job-1', job_type: jobType, status, progress: percent === undefined ? {} : { percent }, error: null,
    cancellable: false, revision: 1, created_at: null, started_at: null, finished_at: null, updated_at: null,
  },
})

beforeEach(() => {
  live = installLiveEvents()
  try { window.localStorage.clear() } catch { /* jsdom always has storage */ }
  api.queue.mockResolvedValue({ items: [item()] })
  api.downloadClients.mockResolvedValue([])
})
afterEach(() => { cleanup(); live.uninstall(); vi.clearAllMocks() })

describe('Replace Media page', () => {
  it('lists a title whose source was lost', async () => {
    renderPage(<ReplaceMediaPage />, '/replace-media')
    await waitFor(() => expect(text()).toContain('Inception'))
    expect(api.queue).toHaveBeenCalledTimes(1)
  })

  it('shows bulk search progress, then reloads once the search finishes', async () => {
    renderPage(<ReplaceMediaPage />, '/replace-media')
    await waitFor(() => expect(text()).toContain('Inception'))
    act(() => live.emit('job.status', jobStatus('running', 40)))
    expect(await screen.findByText(/Searching all titles · 40%/)).toBeTruthy()
    // Progress alone never reloads the list.
    expect(api.queue).toHaveBeenCalledTimes(1)
    act(() => live.emit('job.status', jobStatus('completed')))
    await waitFor(() => expect(screen.queryByText(/Searching all titles/)).toBeNull())
    await waitFor(() => expect(api.queue).toHaveBeenCalledTimes(2), { timeout: 3000 })
  })

  it('ignores other jobs', async () => {
    renderPage(<ReplaceMediaPage />, '/replace-media')
    await waitFor(() => expect(text()).toContain('Inception'))
    act(() => live.emit('job.status', jobStatus('completed', undefined, 'storage_root_scan')))
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(api.queue).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/Searching all titles/)).toBeNull()
  })

  it('only reloads on download progress while something is downloading', async () => {
    renderPage(<ReplaceMediaPage />, '/replace-media')
    await waitFor(() => expect(text()).toContain('Inception'))
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      act(() => live.emit('download.progress', { data: { torrent_id: 't', progress: 0.5 } }))
      await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
      expect(api.queue).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })
})
