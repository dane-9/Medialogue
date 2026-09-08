import { useEffect, useState } from 'react'
import { moviesApi } from '../api/modules/movies'
import type { TMDBMovieLookup } from '../types'
import { Icon } from './Icon'
import { Modal } from './Modal'
import { Button, Input } from './ui'

function posterUrl(reference?: string) {
  if (!reference) return undefined
  if (reference.startsWith('http')) return reference
  return `https://image.tmdb.org/t/p/w185${reference.startsWith('/') ? reference : `/${reference}`}`
}

/** Re-point a movie at a different TMDB title.
 *
 * Identity only: the library record keeps its id, files, releases and torrents.
 * Nothing on disk is renamed, moved or deleted.
 */
export function ChangeMatchModal({ movieId, title, year, currentTmdbId, onClose, onMatched }: {
  movieId: string
  title: string
  year?: number
  currentTmdbId?: number
  onClose: () => void
  onMatched: (message: string) => void
}) {
  const [query, setQuery] = useState(title)
  const [results, setResults] = useState<TMDBMovieLookup[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [chosen, setChosen] = useState<number | null>(null)
  const [error, setError] = useState('')

  const search = async (term: string) => {
    if (!term.trim()) return
    setSearching(true); setError(''); setChosen(null)
    try { setResults(await moviesApi.lookupMovies(term.trim())) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not search TMDB.'); setResults([]) }
    finally { setSearching(false) }
  }

  // Seeded with the current title, so the likely candidates are already listed.
  useEffect(() => { void search(title) }, [title])

  const apply = async () => {
    if (chosen === null) return
    setSaving(true); setError('')
    try {
      const updated = await moviesApi.rematchMovie(movieId, chosen)
      onMatched(`Now matched to ${updated.title}${updated.year ? ` (${updated.year})` : ''}.`)
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not change the match.')
    } finally { setSaving(false) }
  }

  return <Modal
    wide
    className="change-match-modal"
    title={`${title}${year ? ` (${year})` : ''}`}
    eyebrow="CHANGE MATCH"
    onClose={onClose}
    footer={<>
      <span className="modal-foot-spacer" />
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button variant="primary" icon="check" disabled={chosen === null || saving} onClick={() => void apply()}>{saving ? 'Applying…' : 'Use this match'}</Button>
    </>}
  >
    <div className="change-match-body">
      <div className="settings-note">
        <Icon name="info" size={15} />
        <span>Only the identity changes. This movie keeps its files, releases and torrents, and nothing on disk is renamed or moved.</span>
      </div>
      <form className="change-match-search" onSubmit={(event) => { event.preventDefault(); void search(query) }}>
        <div className="search-field">
          <Icon name="search" size={15} />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search TMDB by title…" aria-label="Search TMDB" />
        </div>
        <Button variant="secondary" type="submit" disabled={searching || !query.trim()}>{searching ? 'Searching…' : 'Search'}</Button>
      </form>
      {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
      <div className="change-match-results" role="radiogroup" aria-label="TMDB matches">
        {results?.map((result) => {
          const isCurrent = result.tmdbId === currentTmdbId
          return <button
            type="button"
            role="radio"
            aria-checked={chosen === result.tmdbId}
            key={result.tmdbId}
            className={`change-match-option ${chosen === result.tmdbId ? 'selected' : ''}`}
            disabled={isCurrent || saving}
            onClick={() => setChosen(result.tmdbId)}
          >
            <span className="change-match-poster">{posterUrl(result.posterRef)
              ? <img src={posterUrl(result.posterRef)} alt="" />
              : <Icon name="clapper" size={18} />}</span>
            <span className="change-match-copy">
              <strong>{result.title}{result.year ? ` (${result.year})` : ''}</strong>
              <small>TMDB {result.tmdbId}{result.director ? ` · ${result.director}` : ''}</small>
              {result.overview && <span>{result.overview}</span>}
            </span>
            {isCurrent && <span className="change-match-current">Current match</span>}
          </button>
        })}
        {results && !results.length && !searching && <p className="provider-empty-copy">No TMDB titles matched that search.</p>}
        {searching && !results?.length && <p className="provider-empty-copy">Searching TMDB…</p>}
      </div>
    </div>
  </Modal>
}
