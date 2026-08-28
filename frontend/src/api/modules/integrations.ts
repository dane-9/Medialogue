import type { DownloadClientCategory, DownloadClientTestResult, IndexerTestResult } from '../../types'
import type { components } from '../generated/schema'
import {
  mapDownloadClient, mapDownloadClientCategory, mapDownloadClientTestResult, mapIndexer,
  mapIndexerTestResult, mapPlexConfiguration, mapPlexTestResult, mapTmdbConfiguration,
} from '../mappers/integrations'
import { mapRemotePathMapping } from '../mappers/storage'
import { openApiClient, openApiData } from '../transport'

type RemotePathMappingCreateDto = components['schemas']['RemotePathMappingCreate']
type RemotePathMappingUpdateDto = components['schemas']['RemotePathMappingUpdate']
type DownloadClientCreateDto = components['schemas']['DownloadClientCreate']
type DownloadClientUpdateDto = components['schemas']['DownloadClientUpdate']
type DownloadClientSavedTestRequestDto = components['schemas']['DownloadClientSavedTestRequest']
type DownloadClientTestRequestDto = components['schemas']['DownloadClientTestRequest']
type IndexerCreateDto = components['schemas']['IndexerCreate']
type IndexerUpdateDto = components['schemas']['IndexerUpdate']
type IndexerTestRequestDto = components['schemas']['IndexerTestRequest']

function testDownloadClient(id: string, configuration?: DownloadClientSavedTestRequestDto): Promise<DownloadClientTestResult>
function testDownloadClient(id: undefined, configuration: DownloadClientTestRequestDto): Promise<DownloadClientTestResult>
async function testDownloadClient(
  id: string | undefined,
  configuration?: DownloadClientTestRequestDto | DownloadClientSavedTestRequestDto,
): Promise<DownloadClientTestResult> {
  if (id) {
    const payload = await openApiData(openApiClient.POST('/api/v1/download-clients/{client_id}/test', {
      params: { path: { client_id: id } },
      ...(configuration ? { body: configuration } : {}),
    }))
    return mapDownloadClientTestResult(payload)
  }
  if (!configuration || typeof configuration.url !== 'string' || typeof configuration.password !== 'string') {
    throw new Error('Download client URL and password are required for an unsaved connection test.')
  }
  const unsavedConfiguration: DownloadClientTestRequestDto = {
    url: configuration.url,
    username: configuration.username ?? undefined,
    password: configuration.password,
  }
  const payload = await openApiData(openApiClient.POST('/api/v1/download-clients/test', { body: unsavedConfiguration }))
  return mapDownloadClientTestResult(payload)
}

function testIndexer(id: string): Promise<IndexerTestResult>
function testIndexer(id: undefined, configuration: IndexerTestRequestDto): Promise<IndexerTestResult>
async function testIndexer(id: string | undefined, configuration?: IndexerTestRequestDto): Promise<IndexerTestResult> {
  if (id) {
    const payload = await openApiData(openApiClient.POST('/api/v1/indexers/{indexer_id}/test', { params: { path: { indexer_id: id } } }))
    return mapIndexerTestResult(payload)
  }
  if (!configuration) throw new Error('Indexer configuration is required for an unsaved connection test.')
  const payload = await openApiData(openApiClient.POST('/api/v1/indexers/test', { body: configuration }))
  return mapIndexerTestResult(payload)
}

export const integrationsApi = {
  remotePathMappings: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/remote-path-mappings'))
    return payload.items.map(mapRemotePathMapping)
  },
  createRemotePathMapping: async (mapping: RemotePathMappingCreateDto) => mapRemotePathMapping(await openApiData(openApiClient.POST('/api/v1/remote-path-mappings', { body: mapping }))),
  updateRemotePathMapping: async (id: string, mapping: RemotePathMappingUpdateDto) => mapRemotePathMapping(await openApiData(openApiClient.PATCH('/api/v1/remote-path-mappings/{mapping_id}', { params: { path: { mapping_id: id } }, body: mapping }))),
  deleteRemotePathMapping: (id: string) => openApiData(openApiClient.DELETE('/api/v1/remote-path-mappings/{mapping_id}', { params: { path: { mapping_id: id } } })),
  plexConfiguration: async () => mapPlexConfiguration(await openApiData(openApiClient.GET('/api/v1/integrations/plex'))),
  savePlex: async (configuration: { url: string; token?: string; enabled: boolean; expected_revision?: number }) => mapPlexConfiguration(await openApiData(openApiClient.PUT('/api/v1/integrations/plex', { body: configuration }))),
  refreshPlexHealth: async () => mapPlexTestResult(await openApiData(openApiClient.POST('/api/v1/integrations/plex/health/refresh'))),
  syncPlexLibrary: () => openApiData(openApiClient.POST('/api/v1/integrations/plex/sync')),
  tmdbConfiguration: async () => mapTmdbConfiguration(await openApiData(openApiClient.GET('/api/v1/integrations/tmdb'))),
  saveTmdb: async (configuration: { api_key?: string; enabled: boolean; expected_revision?: number }) => mapTmdbConfiguration(await openApiData(openApiClient.PUT('/api/v1/integrations/tmdb', { body: configuration }))),
  downloadClients: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/download-clients'))
    return payload.items.map(mapDownloadClient)
  },
  downloadClientCategories: async (id: string): Promise<DownloadClientCategory[]> => {
    const rows = await openApiData(openApiClient.GET('/api/v1/download-clients/{client_id}/categories', { params: { path: { client_id: id } } }))
    return rows.map(mapDownloadClientCategory)
  },
  createDownloadClient: async (configuration: DownloadClientCreateDto) => mapDownloadClient(await openApiData(openApiClient.POST('/api/v1/download-clients', { body: configuration }))),
  updateDownloadClient: async (id: string, configuration: DownloadClientUpdateDto) => mapDownloadClient(await openApiData(openApiClient.PUT('/api/v1/download-clients/{client_id}', { params: { path: { client_id: id } }, body: configuration }))),
  testDownloadClient,
  refreshDownloadClient: async (id: string) => mapDownloadClientTestResult(await openApiData(openApiClient.POST('/api/v1/download-clients/{client_id}/health/refresh', { params: { path: { client_id: id } } }))),
  deleteDownloadClient: (id: string) => openApiData(openApiClient.DELETE('/api/v1/download-clients/{client_id}', { params: { path: { client_id: id } } })),
  indexers: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/indexers'))
    return payload.items.map(mapIndexer)
  },
  createIndexer: async (configuration: IndexerCreateDto) => mapIndexer(await openApiData(openApiClient.POST('/api/v1/indexers', { body: configuration }))),
  updateIndexer: async (id: string, configuration: IndexerUpdateDto) => mapIndexer(await openApiData(openApiClient.PUT('/api/v1/indexers/{indexer_id}', { params: { path: { indexer_id: id } }, body: configuration }))),
  deleteIndexer: (id: string) => openApiData(openApiClient.DELETE('/api/v1/indexers/{indexer_id}', { params: { path: { indexer_id: id } } })),
  testIndexer,
}
