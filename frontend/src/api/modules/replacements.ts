import type { components } from '../generated/schema'
import { openApiClient, openApiData } from '../transport'

export type ReplacementItem = components['schemas']['ReplacementItem']
export type MediaHistoryItem = components['schemas']['MediaHistoryItem']

export const replacementsApi = {
  queue: () => openApiData(openApiClient.GET('/api/v1/replacements')),
  history: () => openApiData(openApiClient.GET('/api/v1/replacements/history')),
  count: async () => (await openApiData(openApiClient.GET('/api/v1/replacements/count'))).count,
  search: (targetKey?: string) => openApiData(openApiClient.POST('/api/v1/replacements/search', { body: { target_key: targetKey ?? null } })),
  review: (targets: { target_key: string; reason: NonNullable<ReplacementItem['reason_code']> }[], action: 'ignore' | 'restore') =>
    openApiData(openApiClient.POST('/api/v1/replacements/review', { body: { targets, action } })),
  retireTorrents: (candidateId: string) =>
    openApiData(openApiClient.POST('/api/v1/replacements/{candidate_id}/retire-torrents', { params: { path: { candidate_id: candidateId } } })),
  finish: (candidateId: string) =>
    openApiData(openApiClient.POST('/api/v1/replacements/{candidate_id}/finish', { params: { path: { candidate_id: candidateId } } })),
}
