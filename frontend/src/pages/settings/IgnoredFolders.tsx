import { useEffect, useState } from 'react'
import { libraryApi } from '../../api/modules/library'
import { formatAge } from '../../lib/age'
import { Icon } from '../../components/Icon'
import { SectionHead } from '../../components/settings'
import { Button } from '../../components/ui'

type IgnoredFolder = Awaited<ReturnType<typeof libraryApi.ignoredFolders>>[number]

/** Folders scans skip because their title was removed with the files kept.
 *  Shown only when there are any. */
export function IgnoredFolders() {
  const [items, setItems] = useState<IgnoredFolder[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    libraryApi.ignoredFolders().then((rows) => { if (live) setItems(rows) }).catch(() => undefined)
    return () => { live = false }
  }, [])

  const allow = async (item: IgnoredFolder) => {
    setBusy(item.id); setError('')
    try {
      await libraryApi.stopIgnoring(item.id)
      setItems((rows) => rows.filter((row) => row.id !== item.id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not stop ignoring the folder.')
    } finally { setBusy(null) }
  }

  if (!items.length) return null
  return <>
    <SectionHead title="Ignored Folders" divided
      description="Titles removed from Medialogue with their files kept. Scans skip these folders; allow one again and the next scan of its media folder adds it back." />
    <div className="root-list">
      {items.map((item) => <div className="root-row root-row-static" key={item.id}>
        <span className="root-row-icon"><Icon name="eye-off" size={18} /></span>
        <span className="root-row-copy">
          <span className="root-row-title"><strong>{item.title}{item.year ? ` (${item.year})` : ''}</strong></span>
          <code>{item.resolved_path}</code>
        </span>
        <span className="root-row-end">
          <span className="root-row-scope">{item.media_type === 'shows' ? 'Show' : 'Movie'} · removed {formatAge(item.created_at)}</span>
          <Button variant="ghost" disabled={busy === item.id} onClick={() => void allow(item)}>{busy === item.id ? 'Allowing…' : 'Allow again'}</Button>
        </span>
      </div>)}
    </div>
    {error && <p className="provider-empty-copy">{error}</p>}
  </>
}
