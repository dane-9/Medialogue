import { mapInteractiveSearchJob } from '../mappers/search'
import { openApiClient, openApiData } from '../transport'

export const searchApi = {
  searchJob: async (jobId: string) => mapInteractiveSearchJob(await openApiData(openApiClient.GET('/api/v1/search-jobs/{job_id}', { params: { path: { job_id: jobId } } }))),
  downloadSearchResult: (resultId: string, payload: { download_client_id: string; category?: string | null; show_folder_name?: string; include_year_in_show_folder?: boolean }) => openApiData(openApiClient.POST('/api/v1/search-results/{result_id}/download', {
    params: { path: { result_id: resultId } },
    body: { ...payload, include_year_in_show_folder: payload.include_year_in_show_folder ?? false },
  })),
}
