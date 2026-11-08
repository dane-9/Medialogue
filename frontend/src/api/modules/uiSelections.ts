import { openApiClient, openApiData } from '../transport'

/** Lists whose left-out rows the server remembers. */
export type UiSelectionList = 'new-media-excluded' | 'upgrade-media-excluded' | 'replace-media-unticked' | 'replace-media-picked'

export const uiSelectionsApi = {
  get: async (list: UiSelectionList) => (await openApiData(openApiClient.GET('/api/v1/ui-selections/{list_key}', { params: { path: { list_key: list } } }))).ids,
  replace: async (list: UiSelectionList, ids: string[]) => (await openApiData(openApiClient.PUT('/api/v1/ui-selections/{list_key}', { params: { path: { list_key: list } }, body: { ids } }))).ids,
}
