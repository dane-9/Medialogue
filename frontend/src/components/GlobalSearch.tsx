import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Movie, Show, TMDBMovieLookup, TMDBShowLookup } from '../types'
import { Icon } from './Icon'
import { Badge, Button } from './ui'
import { MovieAcquisitionWizard } from './MovieAcquisitionWizard'
import { ShowAcquisitionWizard } from './ShowAcquisitionWizard'

type Kind = 'movie' | 'show'

/**
 * One row in the result list. A title already in the library carries its
 * internal id; a TMDB-only movie carries just the tmdbId and can enter the
 * manual acquisition flow without creating a library Movie first.
 */
type Result = {
  key: string
  kind: Kind
  tmdbId?: number
  libraryId?: string
  title: string
  year?: number
  poster?: string
  overview?: string
  present: boolean
  state?: string
}

const posterUrl = (reference?: string) => {
  if (!reference) return undefined
  if (reference.startsWith('http')) return reference
  return `https://image.tmdb.org/t/p/w185${reference.startsWith('/') ? reference : `/${reference}`}`
}

/**
 * Global search: TMDB first, library merged on top.
 *
 * The library is not the search index — it is an annotation on the results. You
 * search everything TMDB knows about, and anything you already have is marked
 * Present so you can tell at a glance without it limiting what you can find.
 */
export function GlobalSearch({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<Kind>('movie')
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [active, setActive] = useState(0)
  const [acquisition, setAcquisition] = useState<Result | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    const term = query.trim()
    if (!term) { setResults([]); setError(''); setLoading(false); return }
    let alive = true
    setLoading(true)
    const timer = window.setTimeout(async () => {
      try {
        const merged = kind === 'movie' ? await searchMovies(term) : await searchShows(term)
        if (!alive) return
        setResults(merged); setActive(0); setError('')
      } catch (reason) {
        if (!alive) return
        setResults([])
        setError(reason instanceof Error ? reason.message : 'Search failed.')
      } finally { if (alive) setLoading(false) }
    }, 260)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [query, kind])

  const open = async (result: Result) => {
    if (result.libraryId) {
      navigate(result.kind === 'movie' ? `/movies/${result.libraryId}` : `/shows/${result.libraryId}`)
      onClose()
      return
    }
    if (!result.tmdbId) return
    setAcquisition(result)
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive((value) => Math.min(value + 1, results.length - 1)) }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActive((value) => Math.max(value - 1, 0)) }
    if (event.key === 'Enter' && results[active]) { event.preventDefault(); void open(results[active]) }
  }

  const hint = useMemo(() => {
    if (loading) return 'Searching TMDB…'
    if (!query.trim()) return 'Search every title on TMDB. Anything already in your library is marked Present.'
    if (!results.length) return 'No matches.'
    return `${results.length} result${results.length === 1 ? '' : 's'}`
  }, [loading, query, results.length])

  if (acquisition?.tmdbId && acquisition.kind === 'movie') return <MovieAcquisitionWizard
    movie={{ tmdbId: acquisition.tmdbId, title: acquisition.title, year: acquisition.year, poster: acquisition.poster, overview: acquisition.overview }}
    onBack={() => setAcquisition(null)}
    onClose={onClose}
    onComplete={(movieId) => { navigate(`/movies/${movieId}`); onClose() }}
  />

  if (acquisition?.tmdbId && acquisition.kind === 'show') return <ShowAcquisitionWizard
    show={{ tmdbId: acquisition.tmdbId, title: acquisition.title, year: acquisition.year, poster: acquisition.poster, overview: acquisition.overview }}
    onBack={() => setAcquisition(null)}
    onClose={onClose}
    onComplete={(showId) => { navigate(`/shows/${showId}`); onClose() }}
  />

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="global-search" onClick={(event) => event.stopPropagation()} role="dialog" aria-label="Search">
      <div className="global-search-head">
        <Icon name="search" size={18} />
        <input
          ref={inputRef}
          className="global-search-input"
          value={query}
          placeholder={kind === 'movie' ? 'Search for a movie…' : 'Search for a show…'}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="filter-tabs">
          <button className={kind === 'movie' ? 'active' : ''} onClick={() => setKind('movie')}>Movies</button>
          <button className={kind === 'show' ? 'active' : ''} onClick={() => setKind('show')}>Shows</button>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Close search"><Icon name="close" size={18} /></button>
      </div>

      {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}

      <div className="global-search-results">
        {results.map((result, index) => <button
          key={result.key}
          className={`global-search-row ${index === active ? 'active' : ''}`}
          onMouseEnter={() => setActive(index)}
          onClick={() => void open(result)}
        >
          <span className="global-search-poster">
            {posterUrl(result.poster)
              ? <img src={posterUrl(result.poster)} alt="" loading="lazy" />
              : <Icon name={result.kind === 'movie' ? 'film' : 'tv'} size={18} />}
          </span>
          <span className="global-search-copy">
            <strong>{result.title}</strong>
            <span>{[result.year || 'Year unknown', result.tmdbId ? `TMDB ${result.tmdbId}` : null].filter(Boolean).join(' · ')}</span>
            {result.overview && <small>{result.overview}</small>}
          </span>
          <span className="global-search-state">
            {result.present
              ? <Badge tone={result.state === 'Missing' ? 'amber' : 'green'}>{result.state ?? 'Present'}</Badge>
              : <Badge tone="neutral">Not in library</Badge>}
            <span className="global-search-action">{result.libraryId ? 'Open' : 'Select'}</span>
          </span>
        </button>)}
        {!results.length && <div className="global-search-empty">{hint}</div>}
      </div>

      <div className="global-search-foot">
        <span>{hint}</span>
        <span className="global-search-keys"><kbd>↑</kbd><kbd>↓</kbd> navigate <kbd>enter</kbd> select <kbd>esc</kbd> close</span>
      </div>
    </div>
  </div>
}

