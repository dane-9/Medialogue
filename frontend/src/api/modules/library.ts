import { openApiClient, openApiData } from '../transport'

export type LibraryRemoveMode = 'forget' | 'replace' | 'keep_files'
export interface LibraryTitles { movieIds?: string[]; showIds?: string[] }

const body = ({ movieIds = [], showIds = [] }: LibraryTitles) => ({ movie_ids: movieIds, show_ids: showIds })

export const libraryApi = {
  /** What removing these titles would delete, for the user to confirm. */
  removePlan: (titles: LibraryTitles) => openApiData(openApiClient.POST('/api/v1/library/remove-plan', { body: body(titles) })),
  /** Remove movies or shows: with their files and torrents, or keeping the files. */
  remove: (titles: LibraryTitles, mode: LibraryRemoveMode) => openApiData(openApiClient.POST('/api/v1/library/remove', {
    body: { ...body(titles), mode },
  })),
  /** Folders scans skip, because their title was removed with the files kept. */
  ignoredFolders: () => openApiData(openApiClient.GET('/api/v1/library/ignored-folders')),
  stopIgnoring: (id: string) => openApiData(openApiClient.DELETE('/api/v1/library/ignored-folders/{folder_id}', {
    params: { path: { folder_id: id } },
  })),
}
