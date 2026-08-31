import type { MediaDiscovery, TMDBMovieLookup, TMDBShowLookup } from '../../types'
import type { components } from '../generated/schema'

type MediaDiscoveryDto = components['schemas']['MediaDiscoveryResponse']
type MediaDiscoveryCandidateDto = components['schemas']['MediaDiscoveryCandidate']

export function mapDiscoveryCandidate(value: MediaDiscoveryCandidateDto, mediaType: MediaDiscovery['mediaType']): TMDBMovieLookup | TMDBShowLookup {
  const candidate = {
    tmdbId: value.tmdb_id,
    title: value.title,
    originalTitle: value.original_title ?? undefined,
    year: value.year ?? undefined,
    overview: value.overview ?? undefined,
    posterRef: value.poster_ref ?? undefined,
  }
  return mediaType === 'shows' ? candidate as TMDBShowLookup : candidate as TMDBMovieLookup
}

export function mapMediaDiscovery(value: MediaDiscoveryDto): MediaDiscovery {
  return {
    id: value.id,
    directoryId: value.directory_id,
    path: value.path,
    rootName: value.root_name,
    exists: value.exists,
    mediaType: value.media_type,
    newMediaState: value.review_state,
    parsedTitle: value.parsed_title ?? undefined,
    parsedYear: value.parsed_year ?? undefined,
    confidence: value.confidence ?? undefined,
    qualityProfileId: value.quality_profile_id ?? undefined,
    qualityProfileName: value.quality_profile_name ?? undefined,
    quality: value.quality ?? undefined,
    edition: value.edition ?? undefined,
    parsedEdition: value.parsed_edition ?? undefined,
    editionOverrideActive: value.edition_override_active ?? false,
    manualEditionOverride: value.manual_edition_override,
    libraryEditionConflict: value.library_edition_conflict ?? false,
    hdr: value.hdr ?? [],
    suggested: value.suggested ? mapDiscoveryCandidate(value.suggested, value.media_type) : undefined,
    selected: value.selected ? mapDiscoveryCandidate(value.selected, value.media_type) : undefined,
    candidates: (value.candidates ?? []).map((candidate) => mapDiscoveryCandidate(candidate, value.media_type)),
    files: (value.files ?? []).map((file) => ({ relativePath: file.relative_path, role: file.role })),
    fileCount: value.file_count ?? 0,
    videoCount: value.video_count ?? 0,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  }
}
