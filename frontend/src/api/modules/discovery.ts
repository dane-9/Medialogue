import { mapMediaDiscovery } from '../mappers/discovery'
import { mapJob } from '../mappers/system'
import { openApiClient, openApiData } from '../transport'

export const discoveryApi = {
  discoveries: async (state: 'pending' | 'approved' | 'ignored' = 'pending') => {
    const loadPage = (page: number) => openApiData(openApiClient.GET('/api/v1/discoveries', { params: { query: { state, page, page_size: 250 } } }))
    const first = await loadPage(1)
    const items = [...first.items]
    const pages = Math.max(1, first.pages)
    if (pages > 1) {
      const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, index) => loadPage(index + 2)))
      rest.forEach((page) => items.push(...page.items))
    }
    return items.map(mapMediaDiscovery)
  },
  bulkNewMediaDiscoveries: async (ids: string[], action: 'approve' | 'ignore') => {
    const payload = await openApiData(openApiClient.POST('/api/v1/discoveries/bulk', { body: { ids, action } }))
    return { succeeded: payload.succeeded ?? [], failed: payload.failed ?? [] }
  },
  searchDiscoveryCandidates: async (id: string, query?: string) => mapMediaDiscovery(await openApiData(openApiClient.POST('/api/v1/discoveries/{discovery_id}/search', { params: { path: { discovery_id: id } }, body: { query: query ?? null } }))),
  /** Add folders in a background job; a running add is joined, not repeated. */
  startAddJob: async (ids: string[]) => (await openApiData(openApiClient.POST('/api/v1/discoveries/add-job', { body: { ids } }))).job_id,
  /** The add running now, if any. */
  currentAddJob: async () => {
    const job = await openApiData(openApiClient.GET('/api/v1/discoveries/add-job'))
    return job ? mapJob(job) : null
  },
  discoveryCount: async () => (await openApiData(openApiClient.GET('/api/v1/discoveries/count'))).count,
  selectDiscovery: async (id: string, candidate: { tmdbId: number; title?: string; originalTitle?: string; year?: number; posterRef?: string }) => mapMediaDiscovery(await openApiData(openApiClient.POST('/api/v1/discoveries/{discovery_id}/select', {
    params: { path: { discovery_id: id } },
    body: {
      tmdb_id: candidate.tmdbId,
      title: candidate.title ?? null,
      original_title: candidate.originalTitle ?? null,
      year: candidate.year ?? null,
      poster_ref: candidate.posterRef ?? null,
    },
  }))),
  updateDiscoveryEdition: async (id: string, update: { mode: 'automatic' | 'standard' | 'named'; edition?: string }) => mapMediaDiscovery(await openApiData(openApiClient.PATCH('/api/v1/discoveries/{discovery_id}/edition', { params: { path: { discovery_id: id } }, body: update }))),
  updateDiscoveryQualityProfile: async (id: string, qualityProfileId: string | null) => mapMediaDiscovery(await openApiData(openApiClient.PATCH('/api/v1/discoveries/{discovery_id}/quality-profile', { params: { path: { discovery_id: id } }, body: { quality_profile_id: qualityProfileId } }))),
  bulkDiscoveryQualityProfile: async (ids: string[], qualityProfileId: string | null) => {
    const payload = await openApiData(openApiClient.POST('/api/v1/discoveries/quality-profile', { body: { ids, quality_profile_id: qualityProfileId } }))
    return { succeeded: payload.succeeded ?? [], failed: payload.failed ?? [] }
  },
}
