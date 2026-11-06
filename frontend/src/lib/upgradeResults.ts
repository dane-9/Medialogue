import type { InteractiveSearchResult, UpgradeSearchResults } from '../types'

/**
 * Adapt Upgrade/Replace search results to the shared release table, so both
 * screens get Interactive Search's sorting, badges and blocklist marks.
 */
export function upgradeResultsToReleases(
  searchResults: UpgradeSearchResults,
  jobId: string,
  mediaType: 'movies' | 'shows',
): InteractiveSearchResult[] {
  return searchResults.results.map((result) => {
    const blockReason = (result.rejectionReasons ?? []).find((reason) => reason.startsWith('Blocklisted:'))
    return {
      id: result.id,
      jobId,
      indexerId: result.indexerId,
      indexerName: result.indexerName,
      indexerColor: result.indexerColor,
      mediaType,
      targetEntityType: mediaType === 'movies' ? 'movie' : 'season',
      title: result.title,
      size: result.size,
      seeders: result.seeders,
      leechers: result.leechers,
      publishedAt: result.publishedAt,
      quality: result.quality,
      qualityAllowed: result.eligible,
      qualityPreference: 0,
      edition: result.edition,
      releaseGroup: result.releaseGroup,
      customFormatScore: result.customFormatScore,
      customFormatSnapshot: { matched_format_names: result.matchedFormatNames },
      parser: result.parser,
      // Rejected results stay visible, with the reason as a warning.
      warnings: result.eligible ? [] : (result.rejectionReasons ?? []).filter((reason) => !reason.startsWith('Blocklisted:')),
      // The upgrade search marks previously failed releases with a
      // "Blocklisted:" rejection reason; they must not be selectable.
      blocklisted: Boolean(blockReason),
      blocklistReason: blockReason?.slice('Blocklisted:'.length).trim(),
      createdAt: searchResults.searchedAt,
      expiresAt: searchResults.searchedAt,
    }
  })
}
