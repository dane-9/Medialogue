import { useState } from 'react'
import { moviesApi } from '../api/modules/movies'
import { ApiError } from '../api/transport'
import type { MovieRelease } from '../types'
import { TitleSearch } from './GlobalSearch'
import type { TitleSearchResult } from './GlobalSearch'
import { Icon } from './Icon'
import { Modal } from './Modal'
import { Button } from './ui'

export interface ChangeMatchOutcome {
  /** The movie the releases are in now. */
  movieId: string
  message: string
  /** The movie they left is gone, so its page is too. */
  sourceRemoved: boolean
}

/** The title a release name is about: everything before its year. The last
 *  year-like word is the year, so a title such as "Blade Runner 2049" stays whole. */
export function releaseSearchTerm(name: string, fallback: string) {
  const words = name.replace(/\.[a-z0-9]{2,4}$/i, '').split(/[.\s_]+/).filter(Boolean)
  const isYear = (word: string) => /^(19|20)\d{2}$/.test(word.replace(/[()[\]]/g, ''))
  const year = words.reduce((found, word, index) => index > 0 && isYear(word) ? index : found, -1)
  const title = (year > 0 ? words.slice(0, year) : words.slice(0, 6)).join(' ').trim()
  return title || fallback
}

/** Change match: move releases to the movie they really are, picked in the
 *  same search as global search. The movie a release leaves goes when it has
 *  nothing left, and the user is asked first if anything says it was wanted. */
export function ChangeMatchModal({ movie, releases, onClose, onDone }: {
  movie: { id: string; title: string; year?: number; tmdbId?: number }
  /** The releases being moved, picked on their rows. */
  releases: MovieRelease[]
  onClose: () => void
  onDone: (outcome: ChangeMatchOutcome) => void
}) {
  const [step, setStep] = useState<'search' | 'decide'>('search')
  const [target, setTarget] = useState<TitleSearchResult | null>(null)
  const [decision, setDecision] = useState<{ reasons: string[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const moving = releases
  const label = `${movie.title}${movie.year ? ` (${movie.year})` : ''}`
  const named = (result: TitleSearchResult) => `${result.title}${result.year ? ` (${result.year})` : ''}`

  const apply = async (result: TitleSearchResult, removeEmptied?: boolean) => {
    if (!result.tmdbId || !moving.length) return
    setBusy(true); setError(''); setTarget(result)
    try {
      const outcome = await moviesApi.rematchReleases(movie.id, moving.map((release) => release.id), result.tmdbId, removeEmptied)
      const what = moving.length === 1 ? moving[0].name : `${moving.length} releases`
      onDone({
        movieId: outcome.movie_id,
        sourceRemoved: outcome.source_removed,
        message: `Moved ${what} to ${outcome.title}${outcome.year ? ` (${outcome.year})` : ''}${outcome.source_removed ? `, and removed ${label}, which had nothing else` : ''}.`,
      })
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'REMATCH_KEEP_OR_REMOVE') {
        const reasons = Array.isArray(reason.details?.reasons) ? reason.details.reasons.map(String) : []
        setDecision({ reasons }); setStep('decide')
      } else {
        setError(reason instanceof Error ? reason.message : 'Could not change the match.')
      }
    } finally { setBusy(false) }
  }

  if (step === 'decide' && target) return <Modal
    title={`Keep ${label}?`}
    icon="alert"
    onClose={onClose}
    footer={<>
      <span className="modal-foot-spacer" />
      <Button variant="ghost" disabled={busy} onClick={() => void apply(target, false)}>Keep it</Button>
      <Button variant="danger" disabled={busy} onClick={() => void apply(target, true)}>{busy ? 'Moving…' : 'Remove it'}</Button>
    </>}
  >
    <div className="change-match-decide">
      <p>Once {moving.length === 1 ? 'this release moves' : 'these releases move'} to <strong>{named(target)}</strong>, {label} has no releases left.</p>
      {decision?.reasons.length ? <ul>{decision.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : null}
      <p>Keep it in the library as a movie you want, or remove it. Removing it leaves no history, since it was never really there.</p>
      {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
    </div>
  </Modal>

  const first = moving[0]
  return <TitleSearch
    kind="movie"
    initialQuery={first ? releaseSearchTerm(first.name, movie.title) : movie.title}
    currentTmdbId={movie.tmdbId}
    busy={busy}
    onClose={onClose}
    onPick={(result) => void apply(result)}
    banner={<>
      <Icon name={error ? 'alert' : 'info'} size={14} />
      <span>{error || (busy && target ? `Moving to ${named(target)}…`
        : <>Pick the movie <strong>{moving.length === 1 ? first?.name : `these ${moving.length} releases`}</strong> really {moving.length === 1 ? 'is' : 'are'}. Files, folders and torrents go with {moving.length === 1 ? 'it' : 'them'}; nothing on disk changes.</>)}</span>
    </>}
  />
}
