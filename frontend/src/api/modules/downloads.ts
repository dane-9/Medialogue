import { mapDownload } from '../mappers/downloads'
import { mapTorrentArchiveItem } from '../mappers/torrentArchive'
import { LIBRARY_PAGE_SIZE, openApiClient, openApiData } from '../transport'

export const downloadsApi = {
  downloads: async (query: {
    clientId?: string
    filters?: string[]
    sort?: string
    direction?: 'asc' | 'desc'
    page?: number
    pageSize?: number
  } = {}) => {
    const payload = await openApiData(openApiClient.GET('/api/v1/downloads', {
      params: {
        query: {
          client_id: query.clientId || undefined,
          filters: query.filters?.length ? query.filters : undefined,
          sort: query.sort || undefined,
          direction: query.direction,
          page: query.page ?? 1,
          page_size: query.pageSize ?? 100,
        },
      },
    }))
    return { items: payload.items.map(mapDownload), total: payload.total, pages: payload.pages, page: payload.page }
  },
  downloadFacets: async (clientId?: string) => openApiData(openApiClient.GET('/api/v1/downloads/facets', {
    params: { query: { client_id: clientId || undefined } },
  })),
  applyDownloadAction: (
    downloadIds: string[],
    action: 'start' | 'stop' | 'recheck' | 'reannounce'
      | 'queue_top' | 'queue_up' | 'queue_down' | 'queue_bottom'
      | 'download_limit' | 'upload_limit' | 'share_ratio' | 'remove',
    value?: number,
  ) => openApiData(openApiClient.POST('/api/v1/downloads/actions', {
    body: { download_ids: downloadIds, action, value: value ?? null },
  })),
  /** What deleting these downloads would remove, for the user to confirm. */
  deletePlan: (downloadIds: string[]) => openApiData(openApiClient.POST('/api/v1/downloads/delete-plan', {
    body: { download_ids: downloadIds },
  })),
  /** Remove the torrents and their cross-seeds, and delete their media folders. */
  deleteDownloads: (downloadIds: string[], library: 'replace' | 'forget') => openApiData(openApiClient.POST('/api/v1/downloads/delete', {
    body: { download_ids: downloadIds, library },
  })),
  torrentArchiveHealth: async () => {
    const { torrent_archive: archive } = await openApiData(openApiClient.GET('/api/v1/integrations/health'))
    return { ...archive, message: archive.message ?? undefined }
  },
  rebuildTorrentArchive: async () => await openApiData(openApiClient.POST('/api/v1/torrent-archive/rebuild')) as { imported: number; skipped: number; errors: unknown[] },
  // The whole archive in one request; the Backups page filters it locally.
  torrentArchive: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/torrent-archive', { params: { query: { page_size: LIBRARY_PAGE_SIZE } } }))
    return payload.items.map(mapTorrentArchiveItem)
  },
  retryTorrentArchive: (id: string) => openApiData(openApiClient.POST('/api/v1/torrent-archive/{torrent_id}/retry', { params: { path: { torrent_id: id } } })),
}
