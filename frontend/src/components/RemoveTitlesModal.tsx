import { useEffect, useState } from 'react'
import { libraryApi, type LibraryRemoveMode } from '../api/modules/library'
import { ApiError } from '../api/transport'
import { Modal } from './Modal'
import { Button } from './ui'
import { Icon } from './Icon'

type Plan = Awaited<ReturnType<typeof libraryApi.removePlan>>

/** More titles than this must be acknowledged before their files are deleted. */
const ACKNOWLEDGE_ABOVE = 5

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`

/** Confirm removing movies or shows: every title, folder and torrent that goes
 *  is listed before anything is touched. */
export function RemoveTitlesModal({ kind, ids, onClose, onDone }: {
  kind: 'movie' | 'show'
  ids: string[]
  onClose: () => void
  onDone: (message: string, tone?: 'ok' | 'error') => void
}) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<LibraryRemoveMode>('forget')
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const titles = kind === 'movie' ? { movieIds: ids } : { showIds: ids }

  useEffect(() => {
    let live = true
    libraryApi.removePlan(kind === 'movie' ? { movieIds: ids } : { showIds: ids })
      .then((value) => { if (live) setPlan(value) })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : 'Could not work out what to remove.') })
    return () => { live = false }
  }, [kind, ids])

  const deleting = mode !== 'keep_files'
  const count = plan?.titles.length ?? ids.length
  const noun = plural(count, kind)
  const blocked = deleting && (plan?.blocked.length ?? 0) > 0
  const mustAcknowledge = deleting && count > ACKNOWLEDGE_ABOVE
  const crossSeeds = plan?.torrents.filter((item) => !item.selected) ?? []

  const run = async () => {
    setBusy(true); setError('')
    try {
      const done = await libraryApi.remove(titles, mode)
      const removed = plural(done.titles.length, kind)
      onDone(mode === 'keep_files'
        ? `Removed ${removed} from Medialogue. The files were kept, and scans skip ${done.folders.length === 1 ? 'that folder' : 'those folders'}.`
        : `Deleted ${plural(done.folders.length, 'folder')} and removed ${plural(done.torrents.length, 'torrent')}. `
          + (mode === 'replace' ? `${done.titles.length === 1 ? 'It is' : 'They are'} listed in Replace Media as Missing.` : `Removed ${removed} from Medialogue.`))
    } catch (reason) {
      // The torrents are already gone and the library was updated; only some
      // files stayed. Retrying cannot help, so finish and say what is left.
      if (reason instanceof ApiError && reason.code === 'DELETE_INCOMPLETE') {
        onDone(reason.message, 'error')
        return
      }
      setError(reason instanceof Error ? reason.message : `Could not remove the ${kind}s.`)
      setBusy(false)
    }
  }

  const choice = (value: LibraryRemoveMode, icon: 'close' | 'refresh' | 'folder', title: string, detail: string) =>
    <button type="button" role="radio" aria-checked={mode === value} className={`replace-choice ${value === 'forget' ? 'danger' : ''} ${mode === value ? 'active' : ''}`} onClick={() => setMode(value)}>
      <Icon name={icon} size={16} /><strong>{title}</strong><span>{detail}</span>
    </button>

  return <Modal
    title={`Remove ${count === 1 && plan ? plan.titles[0].title : noun}?`}
    eyebrow={kind === 'movie' ? 'MOVIES' : 'SHOWS'}
    icon="close"
    onClose={onClose}
    footer={<><span className="modal-foot-spacer" /><Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button variant="danger" icon="close" disabled={!plan || busy || blocked || (mustAcknowledge && !acknowledged)} onClick={() => void run()}>
        {busy ? 'Removing…' : deleting ? `Delete ${noun}` : `Remove ${noun}`}
      </Button></>}
  >
    <div className="delete-downloads">
      {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {!plan && !error && <p className="delete-downloads-note">Working out what would be removed…</p>}
      {plan && <>
        <section>
          <h4>What happens</h4>
          <div className="delete-downloads-choices" role="radiogroup">
            {choice('forget', 'close', 'Delete files and remove',
              kind === 'show' ? 'Delete the show folder with every season, and remove the show from Medialogue.' : 'Delete the movie folder and remove the movie from Medialogue.')}
            {choice('replace', 'refresh', 'Delete files and find a replacement',
              `Delete the files but keep the ${kind} in Medialogue, listed in Replace Media as Missing.`)}
            {choice('keep_files', 'folder', 'Keep files, remove from Medialogue',
              `Leave everything on disk and in qBittorrent. Scans skip the ${plan.folders.length === 1 ? 'folder' : 'folders'} until you allow ${plan.folders.length === 1 ? 'it' : 'them'} again in Settings → Media Folders.`)}
          </div>
        </section>
        {blocked && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>
          Nothing can be deleted: {plan.blocked.join(' ')}
        </span></div>}
        <section>
          <h4>{plural(plan.titles.length, kind)}</h4>
          <ul className="delete-downloads-list">{plan.titles.map((item) => <li key={`${item.kind}-${item.title}-${item.year}`}>
            <Icon name={kind === 'movie' ? 'clapper' : 'tv-screen'} size={13} /><span>{item.title}{item.year ? ` (${item.year})` : ''}</span>
          </li>)}</ul>
        </section>
        <section>
          <h4>{deleting ? 'Deleted from disk' : 'Left on disk, skipped by scans'}</h4>
          {plan.folders.length
            ? <ul className="delete-downloads-list">{plan.folders.map((folder) => <li key={folder}><Icon name="folder" size={13} /><code>{folder}</code></li>)}</ul>
            : <p className="delete-downloads-note">No files on disk.</p>}
          {deleting && plan.folders.length > 0 && <p className="delete-downloads-note">Everything inside goes, including files you added yourself such as subtitles and artwork.</p>}
        </section>
        {deleting && plan.torrents.length > 0 && <section>
          <h4>Removed from qBittorrent</h4>
          <ul className="delete-downloads-list">{plan.torrents.map((item) => <li key={item.download_id}>
            <Icon name="qbittorrent" size={13} /><span>{item.name}</span><small>{item.client_name}{item.selected ? '' : ' · cross-seed of the same files'}</small>
          </li>)}</ul>
          {crossSeeds.length > 0 && <p className="delete-downloads-note">
            {crossSeeds.length === 1 ? 'A cross-seed shares these files, so it is removed too' : `${crossSeeds.length} cross-seeds share these files, so they are removed too`} rather than left broken.
          </p>}
        </section>}
        {mustAcknowledge && <label className="inline-check">
          <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
          <span>I understand the files of these {noun} are deleted permanently.</span>
        </label>}
      </>}
    </div>
  </Modal>
}
