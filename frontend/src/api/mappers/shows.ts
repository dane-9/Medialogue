import type { Episode, EpisodeMedia, Season, Show } from '../../types'
import type { components } from '../generated/schema'
import { mapEvidence, mapEvent, mapPlexState, mapTorrentStatus } from './mediaCommon'

type EpisodeMediaDto = components['schemas']['EpisodeMediaResponse']
type EpisodeDto = components['schemas']['EpisodeResponse']
type SeasonDto = components['schemas']['SeasonResponse']
type ShowSummaryDto = components['schemas']['ShowSummaryResponse']
type ShowDetailDto = components['schemas']['ShowDetailResponse']

function mapEpisodeMedia(value: EpisodeMediaDto): EpisodeMedia {
  return {
    mediaFileId: value.media_file_id,
    showReleaseId: value.show_release_id ?? undefined,
    path: value.path,
    exists: value.exists,
    quality: value.quality ?? undefined,
    releaseGroup: value.release_group ?? undefined,
    releaseName: value.release_name ?? undefined,
    releaseScope: value.release_scope ?? undefined,
    mappedEpisodeNumbers: value.mapped_episode_numbers ?? [],
    manualMapping: value.manual_mapping ?? false,
    torrentStatus: mapTorrentStatus(value.torrent_status),
  }
}

export function mapEpisode(value: EpisodeDto): Episode {
  const statuses: Record<EpisodeDto['presence_state'], Episode['status']> = {
    present: 'Present',
    missing: 'Missing',
  }
  return {
    id: value.id,
    seasonNumber: value.season_number,
    episodeNumber: value.episode_number,
    title: value.title ?? undefined,
    airDate: value.air_date ?? undefined,
    tmdbId: value.tmdb_id ?? undefined,
    tvdbId: value.tvdb_id ?? undefined,
    monitored: value.monitored,
    status: statuses[value.presence_state],
    revision: value.revision,
    quality: value.quality ?? undefined,
    plex: mapPlexState(value.plex_state ?? 'unknown'),
    media: (value.media ?? []).map(mapEpisodeMedia),
  }
}

export function mapSeason(value: SeasonDto): Season {
  return {
    id: value.id,
    seasonNumber: value.season_number,
    title: value.title ?? undefined,
    monitored: value.monitored,
    counted: value.counted ?? true,
    revision: value.revision,
    episodeCount: value.episode_count,
    presentCount: value.present_count,
    missingCount: value.missing_count,
    episodes: (value.episodes ?? []).map(mapEpisode),
  }
}

export function mapShow(value: ShowSummaryDto | ShowDetailDto): Show {
  const detail = 'overview' in value ? value as ShowDetailDto : undefined
  return {
    id: value.resource_id,
    internalId: value.id,
    title: value.title,
    year: value.year ?? 0,
    poster: value.poster_ref ?? undefined,
    seasons: value.season_count,
    episodesPresent: value.episodes_present,
    episodesTotal: value.episode_count,
    episodesMissing: value.episodes_missing,
    status: value.state,
    plex: mapPlexState(value.plex_state),
    tmdbId: value.tmdb_id ?? undefined,
    tvdbId: value.tvdb_id ?? undefined,
    tmdbEpisodeGroupId: value.tmdb_episode_group_id ?? undefined,
    monitored: value.monitored,
    identityState: value.identity_state,
    problemCount: value.problem_count,
    overview: detail?.overview ?? undefined,
    revision: value.revision,
    seasonDetail: (detail?.seasons ?? []).map(mapSeason),
    recentEvents: (detail?.recent_events ?? []).map(mapEvent),
    problems: (detail?.problems ?? []).map(mapEvidence),
    storageRoots: detail?.storage_roots?.map((root) => ({ id: root.id, name: root.name, path: root.path, health: root.health ?? undefined })),
    lastObservedAt: value.last_observed_at ?? undefined,
    torrentStatus: mapTorrentStatus(value.torrent_status),
  }
}
