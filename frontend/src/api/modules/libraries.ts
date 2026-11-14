import type { Library } from '../../types'
import type { components } from '../generated/schema'
import { openApiClient, openApiData } from '../transport'

type LibraryDto = components['schemas']['LibraryResponse']

export function mapLibrary(value: LibraryDto): Library {
  return {
    id: value.id,
    name: value.name,
    kind: value.kind,
    sortOrder: value.sort_order,
    folderCount: value.folder_count ?? 0,
    titleCount: value.title_count ?? 0,
  }
}

export const librariesApi = {
  libraries: async () => (await openApiData(openApiClient.GET('/api/v1/libraries'))).map(mapLibrary),
  create: async (name: string, kind: Library['kind']) => mapLibrary(await openApiData(openApiClient.POST('/api/v1/libraries', { body: { name, kind } }))),
  update: async (id: string, changes: { name?: string; kind?: Library['kind'] }) => mapLibrary(await openApiData(openApiClient.PATCH('/api/v1/libraries/{library_id}', { params: { path: { library_id: id } }, body: changes }))),
  remove: (id: string) => openApiData(openApiClient.DELETE('/api/v1/libraries/{library_id}', { params: { path: { library_id: id } } })),
  reorder: async (ids: string[]) => (await openApiData(openApiClient.POST('/api/v1/libraries/reorder', { body: { ids } }))).map(mapLibrary),
  setFolders: async (id: string, storageRootIds: string[]) => mapLibrary(await openApiData(openApiClient.PUT('/api/v1/libraries/{library_id}/folders', { params: { path: { library_id: id } }, body: { storage_root_ids: storageRootIds } }))),
  scan: async (id: string) => (await openApiData(openApiClient.POST('/api/v1/libraries/{library_id}/scan', { params: { path: { library_id: id } } }))).job_ids,
}
