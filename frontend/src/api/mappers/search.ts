import type { InteractiveSearchJob, InteractiveSearchResult } from '../../types'
import type { components } from '../generated/schema'

type SearchResultDto = components['schemas']['SearchResultResponse']
type SearchJobDto = components['schemas']['SearchJobResponse']

export function mapInteractiveSearchResult(value: SearchResultDto): InteractiveSearchResult {
  return {
    id: value.id,
    jobId: value.job_id,
    indexerId: value.indexer_id ?? undefined,
    indexerName: value.indexer_name,
    indexerColor: value.indexer_color ?? undefined,
    mediaType: value.media_type,
    targetEntityType: value.target_entity_type,
    title: value.title,
    size: value.size ?? undefined,
    seeders: value.seeders ?? undefined,
    leechers: value.leechers ?? undefined,
    publishedAt: value.published_at ?? undefined,
    quality: value.quality ?? undefined,
    qualityAllowed: value.quality_allowed,
    qualityPreference: value.quality_preference,
    edition: value.edition ?? undefined,
    releaseGroup: value.release_group ?? undefined,
    customFormatScore: value.custom_format_score ?? undefined,
    qualityProfileId: value.quality_profile_id ?? undefined,
    qualityProfileName: value.quality_profile_name ?? undefined,
    minimumQuality: value.minimum_quality ?? undefined,
    minimumQualityMet: value.minimum_quality_met ?? undefined,
    customFormatSnapshot: value.custom_format_snapshot,
    parser: value.parser,
    warnings: value.warnings,
    selectedAt: value.selected_at ?? undefined,
    selectedDownloadClientId: value.selected_download_client_id ?? undefined,
    createdAt: value.created_at,
    expiresAt: value.expires_at,
  }
}

export function mapInteractiveSearchJob(value: SearchJobDto): InteractiveSearchJob {
  return {
    id: value.id,
    status: value.status,
    target: {
      mediaType: value.target.media_type,
      entityType: value.target.entity_type,
      entityId: value.target.entity_id ?? undefined,
      profileEntityId: value.target.profile_entity_id ?? undefined,
      tmdbId: value.target.tmdb_id ?? undefined,
      season: value.target.season ?? undefined,
      episode: value.target.episode ?? undefined,
      title: value.target.title,
      year: value.target.year ?? undefined,
      overview: value.target.overview ?? undefined,
      posterRef: value.target.poster_ref ?? undefined,
      qualityProfileId: value.target.quality_profile_id ?? undefined,
    },
    progress: {
      totalIndexers: value.progress.total_indexers ?? 0,
      completedIndexers: value.progress.completed_indexers ?? 0,
      percent: value.progress.percent ?? 0,
    },
    indexers: value.indexers.map((indexer) => ({
      id: indexer.id,
      name: indexer.name,
      status: indexer.status,
      results: indexer.results ?? 0,
      elapsedMs: indexer.elapsed_ms ?? undefined,
      error: indexer.error ?? undefined,
    })),
    results: value.results.map(mapInteractiveSearchResult),
    resultTotal: value.result_total,
    error: value.error ?? undefined,
    createdAt: value.created_at,
    startedAt: value.started_at ?? undefined,
    finishedAt: value.finished_at ?? undefined,
  }
}
