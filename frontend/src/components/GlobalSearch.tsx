import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { moviesApi } from '../api/modules/movies'
import { showsApi } from '../api/modules/shows'
import type { Movie, Show, TMDBMovieLookup, TMDBShowLookup } from '../types'
import { evaluateShiftTap } from '../lib/doubleTap'
import { rankResults } from '../lib/searchRank'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'
import { MovieDownloadWizard } from './MovieDownloadWizard'
import { ShowDownloadWizard } from './ShowDownloadWizard'
import { Badge } from './ui'
import imdbLogo from '../assets/imdb.svg'

type Kind = 'movie' | 'show'

type Scope = Kind | 'all'

type Result = {
  key: string

  index: number
  imdbId?: string
  imdbRating?: number
  imdbVotes?: number
  voteCount?: number
  voteAverage?: number
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

export function GlobalSearch({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<Scope>('all')
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [active, setActive] = useState(0)
  const [downloadTarget, setDownloadTarget] = useState<Result | null>(null)
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
        const merged = scope === 'movie' ? await searchMovies(term)
          : scope === 'show' ? await searchShows(term)
          : await searchEverything(term)
        if (!alive) return
        setResults(merged); setActive(0); setError('')
      } catch (reason) {
        if (!alive) return
        setResults([])
        setError(reason instanceof Error ? reason.message : 'Search failed.')
      } finally { if (alive) setLoading(false) }
    }, 260)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [query, scope])

  const open = async (result: Result) => {
    if (result.libraryId) {
      navigate(result.kind === 'movie' ? `/movies/${result.libraryId}` : `/shows/${result.libraryId}`)
      onClose()
      return
    }
    if (!result.tmdbId) return
    setDownloadTarget(result)
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive((value) => Math.min(value + 1, results.length - 1)) }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActive((value) => Math.max(value - 1, 0)) }
    if (event.key === 'Enter' && results[active]) { event.preventDefault(); void open(results[active]) }
  }

  const hint = useMemo(() => {
    if (loading) return 'Searching TMDB…'
    if (!query.trim()) return 'Search every title on TMDB. Anything already in your library is marked Present.'
    return 'No matches.'
  }, [loading, query])

  const count = useMemo(() => {
    if (!query.trim()) return ''
    if (loading) return 'Searching…'
    if (!results.length) return 'No matches.'
    return `${results.length} result${results.length === 1 ? '' : 's'}`
  }, [loading, query, results.length])

  if (downloadTarget?.tmdbId && downloadTarget.kind === 'movie') return <MovieDownloadWizard
    movie={{ tmdbId: downloadTarget.tmdbId, title: downloadTarget.title, year: downloadTarget.year, poster: downloadTarget.poster, overview: downloadTarget.overview, imdbId: downloadTarget.imdbId }}
    onBack={() => setDownloadTarget(null)}
    onClose={onClose}
    onComplete={(movieId) => { navigate(`/movies/${movieId}`); onClose() }}
  />

  if (downloadTarget?.tmdbId && downloadTarget.kind === 'show') return <ShowDownloadWizard
    show={{ tmdbId: downloadTarget.tmdbId, title: downloadTarget.title, year: downloadTarget.year, poster: downloadTarget.poster, overview: downloadTarget.overview, imdbId: downloadTarget.imdbId }}
    onBack={() => setDownloadTarget(null)}
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
          placeholder={scope === 'movie' ? 'Search for a movie…' : scope === 'show' ? 'Search for a show…' : 'Search for a movie or show…'}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="filter-tabs">
          <button className={scope === 'all' ? 'active' : ''} onClick={() => setScope('all')}>All</button>
          <button className={scope === 'movie' ? 'active' : ''} onClick={() => setScope('movie')}>
            <span className="kind-box kind-movie"><Icon name="clapper" size={12} />Movies</span>
          </button>
          <button className={scope === 'show' ? 'active' : ''} onClick={() => setScope('show')}>
            <span className="kind-box kind-show"><Icon name="tv-screen" size={12} />Shows</span>
          </button>
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
              : <Icon name={result.kind === 'movie' ? 'clapper' : 'tv-screen'} size={18} />}
          </span>
          <span className="global-search-copy">
            <strong>
              <Tooltip asChild content={result.kind === 'movie' ? 'Movie' : 'Show'}>
                <span className={`kind-box kind-${result.kind}`} aria-label={result.kind === 'movie' ? 'Movie' : 'Show'}>
                  <Icon name={result.kind === 'movie' ? 'clapper' : 'tv-screen'} size={13} />
                </span>
              </Tooltip>
              <span className="global-search-title">{result.title}</span>
              <span className={`global-search-year ${result.year ? '' : 'unknown'}`}>{result.year || 'Year unknown'}</span>
              {result.imdbRating != null &&
                <Tooltip asChild content={result.imdbVotes ? `${result.imdbVotes.toLocaleString()} IMDb ratings` : 'IMDb rating'}>
                  <span className="global-search-rating"><img src={imdbLogo} alt="IMDb" /> {result.imdbRating.toFixed(1)}</span>
                </Tooltip>}
            </strong>
            {result.overview && <small>{result.overview}</small>}
          </span>
          <span className="global-search-state">
            {result.present
              ? <Badge tone={result.state === 'Missing' ? 'amber' : 'green'}>{result.state ?? 'Present'}</Badge>
              : <Badge tone="neutral">Not in library</Badge>}
          </span>
        </button>)}
        {!results.length && <div className="global-search-empty">{hint}</div>}
      </div>

      <div className="global-search-foot">
        <span>{count}</span>
        <span className="global-search-keys"><kbd>↑</kbd><kbd>↓</kbd> navigate <kbd>enter</kbd> select <kbd>esc</kbd> close</span>
      </div>
    </div>
  </div>
}

