import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MovieDownloadWizard } from './MovieDownloadWizard'
import { ShowDownloadWizard } from './ShowDownloadWizard'
import { Tooltip } from './Tooltip'
import { openApiClient, openApiData } from '../api/transport'
import type { components } from '../api/generated/schema'
import { formatAge } from '../lib/age'
import { qualityLabel } from '../lib/qualityLabel'
import { formatBytes } from './ReleaseResults'
import { Button } from './ui'
import { Icon } from './Icon'
import { Modal } from './Modal'
import { tmdbPosterUrl } from '../pages/shared'

type HistoryItem = components['schemas']['LibraryHistoryItem']
type SortKey = 'title' | 'size' | 'removed'

export const libraryHistory = (mediaType: 'movies' | 'shows') =>
  openApiData(openApiClient.GET('/api/v1/library/history', { params: { query: { media_type: mediaType } } }))

/** Movies or shows that left the library, newest first: what it was, what was
 *  on disk, how and when it went. */
export function LibraryHistoryModal({ mediaType, onClose }: { mediaType: 'movies' | 'shows'; onClose: () => void }) {
  const navigate = useNavigate()
  const [items, setItems] = useState<HistoryItem[] | null>(null)
  const [error, setError] = useState('')
  // Adding a title back runs the same search as adding one from TMDB: pick
  // the media folder, qBittorrent client and release.
  const [adding, setAdding] = useState<HistoryItem | null>(null)
  // Newest removals first until a column is chosen.
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({ key: 'removed', descending: true })
  const sorted = useMemo(() => {
    const value = (item: HistoryItem): string | number => sort.key === 'title' ? item.title.toLocaleLowerCase()
      : sort.key === 'size' ? item.size ?? -1 : new Date(item.removed_at).getTime()
    return [...(items ?? [])].sort((a, b) => {
      const left = value(a), right = value(b)
      const order = typeof left === 'string' ? left.localeCompare(String(right)) : left - Number(right)
      return sort.descending ? -order : order
    })
  }, [items, sort])

  useEffect(() => {
    let alive = true
    libraryHistory(mediaType).then((value) => { if (alive) setItems(value) })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load the history.') })
    return () => { alive = false }
  }, [mediaType])

  const noun = mediaType === 'movies' ? 'movies' : 'shows'
  if (adding?.tmdb_id) {
    const choice = { tmdbId: adding.tmdb_id, title: adding.title, year: adding.year ?? undefined, poster: adding.poster_ref ?? undefined }
    return mediaType === 'movies'
      ? <MovieDownloadWizard movie={choice} onBack={() => setAdding(null)} onClose={onClose} onComplete={(id) => { navigate(`/movies/${id}`); onClose() }} />
      : <ShowDownloadWizard show={choice} onBack={() => setAdding(null)} onClose={onClose} onComplete={(id) => { navigate(`/shows/${id}`); onClose() }} />
  }
  const label = mediaType === 'movies' ? 'Movie' : 'Show'
  const header = (key: SortKey, text: string, className = '') => <button
    type="button"
    className={`library-history-sort ${className} ${sort.key === key ? 'active' : ''}`}
    aria-sort={sort.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'}
    onClick={() => setSort((current) => current.key === key ? { key, descending: !current.descending } : { key, descending: key !== 'title' })}
  >{text}{sort.key === key && <Icon name="caret" size={10} className={sort.descending ? '' : 'flipped'} />}</button>

  return <Modal
    title="History"
    className="media-history-modal library-history-modal"
    onClose={onClose}
    footer={<><span className="wizard-foot-count">{items?.length ? `${items.length} ${items.length === 1 ? label.toLowerCase() : noun}` : ''}</span><span className="modal-foot-spacer" /><Button variant="ghost" onClick={onClose}>Close</Button></>}
  >
    {error ? <p className="media-history-note">{error}</p>
      : !items ? <p className="media-history-note">Loading…</p>
        : !items.length ? <p className="media-history-note">No {noun} have been removed from the library yet.</p>
          : <div className="library-history-table" role="table">
            <div className="library-history-head" role="row">
              {header('title', label)}
              {header('size', 'Size', 'numeric')}
              {header('removed', 'Removed', 'numeric')}
              <span aria-hidden="true" />
            </div>
            {sorted.map((item) => {
              const poster = tmdbPosterUrl(item.poster_ref ?? undefined)
              const seasons = item.season_numbers?.length ? `Season ${item.season_numbers.join(', ')}` : undefined
              const details = [item.year ? `${item.year}` : undefined, seasons, qualityLabel(item.quality ?? undefined) || item.quality].filter(Boolean).join(' · ')
              return <div key={item.id} className="library-history-row" role="row">
                <span className="library-history-title">
                  <span className="library-history-poster">{poster
                    ? <img src={poster} alt="" loading="lazy" referrerPolicy="no-referrer" />
                    : <Icon name={mediaType === 'movies' ? 'clapper' : 'tv-screen'} size={16} />}</span>
                  <span className="library-history-copy">
                    <strong>{item.title}</strong>
                    <small>{details}</small>
                    {item.release_name && <span className="library-history-release">{item.release_name}</span>}
                  </span>
                </span>
                <span className="library-history-size">{item.size ? formatBytes(item.size) : '—'}</span>
                <span className="library-history-when"><span>{formatAge(item.removed_at)}</span><small>{item.reason}</small></span>
                <span className="library-history-action"><Tooltip asChild content={item.tmdb_id
                  ? `Restore: choose the qBittorrent client and a release to download and restore the ${mediaType === 'movies' ? 'movie' : 'show'}`
                  : 'Cannot be restored: it was never matched to TMDB, so there is nothing to search for'}>
                  <button type="button" className="icon-button library-history-add" aria-label={`Restore ${item.title}`} disabled={!item.tmdb_id} onClick={() => setAdding(item)}><Icon name="open-in" size={16} /></button>
                </Tooltip></span>
              </div>
            })}
          </div>}
  </Modal>
}
