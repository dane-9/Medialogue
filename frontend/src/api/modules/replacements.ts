import type { components } from '../generated/schema'
import { openApiClient, openApiData } from '../transport'

export type ReplacementItem = components['schemas']['ReplacementItem']
export type ReplacementTab = ReplacementItem['tab']

export const replacementsApi = {
  queue: () => openApiData(openApiClient.GET('/api/v1/replacements')),
  count: async () => (await openApiData(openApiClient.GET('/api/v1/replacements/count'))).count,
  search: (targetKey?: string) => openApiData(openApiClient.POST('/api/v1/replacements/search', { body: { target_key: targetKey ?? null } })),
  review: (torrentIds: string[], action: 'ignore' | 'snooze' | 'restore') =>
    openApiData(openApiClient.POST('/api/v1/replacements/review', { body: { torrent_ids: torrentIds, action } })),
  retireTorrents: (candidateId: string) =>
    openApiData(openApiClient.POST('/api/v1/replacements/{candidate_id}/retire-torrents', { params: { path: { candidate_id: candidateId } } })),
  finish: (candidateId: string) =>
    openApiData(openApiClient.POST('/api/v1/replacements/{candidate_id}/finish', { params: { path: { candidate_id: candidateId } } })),
}