async function searchEverything(term: string): Promise<Result[]> {
  const [movies, shows] = await Promise.all([searchMovies(term), searchShows(term)])
  return rankResults([...movies, ...shows], term)
}

async function searchMovies(term: string): Promise<Result[]> {
  const [library, tmdb] = await Promise.all([
    moviesApi.movies(term).catch(() => [] as Movie[]),
    moviesApi.lookupMovies(term).catch(() => [] as TMDBMovieLookup[]),
  ])
  const byTmdb = new Map<number, Movie>()
  library.forEach((movie) => { if (movie.tmdbId) byTmdb.set(movie.tmdbId, movie) })

  const rows: Result[] = tmdb.map((match, index) => {
    const owned = byTmdb.get(match.tmdbId)
    return {
      key: `tmdb-${match.tmdbId}`,
      index,
      imdbId: match.imdbId,
      imdbRating: match.imdbRating,
      imdbVotes: match.imdbVotes,
      voteCount: match.voteCount,
      voteAverage: match.voteAverage,
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

  library.forEach((movie) => {
    if (movie.tmdbId && rows.some((row) => row.tmdbId === movie.tmdbId)) return
    rows.unshift({ key: `library-${movie.id}`, index: 0, kind: 'movie', libraryId: movie.id, tmdbId: movie.tmdbId, title: movie.title, year: movie.year, poster: movie.poster, present: true, state: movie.status })
  })
  return rows
}

async function searchShows(term: string): Promise<Result[]> {
  const [library, tmdb] = await Promise.all([
    showsApi.shows(term).catch(() => [] as Show[]),
    showsApi.lookupShows(term).catch(() => [] as TMDBShowLookup[]),
  ])
  const byTmdb = new Map<number, Show>()
  library.forEach((show) => { if (show.tmdbId) byTmdb.set(show.tmdbId, show) })

  const rows: Result[] = tmdb.map((match, index) => {
    const owned = byTmdb.get(match.tmdbId)
    return {
      key: `tmdb-${match.tmdbId}`,
      index,
      imdbId: match.imdbId,
      imdbRating: match.imdbRating,
      imdbVotes: match.imdbVotes,
      voteCount: match.voteCount,
      voteAverage: match.voteAverage,
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
    rows.unshift({ key: `library-${show.id}`, index: 0, kind: 'show', libraryId: show.id, tmdbId: show.tmdbId, title: show.title, year: show.year, poster: show.poster, present: true, state: show.status })
  })
  return rows
}

export function GlobalSearchButton({ onOpen }: { onOpen: () => void }) {
  useEffect(() => {
    // Held in a closure rather than state: this fires on every keystroke and
    // must not re-render the shell.
    let lastShiftAt: number | null = null

    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        lastShiftAt = null
        onOpen()
        return
      }
      const result = evaluateShiftTap(event, lastShiftAt)
      lastShiftAt = result.lastTapAt
      if (result.triggered) onOpen()
    }

    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onOpen])

  return <Tooltip asChild content="Search (Ctrl+K, or double-tap Shift)"><button className="brand-search" onClick={onOpen} aria-label="Search movies and shows">
    <Icon name="search" size={16} />
  </button></Tooltip>
}
