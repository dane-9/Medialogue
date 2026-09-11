import type { BlocklistEntry } from '../../types'
import { openApiClient, openApiData } from '../transport'

const mapEntry = (value: Awaited<ReturnType<typeof rawEntries>>['items'][number]): BlocklistEntry => ({
  id: value.id,
  mediaType: value.media_type,
  targetEntityType: value.target_entity_type,
  targetEntityId: value.target_entity_id ?? undefined,
  mediaResourceId: value.media_resource_id ?? undefined,
  mediaTitle: value.media_title ?? undefined,
  sourceTitle: value.source_title,
  infoHash: value.info_hash ?? undefined,
  indexerName: value.indexer_name ?? undefined,
  quality: value.quality ?? undefined,
  reason: value.reason,
  createdAt: value.created_at,
})

const rawEntries = () => openApiData(openApiClient.GET('/api/v1/blocklist', { params: { query: { page_size: 250 } } }))

export const blocklistApi = {
  entries: async () => (await rawEntries()).items.map(mapEntry),
  count: async () => (await openApiData(openApiClient.GET('/api/v1/blocklist/count'))).count,
  remove: (id: string) => openApiData(openApiClient.DELETE('/api/v1/blocklist/{entry_id}', { params: { path: { entry_id: id } } })),
  removeMany: (ids: string[]) => openApiData(openApiClient.POST('/api/v1/blocklist/delete', { body: { ids } })),
  clear: () => openApiData(openApiClient.DELETE('/api/v1/blocklist')),
}