async function searchMovies(term: string): Promise<Result[]> {
  const [library, tmdb] = await Promise.all([
    api.movies(term).catch(() => [] as Movie[]),
    api.lookupMovies(term).catch(() => [] as TMDBMovieLookup[]),
  ])
  const byTmdb = new Map<number, Movie>()
  library.forEach((movie) => { if (movie.tmdbId) byTmdb.set(movie.tmdbId, movie) })

  const rows: Result[] = tmdb.map((match) => {
    const owned = byTmdb.get(match.tmdbId)
    return {
      key: `tmdb-${match.tmdbId}`,
      kind: 'movie',
      tmdbId: match.tmdbId,
      libraryId: owned?.id,
      title: owned?.title ?? match.title,
      year: owned?.year ?? match.year,
      poster: owned?.poster ?? match.posterRef,
      overview: match.overview,
      present: Boolean(owned),
      state: owned?.status,
    }
  })

  // A library title TMDB did not return — an unmatched local discovery, say —
  // still belongs in the results rather than disappearing.
  library.forEach((movie) => {
    if (movie.tmdbId && rows.some((row) => row.tmdbId === movie.tmdbId)) return
    rows.unshift({ key: `library-${movie.id}`, kind: 'movie', libraryId: movie.id, tmdbId: movie.tmdbId, title: movie.title, year: movie.year, poster: movie.poster, present: true, state: movie.status })
  })
  return rows
}

async function searchShows(term: string): Promise<Result[]> {
  const [library, tmdb] = await Promise.all([
    api.shows(term).catch(() => [] as Show[]),
    api.lookupShows(term).catch(() => [] as TMDBShowLookup[]),
  ])
  const byTmdb = new Map<number, Show>()
  library.forEach((show) => { if (show.tmdbId) byTmdb.set(show.tmdbId, show) })

  const rows: Result[] = tmdb.map((match) => {
    const owned = byTmdb.get(match.tmdbId)
    return {
      key: `tmdb-${match.tmdbId}`,
      kind: 'show',
      tmdbId: match.tmdbId,
      libraryId: owned?.id,
      title: owned?.title ?? match.title,
      year: owned?.year ?? match.year,
      poster: owned?.poster ?? match.posterRef,
      overview: match.overview,
      present: Boolean(owned),
      state: owned?.status,
    }
  })
  library.forEach((show) => {
    if (show.tmdbId && rows.some((row) => row.tmdbId === show.tmdbId)) return
    rows.unshift({ key: `library-${show.id}`, kind: 'show', libraryId: show.id, tmdbId: show.tmdbId, title: show.title, year: show.year, poster: show.poster, present: true, state: show.status })
  })
  return rows
}

/** Trigger that lives beside the brand mark and opens the modal. */
export function GlobalSearchButton({ onOpen }: { onOpen: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); onOpen() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onOpen])
  return <button className="brand-search" onClick={onOpen} aria-label="Search movies and shows" title="Search (Ctrl+K)">
    <Icon name="search" size={16} />
  </button>
}
