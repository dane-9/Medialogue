import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { MediaHistoryItem } from '../api/modules/replacements'
import { formatAge } from '../lib/age'
import { qualityLabel } from '../lib/qualityLabel'
import { Button } from './ui'
import { Icon } from './Icon'
import { Modal } from './Modal'

/** Finished upgrades or replacements, newest first: what was replaced, by
 *  what, how it ended and when. Event History has the full detail. */
export function MediaHistoryModal({ title, load, empty, onClose }: {
  title: string
  load: () => Promise<MediaHistoryItem[]>
  empty: string
  onClose: () => void
}) {
  const [items, setItems] = useState<MediaHistoryItem[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    load().then((value) => { if (alive) setItems(value) })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load the history.') })
    return () => { alive = false }
  }, [load])

  return <Modal
    title={title}
    eyebrow="HISTORY"
    className="media-history-modal"
    onClose={onClose}
    footer={<><Link className="media-history-link" to="/events" onClick={onClose}>View all in Event History</Link><span className="modal-foot-spacer" /><Button variant="ghost" onClick={onClose}>Close</Button></>}
  >
    {error ? <p className="media-history-note">{error}</p>
      : !items ? <p className="media-history-note">Loading…</p>
        : !items.length ? <p className="media-history-note">{empty}</p>
          : <ul className="media-history-list">{items.map((item) => <li key={item.id}>
            <div className="media-history-head">
              <strong>{item.title}</strong>
              <small>{[item.year ? `${item.year}` : undefined, item.season_number != null ? `Season ${item.season_number}` : undefined].filter(Boolean).join(' · ')}</small>
              <span className="media-history-when">{formatAge(item.finished_at)}</span>
            </div>
            <div className="media-history-change">
              <span className="media-history-old">{item.old_release ?? '—'}</span>
              <Icon name="arrow" size={13} />
              <span className="media-history-new">{item.new_release ?? '—'}</span>
            </div>
            <div className="media-history-foot">
              {(item.old_quality || item.new_quality) && <small>{qualityLabel(item.old_quality ?? undefined) || item.old_quality || '?'} → {qualityLabel(item.new_quality ?? undefined) || item.new_quality || '?'}</small>}
              <span className="media-history-outcome">{item.outcome}</span>
            </div>
          </li>)}</ul>}
  </Modal>
}
