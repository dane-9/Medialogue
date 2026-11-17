import type { Movie, Show } from '../types'
import type { FilterField } from './customFilters'

const PLEX = ['Verified', 'Pending', 'Not in Plex', 'Multiple versions', 'Conflict', 'Unavailable']

/** What a custom filter on the Movies list can test; editions are every one the parser knows. */
export const movieFilterFields = (editions: string[] = []): FilterField<Movie>[] => [
  { key: 'title', label: 'Title', kind: 'string', get: (movie) => movie.title },
  { key: 'year', label: 'Year', kind: 'number', get: (movie) => movie.year },
  { key: 'monitored', label: 'Monitored', kind: 'bool', get: (movie) => movie.monitored !== false },
  { key: 'status', label: 'Status', kind: 'exact', choices: ['Present', 'Missing', 'Duplicate', 'Downloading'], get: (movie) => movie.status },
  { key: 'quality', label: 'Quality', kind: 'exact', get: (movie) => movie.quality || undefined },
  // A movie with no edition reads as theatrical, so that edition is never offered.
  { key: 'edition', label: 'Edition', kind: 'exact', choices: editions.filter((edition) => edition.toLowerCase() !== 'theatrical cut'), get: (movie) => movie.edition },
  { key: 'releases', label: 'Releases', kind: 'number', get: (movie) => movie.releases },
  { key: 'problems', label: 'Problems', kind: 'number', get: (movie) => movie.problemCount ?? 0 },
  { key: 'tags', label: 'Tags', kind: 'array', get: (movie) => movie.tags?.map((tag) => tag.name) ?? [] },
  { key: 'mediaFolder', label: 'Media folder', kind: 'exact', get: (movie) => movie.storageRoot },
  { key: 'plex', label: 'Plex', kind: 'exact', choices: PLEX, get: (movie) => movie.plex },
  { key: 'torrentState', label: 'Torrent state', kind: 'exact', get: (movie) => movie.torrentStatus?.state },
  { key: 'lastObserved', label: 'Last observed', kind: 'date', get: (movie) => movie.lastObservedAt },
]

/** What a custom filter on the Shows list can test. */
export const showFilterFields: FilterField<Show>[] = [
  { key: 'title', label: 'Title', kind: 'string', get: (show) => show.title },
  { key: 'year', label: 'Year', kind: 'number', get: (show) => show.year },
  { key: 'monitored', label: 'Monitored', kind: 'bool', get: (show) => show.monitored !== false },
  { key: 'status', label: 'Status', kind: 'exact', choices: ['Present', 'Missing'], get: (show) => show.status },
  { key: 'seasons', label: 'Seasons', kind: 'number', get: (show) => show.seasons },
  { key: 'episodesPresent', label: 'Episodes present', kind: 'number', get: (show) => show.episodesPresent },
  { key: 'episodesTotal', label: 'Episodes total', kind: 'number', get: (show) => show.episodesTotal },
  { key: 'episodesMissing', label: 'Episodes missing', kind: 'number', get: (show) => show.episodesMissing ?? Math.max(0, show.episodesTotal - show.episodesPresent) },
  { key: 'problems', label: 'Problems', kind: 'number', get: (show) => show.problemCount ?? 0 },
  { key: 'mediaFolder', label: 'Media folder', kind: 'array', get: (show) => show.storageRoots?.map((root) => root.name) ?? [] },
  { key: 'plex', label: 'Plex', kind: 'exact', choices: PLEX, get: (show) => show.plex },
  { key: 'torrentState', label: 'Torrent state', kind: 'exact', get: (show) => show.torrentStatus?.state },
  { key: 'lastObserved', label: 'Last observed', kind: 'date', get: (show) => show.lastObservedAt },
]
