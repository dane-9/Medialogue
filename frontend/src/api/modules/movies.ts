import type { MovieEditionUpdate, MovieEditionUpdateResult } from '../../types'
import { mapMovie, mapTag } from '../mappers/movies'
import { mapTMDBMovieLookup } from '../mappers/metadata'
import { openApiClient, openApiData } from '../transport'

export const moviesApi = {
  movies: async (query = '', tag = '', sort = 'title') => {
    const payload = await openApiData(openApiClient.GET('/api/v1/movies', { params: { query: {
      page_size: 250,
      query: query || undefined,
      tag: tag || undefined,
      sort,
    } } }))
    return payload.items.map(mapMovie)
  },
  movie: async (id: string) => mapMovie(await openApiData(openApiClient.GET('/api/v1/movies/{resource_id}', { params: { path: { resource_id: id } } }))),
  tags: async () => (await openApiData(openApiClient.GET('/api/v1/tags'))).map(mapTag),
  createTag: async (name: string) => mapTag(await openApiData(openApiClient.POST('/api/v1/tags', { body: { name } }))),
  bulkMovies: (payload: { movie_ids: string[]; action: 'change_profile' | 'add_tags' | 'remove_tags' | 'monitor' | 'unmonitor' | 'recheck_plex' | 'reevaluate_parser' | 'reevaluate_custom_formats'; quality_profile_id?: string | null; tag_ids?: string[] }) => openApiData(openApiClient.POST('/api/v1/movies/bulk', { body: payload })),
  lookupMovies: async (query: string, year?: number) => (await openApiData(openApiClient.GET('/api/v1/movies/lookup', { params: { query: { query, year } } }))).map(mapTMDBMovieLookup),
  movieEditionOptions: async () => (await openApiData(openApiClient.GET('/api/v1/movies/edition-options'))).editions,
  updateMovieReleaseEditions: async (movieId: string, updates: MovieEditionUpdate[]): Promise<MovieEditionUpdateResult> => {
    const payload = await openApiData(openApiClient.PATCH('/api/v1/movies/{resource_id}/releases/editions', {
      params: { path: { resource_id: movieId } },
      body: { updates: updates.map((update) => ({ release_id: update.releaseId, mode: update.mode, edition: update.edition })) },
    }))
    return {
      movieId: payload.movie_id,
      duplicateResolved: payload.duplicate_resolved,
      releases: payload.releases.map((release) => ({
        releaseId: release.release_id,
        parsedEdition: release.parsed_edition ?? undefined,
        editionOverrideActive: release.edition_override_active,
        manualEditionOverride: release.manual_edition_override,
        effectiveEdition: release.effective_edition ?? undefined,
        state: release.state,
      })),
    }
  },
  rematchMovie: async (id: string, tmdbId: number) => mapMovie(await openApiData(openApiClient.POST('/api/v1/movies/{resource_id}/rematch', {
    params: { path: { resource_id: id } },
    body: { tmdb_id: tmdbId },
  }))),
  recheckMoviePlex: (id: string) => openApiData(openApiClient.POST('/api/v1/movies/{resource_id}/actions/recheck-plex', { params: { path: { resource_id: id } } })),
  startMovieSearch: (id: string) => openApiData(openApiClient.POST('/api/v1/movies/{resource_id}/interactive-search', { params: { path: { resource_id: id } } })),
  startUnattachedMovieSearch: (tmdbId: number, qualityProfileId: string) => openApiData(openApiClient.POST('/api/v1/interactive-search/movies', { body: { tmdb_id: tmdbId, quality_profile_id: qualityProfileId } })),
}
