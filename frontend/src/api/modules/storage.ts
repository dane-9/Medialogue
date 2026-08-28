import type { components } from '../generated/schema'
import { mapFilesystemListing, mapLibrarySummary, mapStorageRoot } from '../mappers/storage'
import { openApiClient, openApiData } from '../transport'

type StorageRootCreateDto = components['schemas']['StorageRootCreate']
type StorageRootUpdateDto = components['schemas']['StorageRootUpdate']
type StorageRootReorderDto = components['schemas']['StorageRootReorder']

export const storageApi = {
  storageRoots: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/storage-roots'))
    return payload.items.map(mapStorageRoot)
  },
  createStorageRoot: async (root: StorageRootCreateDto) => mapStorageRoot(await openApiData(openApiClient.POST('/api/v1/storage-roots', { body: root }))),
  updateStorageRoot: async (rootId: string, root: StorageRootUpdateDto) => mapStorageRoot(await openApiData(openApiClient.PATCH('/api/v1/storage-roots/{root_id}', { params: { path: { root_id: rootId } }, body: root }))),
  deleteStorageRoot: (rootId: string, removeEntries = false) => openApiData(openApiClient.DELETE('/api/v1/storage-roots/{root_id}', { params: { path: { root_id: rootId }, query: { remove_entries: removeEntries } } })),
  reorderStorageRoots: async (ids: StorageRootReorderDto['ids']) => {
    const payload = await openApiData(openApiClient.POST('/api/v1/storage-roots/reorder', { body: { ids } }))
    return payload.items.map(mapStorageRoot)
  },
  browseFilesystem: async (path: string) => mapFilesystemListing(await openApiData(openApiClient.GET('/api/v1/filesystem', { params: { query: { path } } }))),
  librarySummary: async () => mapLibrarySummary(await openApiData(openApiClient.GET('/api/v1/library/summary'))),
  checkStorageRootHealth: async () => {
    const payload = await openApiData(openApiClient.POST('/api/v1/storage-roots/health'))
    return payload.items.map(mapStorageRoot)
  },
  startScan: (rootId: string) => openApiData(openApiClient.POST('/api/v1/storage-roots/{root_id}/scan', { params: { path: { root_id: rootId } } })),
}
