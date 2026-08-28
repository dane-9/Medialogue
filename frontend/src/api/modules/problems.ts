import { mapProblem } from '../mappers/problems'
import { numberValue } from '../mappers/shared'
import { openApiClient, openApiData } from '../transport'

export const problemsApi = {
  problemCount: async (status = 'open') => {
    const payload = await openApiData(openApiClient.GET('/api/v1/problems/count', { params: { query: { status } } }))
    return numberValue(payload.count)
  },
  problemSummary: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/problems/summary'))
    return { open: payload.open ?? 0, suppressed: payload.suppressed ?? 0, workflows: payload.workflows ?? {} }
  },
  problemsPage: async (filters: { status?: string; page?: number; pageSize?: number; category?: string; severity?: string; workflow?: string } = {}) => {
    const payload = await openApiData(openApiClient.GET('/api/v1/problems', { params: { query: {
      status: filters.status,
      category: filters.category && filters.category !== 'all' ? filters.category : undefined,
      severity: filters.severity && filters.severity !== 'all' ? filters.severity : undefined,
      workflow: filters.workflow && filters.workflow !== 'all' ? filters.workflow : undefined,
      page: filters.page ?? 1,
      page_size: filters.pageSize ?? 100,
    } } }))
    return {
      items: payload.items.map(mapProblem),
      total: payload.total ?? payload.items.length,
      pages: payload.pages ?? 1,
      page: payload.page ?? filters.page ?? 1,
      pageSize: payload.page_size ?? filters.pageSize ?? 100,
    }
  },
  deleteProblem: (id: string) => openApiData(openApiClient.DELETE('/api/v1/problems/{problem_id}', { params: { path: { problem_id: id } } })),
  recheckProblems: () => openApiData(openApiClient.POST('/api/v1/problems/recheck')),
  resolveProblem: async (id: string, action: string, payload: Record<string, unknown> = {}) => mapProblem(await openApiData(openApiClient.POST('/api/v1/problems/{problem_id}/resolve', { params: { path: { problem_id: id } }, body: { action, payload } }))),
}
