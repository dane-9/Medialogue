import { useEffect, useState } from 'react'
import { formatAge } from '../lib/age'
import { Link } from 'react-router-dom'
import { Note } from '../components/settings'
import { useConfirm } from '../components/Confirm'
import { Button, EmptyState, Panel } from '../components/ui'
import { blocklistApi } from '../api/modules/blocklist'
import { toggleIdSelection } from '../lib/uiState'
import type { BlocklistEntry } from '../types'
import { Page } from './shared'
import { openLiveEvents } from '../lib/liveEvents'

export function BlocklistPage() {
  const confirm = useConfirm()
  const [items, setItems] = useState<BlocklistEntry[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    try { setItems(await blocklistApi.entries()); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load the blocklist.') }
    finally { setLoading(false) }
  }
  useEffect(() => {
    void load()
    const stream = openLiveEvents()
    stream.addEventListener('blocklist.changed', load)
    stream.addEventListener('download.failed', load)
    return () => stream.close()
  }, [])

  const remove = async (ids: string[]) => {
    if (!ids.length) return
    const approved = await confirm({
      title: ids.length === 1 ? 'Remove blocklist entry?' : `Remove ${ids.length} blocklist entries?`,
      body: <p>The release will become eligible in future searches again. Failure history will remain in Events.</p>,
      confirmLabel: 'Unblock',
      icon: 'unlock',
    })
    if (!approved) return
    try {
      if (ids.length === 1) await blocklistApi.remove(ids[0])
      else await blocklistApi.removeMany(ids)
      setSelected(new Set())
      await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not update the blocklist.') }
  }

  const clear = async () => {
    const approved = await confirm({
      title: 'Clear the entire blocklist?',
      body: <p>Every failed release will become eligible again. Recorded failure events are not deleted.</p>,
      confirmLabel: 'Clear blocklist',
      icon: 'unlock',
    })
    if (!approved) return
    try { await blocklistApi.clear(); setSelected(new Set()); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not clear the blocklist.') }
  }

  const allSelected = items.length > 0 && items.every((item) => selected.has(item.id))
  return <Page
    title="Blocklist"
    subtitle="Failed releases excluded from future searches. Removing an entry does not erase its failure history."
    action={<div className="page-actions"><Button variant="ghost" icon="unlock" disabled={!selected.size} onClick={() => void remove([...selected])}>Unblock selected</Button><Button variant="danger" disabled={!items.length} onClick={() => void clear()}>Clear all</Button></div>}
  >
    {error && <Note message={{ tone: 'error', text: error }} />}
    {loading ? <Panel><div className="history-empty">Loading blocklist…</div></Panel>
      : !items.length ? <EmptyState title="Nothing is blocklisted" detail="Failed releases you exclude will appear here. Failure records remain available under Events." />
        : <Panel className="blocklist-panel"><table className="data-table blocklist-table"><thead><tr>
          <th className="blocklist-select"><input type="checkbox" aria-label="Select all blocklist entries" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((item) => item.id)))} /></th>
          <th>Media</th><th>Failed release</th><th>Quality</th><th>Indexer</th><th>Failed</th><th>Reason</th><th />
        </tr></thead><tbody>{items.map((item) => <tr key={item.id}>
          <td className="blocklist-select"><input type="checkbox" aria-label={`Select ${item.sourceTitle}`} checked={selected.has(item.id)} onChange={() => setSelected((current) => toggleIdSelection(current, item.id))} /></td>
          <td><strong>{item.mediaTitle ?? (item.mediaType === 'movies' ? 'Movie' : 'Show')}</strong><span className="table-sub">{item.targetEntityType}</span></td>
          <td><strong>{item.sourceTitle}</strong>{item.infoHash && <code className="table-sub">{item.infoHash.slice(0, 12)}…</code>}</td>
          <td>{item.quality ?? 'Unknown'}</td><td>{item.indexerName ?? 'Unknown'}</td><td>{formatAge(item.createdAt)}</td>
          <td className="blocklist-reason">{item.reason}</td>
          <td><div className="blocklist-actions">{item.mediaResourceId && <Link className="button button-secondary" to={item.mediaType === 'movies' ? `/movies/${item.mediaResourceId}?search=1` : `/shows/${item.mediaResourceId}`}>{item.mediaType === 'movies' ? 'Search alternatives' : 'Open show'}</Link>}<Button variant="ghost" icon="unlock" onClick={() => void remove([item.id])}>Unblock</Button></div></td>
        </tr>)}</tbody></table></Panel>}
  </Page>
}
