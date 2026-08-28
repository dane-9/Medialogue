import type { TMDBMovieLookup, TMDBShowLookup } from '../../types'
import type { components } from '../generated/schema'

type TMDBShowLookupDto = components['schemas']['TMDBShowLookupResponse']
type TMDBMovieLookupDto = components['schemas']['TMDBMovieLookupResponse']

export function mapTMDBShowLookup(value: TMDBShowLookupDto): TMDBShowLookup {
  return {
    tmdbId: value.tmdb_id,
    title: value.title,
    originalTitle: value.original_title ?? undefined,
    year: value.year ?? undefined,
    overview: value.overview ?? undefined,
    posterRef: value.poster_ref ?? undefined,
    imdbId: value.imdb_id ?? undefined,
    imdbRating: value.imdb_rating ?? undefined,
    imdbVotes: value.imdb_votes ?? undefined,
    popularity: value.popularity ?? undefined,
    voteCount: value.vote_count ?? undefined,
    voteAverage: value.vote_average ?? undefined,
    director: value.director ?? undefined,
    cast: value.cast,
  }
}

export function mapTMDBMovieLookup(value: TMDBMovieLookupDto): TMDBMovieLookup {
  return {
    tmdbId: value.tmdb_id,
    title: value.title,
    originalTitle: value.original_title ?? undefined,
    year: value.year ?? undefined,
    overview: value.overview ?? undefined,
    posterRef: value.poster_ref ?? undefined,
    imdbId: value.imdb_id ?? undefined,
    imdbRating: value.imdb_rating ?? undefined,
    imdbVotes: value.imdb_votes ?? undefined,
    popularity: value.popularity ?? undefined,
    voteCount: value.vote_count ?? undefined,
    voteAverage: value.vote_average ?? undefined,
    director: value.director ?? undefined,
    cast: value.cast,
  }
}
