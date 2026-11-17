import type { CustomFilter, FilterCondition } from '../../lib/customFilters'
import type { components } from '../generated/schema'
import { openApiClient, openApiData } from '../transport'

type CustomFilterDto = components['schemas']['CustomFilterResponse']

const mapCustomFilter = (value: CustomFilterDto): CustomFilter => ({
  id: value.id,
  mediaType: value.media_type,
  label: value.label,
  filters: value.filters.map((condition) => ({ key: condition.key, type: condition.type, value: condition.value })),
})

export const customFiltersApi = {
  list: async (mediaType: 'movies' | 'shows') =>
    (await openApiData(openApiClient.GET('/api/v1/custom-filters', { params: { query: { media_type: mediaType } } }))).map(mapCustomFilter),
  create: async (mediaType: 'movies' | 'shows', label: string, filters: FilterCondition[]) =>
    mapCustomFilter(await openApiData(openApiClient.POST('/api/v1/custom-filters', { body: { media_type: mediaType, label, filters } }))),
  update: async (id: string, mediaType: 'movies' | 'shows', label: string, filters: FilterCondition[]) =>
    mapCustomFilter(await openApiData(openApiClient.PUT('/api/v1/custom-filters/{filter_id}', { params: { path: { filter_id: id } }, body: { media_type: mediaType, label, filters } }))),
  remove: (id: string) => openApiData(openApiClient.DELETE('/api/v1/custom-filters/{filter_id}', { params: { path: { filter_id: id } } })),
}
