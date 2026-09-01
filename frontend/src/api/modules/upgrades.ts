import type { UpgradeCandidate, UpgradeCleanupPreview, UpgradeCleanupResult } from '../../types'
import type { components } from '../generated/schema'
import { openApiClient, openApiData } from '../transport'

type UpgradeCandidateDto = components['schemas']['UpgradeCandidateResponse']
type UpgradeCleanupPreviewDto = components['schemas']['UpgradeCleanupPreviewResponse']

function mapUpgradeCandidate(value: UpgradeCandidateDto): UpgradeCandidate {
  return {
    id: value.id,
    mediaType: value.media_type,
    movieId: value.movie_id ?? undefined,
    seasonId: value.season_id ?? undefined,
    mediaTitle: value.media_title,
    mediaYear: value.media_year ?? undefined,
    seasonNumber: value.season_number ?? undefined,
    currentPaths: value.current_paths ?? [],
    rootName: value.root_name ?? undefined,
    posterRef: value.poster_ref ?? undefined,
    releaseFolder: value.release_folder ?? undefined,
    currentReleaseIds: value.current_release_ids,
    currentReleaseName: value.current_release_name,
    currentQuality: value.current_quality ?? undefined,
    currentCustomFormatScore: value.current_custom_format_score,
    qualityProfileId: value.quality_profile_id ?? undefined,
    qualityProfileName: value.quality_profile_name ?? undefined,
    qualityProfileInherited: value.quality_profile_inherited,
    indexerId: value.indexer_id ?? undefined,
    indexerName: value.indexer_name,
    title: value.title,
    quality: value.quality ?? undefined,
    customFormatScore: value.custom_format_score,
    scoreDelta: value.score_delta,
    size: value.size ?? undefined,
    seeders: value.seeders ?? undefined,
    leechers: value.leechers ?? undefined,
    publishedAt: value.published_at ?? undefined,
    currentMatchedFormats: (value.current_matched_formats ?? []).map((format) => ({
      id: format.custom_format_id,
      name: format.custom_format_name,
      contribution: format.contribution,
    })),
    candidateMatchedFormats: (value.candidate_matched_formats ?? []).map((format) => ({
      id: format.custom_format_id,
      name: format.custom_format_name,
      contribution: format.contribution,
    })),
    tmdbId: value.tmdb_id ?? undefined,
    indexerColor: value.indexer_color ?? undefined,
    upgradeTypes: value.upgrade_types,
    addedCustomFormats: value.added_custom_formats.map((format) => ({
      id: String(format.custom_format_id ?? ''),
      name: String(format.custom_format_name ?? 'Custom Format'),
      contribution: Number(format.contribution ?? 0),
      previousContribution: format.previous_contribution == null ? undefined : Number(format.previous_contribution),
      delta: Number(format.delta ?? 0),
      isNew: Boolean(format.new),
    })),
    executionEligible: value.execution_eligible,
    executionBlockReason: value.execution_block_reason ?? undefined,
    comparisonSnapshot: value.comparison_snapshot,
    executionState: value.execution_state ?? undefined,
    selectedDownloadClientId: value.selected_download_client_id ?? undefined,
    selectedDownloadClientName: value.selected_download_client_name ?? undefined,
    selectedCategory: value.selected_category ?? undefined,
    torrentId: value.torrent_id ?? undefined,
    downloadProgress: value.download_progress ?? undefined,
    downloadState: value.download_state ?? undefined,
    executionError: value.execution_error ?? undefined,
    submittedAt: value.submitted_at ?? undefined,
    validatedAt: value.validated_at ?? undefined,
    oldTorrentsRemovedAt: value.old_torrents_removed_at ?? undefined,
    cleanupCompletedAt: value.cleanup_completed_at ?? undefined,
    lastSearchedAt: value.last_searched_at,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  }
}

function mapCleanupPreview(value: UpgradeCleanupPreviewDto): UpgradeCleanupPreview {
  return {
    candidateId: value.candidate_id,
    mediaType: value.media_type,
    eligible: value.eligible,
    blocked: value.blocked,
    targets: value.targets.map((target) => ({
      path: target.path,
      kind: target.kind,
      inventory: target.inventory ?? [],
    })),
    totalBytes: value.total_bytes,
    confirmationToken: value.confirmation_token,
    expiresAt: value.expires_at,
    warnings: value.warnings,
  }
}

export const upgradesApi = {
  upgradeCandidateCount: async () => {
    const value = await openApiData(openApiClient.GET('/api/v1/upgrade-candidates/count'))
    return { count: value.count, readOnlyRoots: value.read_only_roots }
  },
  upgradeCandidates: async (mediaType?: 'movies' | 'shows') => {
    const rows = await openApiData(openApiClient.GET('/api/v1/upgrade-candidates', {
      params: { query: { media_type: mediaType ?? null } },
    }))
    return rows.map(mapUpgradeCandidate)
  },
  searchUpgrades: async () => {
    const value = await openApiData(openApiClient.POST('/api/v1/upgrade-candidates/search'))
    return { jobId: value.job_id }
  },
  setUpgradeQualityProfile: async (candidateId: string, qualityProfileId: string | null) => {
    const value = await openApiData(openApiClient.PATCH('/api/v1/upgrade-candidates/{candidate_id}/quality-profile', {
      params: { path: { candidate_id: candidateId } },
      body: { quality_profile_id: qualityProfileId },
    }))
    return {
      jobId: value.job_id,
      candidateIds: value.candidate_ids,
      qualityProfileId: value.quality_profile_id ?? undefined,
      qualityProfileName: value.quality_profile_name ?? undefined,
      updatedEntities: value.updated_entities,
    }
  },
  setUpgradeQualityProfiles: async (candidateIds: string[], qualityProfileId: string | null) => {
    const value = await openApiData(openApiClient.POST('/api/v1/upgrade-candidates/quality-profile', {
      body: { candidate_ids: candidateIds, quality_profile_id: qualityProfileId },
    }))
    return {
      jobId: value.job_id,
      candidateIds: value.candidate_ids,
      qualityProfileId: value.quality_profile_id ?? undefined,
      qualityProfileName: value.quality_profile_name ?? undefined,
      updatedEntities: value.updated_entities,
    }
  },
  executeUpgrade: async (candidateId: string, downloadClientId: string, category?: string) => mapUpgradeCandidate(
    await openApiData(openApiClient.POST('/api/v1/upgrade-candidates/{candidate_id}/upgrade', {
      params: { path: { candidate_id: candidateId } },
      body: { download_client_id: downloadClientId, category: category || null },
    })),
  ),
  retryUpgradeHandoff: async (candidateId: string) => mapUpgradeCandidate(
    await openApiData(openApiClient.POST('/api/v1/upgrade-candidates/{candidate_id}/retry-handoff', {
      params: { path: { candidate_id: candidateId } },
    })),
  ),
  upgradeCleanupPreview: async (candidateId: string) => mapCleanupPreview(
    await openApiData(openApiClient.GET('/api/v1/upgrade-candidates/{candidate_id}/cleanup-preview', {
      params: { path: { candidate_id: candidateId } },
    })),
  ),
  cleanupUpgrade: async (candidateId: string, confirmationToken: string): Promise<UpgradeCleanupResult> => {
    const value = await openApiData(openApiClient.POST('/api/v1/upgrade-candidates/{candidate_id}/cleanup', {
      params: { path: { candidate_id: candidateId } },
      body: { confirmation_token: confirmationToken },
    }))
    return { candidateId: value.candidate_id, state: value.state, deletedPaths: value.deleted_paths }
  },
}
