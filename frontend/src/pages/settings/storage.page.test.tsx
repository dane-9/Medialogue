// @vitest-environment jsdom
import { act, cleanup, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { installLiveEvents, renderPage } from '../../test/pageHarness'
import type { StorageRoot } from '../../types'

const api = vi.hoisted(() => ({
  storageRoots: vi.fn(),
  checkStorageRootHealth: vi.fn(),
  remotePathMappings: vi.fn(),
  downloadClients: vi.fn(),
}))
vi.mock('../../api/modules/storage', () => ({
  storageApi: { ...api, librarySummary: async () => ({ unlinked_directories: 0 }) },
}))
vi.mock('../../api/modules/integrations', () => ({
  integrationsApi: { ...api, tmdbConfiguration: async () => ({ enabled: true, api_key_configured: true }) },
}))
vi.mock('./IgnoredFolders', () => ({ IgnoredFolders: () => null }))

import { StorageSettings } from './storage'

const root: StorageRoot = {
  id: 'root-1', name: 'Movies', resolved_root_path: '/movies/Movies', media_type: 'movies',
  library_id: 'library-1', title_count: 1, media_count: 1, enabled: true, missing_grace_checks: 2,
  scanning: false, access_mode: 'read_only', discovery_mode: 'automatic',
  last_health: 'available', last_scan_at: '2026-11-18T04:00:00Z',
}

let live: ReturnType<typeof installLiveEvents>
afterEach(() => { cleanup(); live.uninstall(); vi.resetAllMocks() })

it('shows the saved folders while health and integration requests are pending', async () => {
  live = installLiveEvents()
  let finishList!: (rows: StorageRoot[]) => void
  let finishHealth!: (rows: StorageRoot[]) => void
  api.storageRoots.mockReturnValue(new Promise<StorageRoot[]>((resolve) => { finishList = resolve }))
  api.checkStorageRootHealth.mockReturnValue(new Promise<StorageRoot[]>((resolve) => { finishHealth = resolve }))
  api.remotePathMappings.mockReturnValue(new Promise(() => {}))
  api.downloadClients.mockReturnValue(new Promise(() => {}))

  renderPage(<StorageSettings />)
  expect(screen.getByText('Loading media folders…')).toBeTruthy()
  expect(api.checkStorageRootHealth).not.toHaveBeenCalled()

  act(() => finishList([root]))
  expect(await screen.findByText(root.resolved_root_path)).toBeTruthy()
  expect(api.checkStorageRootHealth).toHaveBeenCalledTimes(1)
  expect(screen.queryByText('Path not reachable')).toBeNull()

  act(() => finishHealth([{ ...root, last_health: 'unavailable' }]))
  expect(await screen.findByText('Path not reachable')).toBeTruthy()
})
