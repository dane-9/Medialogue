import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import type { MediaHistoryItem } from '../api/modules/replacements'
import { formatAge } from '../lib/age'
import { qualityLabel } from '../lib/qualityLabel'
import { Button } from './ui'
import { Icon } from './Icon'
import { Modal } from './Modal'

type SortKey = 'title' | 'finished'

/** Finished upgrades or replacements, as a table in the same style as the
 *  Movies and Shows History: what was replaced, by what, how it ended and
 *  when. Event History has the full detail. */
export function MediaHistoryModal({ load, empty, onClose }: {
  load: () => Promise<MediaHistoryItem[]>
  empty: string
  onClose: () => void
}) {
  const [items, setItems] = useState<MediaHistoryItem[] | null>(null)
  const [error, setError] = useState('')
  // Newest first until a column is chosen.
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({ key: 'finished', descending: true })

  useEffect(() => {
    let alive = true
    load().then((value) => { if (alive) setItems(value) })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load the history.') })
    return () => { alive = false }
  }, [load])

  const sorted = useMemo(() => {
    const value = (item: MediaHistoryItem): string | number => sort.key === 'title'
      ? item.title.toLocaleLowerCase() : new Date(item.finished_at).getTime()
    return [...(items ?? [])].sort((a, b) => {
      const left = value(a), right = value(b)
      const order = typeof left === 'string' ? left.localeCompare(String(right)) : left - Number(right)
      return sort.descending ? -order : order
    })
  }, [items, sort])

  const header = (key: SortKey, text: string, className = '') => <button
    type="button"
    className={`library-history-sort ${className} ${sort.key === key ? 'active' : ''}`}
    aria-sort={sort.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'}
    onClick={() => setSort((current) => current.key === key ? { key, descending: !current.descending } : { key, descending: key !== 'title' })}
  >{text}{sort.key === key && <Icon name="caret" size={10} className={sort.descending ? '' : 'flipped'} />}</button>

  const quality = (value?: string | null) => qualityLabel(value ?? undefined) || value || '?'

  return <Modal
    title="History"
    className="media-history-modal library-history-modal"
    onClose={onClose}
    footer={<>
      <Link className="media-history-link" to="/events" onClick={onClose}>View all in Event History</Link>
      <span className="modal-foot-spacer" />
      {items?.length ? <span className="wizard-foot-count">{items.length} finished</span> : null}
      <Button variant="ghost" onClick={onClose}>Close</Button>
    </>}
  >
    {error ? <p className="media-history-note">{error}</p>
      : !items ? <p className="media-history-note">Loading…</p>
        : !items.length ? <p className="media-history-note">{empty}</p>
          : <div className="library-history-table media-history-table" role="table">
            <div className="library-history-head" role="row">
              {header('title', 'Title')}
              <span className="library-history-sort numeric">Quality</span>
              {header('finished', 'Finished', 'numeric')}
            </div>
            {sorted.map((item) => {
              const details = [item.year ? `${item.year}` : undefined, item.season_number != null ? `Season ${item.season_number}` : undefined].filter(Boolean).join(' · ')
              return <div key={item.id} className="library-history-row" role="row">
                <span className="library-history-title">
                  <span className="library-history-poster"><Icon name={item.media_type === 'shows' ? 'tv-screen' : 'clapper'} size={16} /></span>
                  <span className="library-history-copy">
                    <span className="media-history-title-line"><strong>{item.title}</strong>{details && <small>{details}</small>}</span>
                    <span className="media-history-change">
                      <span className="media-history-old">{item.old_release ?? '—'}</span>
                      <span className="media-history-new"><span className="upgrade-caret" aria-label="replaced by"><Icon name="caret" size={9} /></span>{item.new_release ?? '—'}</span>
                    </span>
                  </span>
                </span>
                <span className="library-history-size media-history-quality">{item.old_quality || item.new_quality
                  ? <>{quality(item.old_quality)}<span className="upgrade-caret" aria-label="to"><Icon name="caret" size={9} /></span>{quality(item.new_quality)}</>
                  : '—'}</span>
                <span className="library-history-when"><span>{formatAge(item.finished_at)}</span><small className="media-history-outcome">{item.outcome}</small></span>
              </div>
            })}
          </div>}
  </Modal>
}
