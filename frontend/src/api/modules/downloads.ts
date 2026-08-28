import { mapDownload } from '../mappers/downloads'
import { mapTorrentArchiveItem } from '../mappers/torrentArchive'
import { openApiClient, openApiData } from '../transport'

export const downloadsApi = {
  downloads: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/downloads'))
    return payload.items.map(mapDownload)
  },
  torrentArchiveHealth: async () => {
    const { torrent_archive: archive } = await openApiData(openApiClient.GET('/api/v1/integrations/health'))
    return { ...archive, message: archive.message ?? undefined }
  },
  torrentArchive: async (query = '') => {
    const payload = await openApiData(openApiClient.GET('/api/v1/torrent-archive', { params: { query: { query: query || undefined } } }))
    return payload.items.map(mapTorrentArchiveItem)
  },
  retryTorrentArchive: (id: string) => openApiData(openApiClient.POST('/api/v1/torrent-archive/{torrent_id}/retry', { params: { path: { torrent_id: id } } })),
  restoreTorrentArchive: (id: string, payload: { download_client_id: string; save_path: string; category?: string; tags?: string[] }) => openApiData(openApiClient.POST('/api/v1/torrent-archive/{torrent_id}/restore', { params: { path: { torrent_id: id } }, body: payload })),
}
