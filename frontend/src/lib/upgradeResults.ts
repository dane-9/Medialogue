import type { InteractiveSearchResult, UpgradeSearchResults } from '../types'

const REJECTION_LABELS: Array<[start: string, label: string]> = [
  ['A different title', 'Different title'],
  ['This release group is banned', 'Banned group'],
  ['Different movie edition', 'Different edition'],
  ['The release quality is not recognized', 'Blocked by profile'],
  ['The release is lower quality', 'Lower quality'],
  ['The release scores below', 'Lower score'],
  ['The release does not improve', 'No improvement'],
  ['This is an existing torrent', 'Existing torrent'],
  ['No seeders', 'No seeders'],
  ['Individual episode', 'Not a season pack'],
  ['The release is not a pack', 'Not a season pack'],
  ['The indexer did not provide', 'No download link'],
]

/** A few words for why a result was rejected; the tooltip has the full reason. */
export function rejectionLabel(reasons: string[]): string {
  const reason = reasons[0] ?? ''
  return REJECTION_LABELS.find(([start]) => reason.startsWith(start))?.[1] ?? 'Rejected'
}

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
      // Rejected results stay visible; the screens label them with the reason.
      // A warning here would add the release table's own "Blocked by profile".
      warnings: [],
      // The upgrade search marks previously failed releases with a
      // "Blocklisted:" rejection reason; they must not be selectable.
      blocklisted: Boolean(blockReason),
      blocklistReason: blockReason?.slice('Blocklisted:'.length).trim(),
      createdAt: searchResults.searchedAt,
      expiresAt: searchResults.searchedAt,
    }
  })
}
