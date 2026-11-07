import { useEffect, useState } from 'react'
import { downloadsApi } from '../api/modules/downloads'
import { Modal } from './Modal'
import { Button } from './ui'
import { Icon } from './Icon'

type Plan = Awaited<ReturnType<typeof downloadsApi.deletePlan>>

/** Confirm deleting downloads: the folders, every torrent going with them and
 *  what happens to the titles afterwards, all shown before anything is touched. */
export function DeleteDownloadsModal({ downloadIds, onClose, onDone }: {
  downloadIds: string[]
  onClose: () => void
  onDone: (message: string) => void
}) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState('')
  const [library, setLibrary] = useState<'replace' | 'forget'>('replace')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    downloadsApi.deletePlan(downloadIds)
      .then((value) => { if (live) setPlan(value) })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : 'Could not work out what to delete.') })
    return () => { live = false }
  }, [downloadIds])

  const crossSeeds = plan?.torrents.filter((item) => !item.selected) ?? []
  const titleNames = plan?.titles.map((item) => `${item.title}${item.season_numbers?.length ? ` (Season ${item.season_numbers.join(', ')})` : ''}`) ?? []
  const shows = plan?.titles.some((item) => item.kind === 'show')

  const run = async () => {
    setBusy(true); setError('')
    try {
      const done = await downloadsApi.deleteDownloads(downloadIds, library)
      const folders = `${done.folders.length} folder${done.folders.length === 1 ? '' : 's'}`
      const torrents = `${done.torrents.length} torrent${done.torrents.length === 1 ? '' : 's'}`
      const after = !done.titles.length ? ''
        : library === 'replace' ? ' It is listed in Replace Media as Missing.' : ' It was removed from Medialogue.'
      onDone(`Deleted ${folders} and removed ${torrents}.${after}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete the downloads.')
      setBusy(false)
    }
  }

  return <Modal
    title={downloadIds.length === 1 ? 'Delete this download and its files?' : `Delete ${downloadIds.length} downloads and their files?`}
    eyebrow="DOWNLOADS"
    icon="close"
    onClose={onClose}
    footer={<><span className="modal-foot-spacer" /><Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button variant="danger" icon="close" disabled={!plan || plan.blocked.length > 0 || busy} onClick={() => void run()}>Delete</Button></>}
  >
    <div className="delete-downloads">
      {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {!plan && !error && <p className="delete-downloads-note">Working out what would be deleted…</p>}
      {plan && <>
        {plan.blocked.length > 0 && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>
          Nothing can be deleted: {plan.blocked.join(' ')}
        </span></div>}
        <section>
          <h4>Deleted from disk</h4>
          <ul className="delete-downloads-list">{plan.folders.map((folder) => <li key={folder}><Icon name="folder" size={13} /><code>{folder}</code></li>)}</ul>
          <p className="delete-downloads-note">Everything inside goes, including files you added yourself such as subtitles.</p>
        </section>
        <section>
          <h4>Removed from qBittorrent</h4>
          <ul className="delete-downloads-list">{plan.torrents.map((item) => <li key={item.download_id}>
            <Icon name="qbittorrent" size={13} /><span>{item.name}</span><small>{item.client_name}{item.selected ? '' : ' · cross-seed of the same files'}</small>
          </li>)}</ul>
          {crossSeeds.length > 0 && <p className="delete-downloads-note">
            {crossSeeds.length === 1 ? 'A cross-seed shares these files, so it is removed too' : `${crossSeeds.length} cross-seeds share these files, so they are removed too`} rather than left broken.
          </p>}
        </section>
        {plan.titles.length > 0 && <section>
          <h4>Afterwards: {titleNames.join(', ')}</h4>
          <div className="delete-downloads-choices" role="radiogroup">
            <button type="button" role="radio" aria-checked={library === 'replace'} className={`replace-choice ${library === 'replace' ? 'active' : ''}`} onClick={() => setLibrary('replace')}>
              <Icon name="refresh" size={16} /><strong>Find a replacement</strong>
              <span>Keep it in Medialogue and list it in Replace Media as Missing, where a new torrent is searched for.</span>
            </button>
            <button type="button" role="radio" aria-checked={library === 'forget'} className={`replace-choice danger ${library === 'forget' ? 'active' : ''}`} onClick={() => setLibrary('forget')}>
              <Icon name="close" size={16} /><strong>Remove from Medialogue</strong>
              <span>{shows
                ? 'Forget these releases and stop wanting the season. The show itself goes only when nothing of it is left.'
                : 'Remove the movie from the library entirely.'}</span>
            </button>
          </div>
        </section>}
      </>}
    </div>
  </Modal>
}
