import { mapEpisode, mapSeason, mapShow } from '../mappers/shows'
import { mapTMDBShowLookup } from '../mappers/metadata'
import { LIBRARY_PAGE_SIZE, openApiClient, openApiData } from '../transport'

export const showsApi = {
  shows: async (query = '', sort = 'title') => {
    const payload = await openApiData(openApiClient.GET('/api/v1/shows', { params: { query: {
      page_size: LIBRARY_PAGE_SIZE,
      query: query || undefined,
      sort,
    } } }))
    return payload.items.map(mapShow)
  },
  show: async (resourceId: string) => mapShow(await openApiData(openApiClient.GET('/api/v1/shows/{resource_id}', { params: { path: { resource_id: resourceId } } }))),
  specialsCounting: () => openApiData(openApiClient.GET('/api/v1/shows/specials-counting')),
  setSpecialsCounting: (countSpecials: boolean) => openApiData(openApiClient.PUT('/api/v1/shows/specials-counting', { body: { count_specials: countSpecials } })),
  lookupShows: async (query: string, year?: number) => (await openApiData(openApiClient.GET('/api/v1/shows/lookup', { params: { query: { query, year } } }))).map(mapTMDBShowLookup),
  episodeOrderings: async (resourceId: string) => {
    const payload = await openApiData(openApiClient.GET('/api/v1/shows/{resource_id}/episode-orderings', { params: { path: { resource_id: resourceId } } }))
    return payload.map((option) => ({ ...option, description: option.description ?? undefined, network: option.network ?? undefined }))
  },
  updateShow: async (resourceId: string, payload: { monitored?: boolean; tmdb_episode_group_id?: string; expected_revision?: number }) => mapShow(await openApiData(openApiClient.PATCH('/api/v1/shows/{resource_id}', { params: { path: { resource_id: resourceId } }, body: payload }))),
  updateSeason: async (seasonId: string, payload: { monitored?: boolean; counted?: boolean; expected_revision?: number }) => mapSeason(await openApiData(openApiClient.PATCH('/api/v1/seasons/{season_id}', { params: { path: { season_id: seasonId } }, body: payload }))),
  updateEpisode: async (episodeId: string, payload: { monitored?: boolean; expected_revision?: number }) => mapEpisode(await openApiData(openApiClient.PATCH('/api/v1/episodes/{episode_id}', { params: { path: { episode_id: episodeId } }, body: payload }))),
  correctEpisodeMapping: (mediaFileId: string, episodeIds: string[]) => openApiData(openApiClient.PUT('/api/v1/media-files/{media_file_id}/episode-mappings', { params: { path: { media_file_id: mediaFileId } }, body: { episode_ids: episodeIds } })),
  refreshShowMetadata: (resourceId: string) => openApiData(openApiClient.POST('/api/v1/shows/{resource_id}/metadata/refresh', { params: { path: { resource_id: resourceId } } })),
  recheckShowPlex: (resourceId: string) => openApiData(openApiClient.POST('/api/v1/shows/{resource_id}/actions/recheck-plex', { params: { path: { resource_id: resourceId } } })),
  startSeasonSearch: (seasonId: string) => openApiData(openApiClient.POST('/api/v1/seasons/{season_id}/interactive-search', { params: { path: { season_id: seasonId } } })),
  showDownloadPreview: async (tmdbId: number) => {
    const payload = await openApiData(openApiClient.GET('/api/v1/interactive-search/shows/{tmdb_id}/preview', { params: { path: { tmdb_id: tmdbId } } }))
    return {
      tmdbId: payload.tmdb_id,
      title: payload.title,
      year: payload.year ?? undefined,
      overview: payload.overview ?? undefined,
      posterRef: payload.poster_ref ?? undefined,
      seasons: payload.seasons.map((season) => ({
        seasonNumber: season.season_number,
        title: season.title ?? undefined,
        episodeCount: season.episode_count ?? 0,
        airDate: season.air_date ?? undefined,
        posterRef: season.poster_ref ?? undefined,
      })),
    }
  },
  showDownloadPreferences: () => openApiData(openApiClient.GET('/api/v1/shows/download-preferences')),
  saveShowDownloadPreferences: (includeYearInFolderName: boolean) => openApiData(openApiClient.PUT('/api/v1/shows/download-preferences', { body: { include_year_in_folder_name: includeYearInFolderName } })),
  startUnattachedShowSeasonSearch: (tmdbId: number, qualityProfileId: string, seasonNumber: number) => openApiData(openApiClient.POST('/api/v1/interactive-search/shows/seasons', { body: { tmdb_id: tmdbId, quality_profile_id: qualityProfileId, season_number: seasonNumber } })),
}
