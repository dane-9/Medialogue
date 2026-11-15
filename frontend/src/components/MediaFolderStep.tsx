import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { storageApi } from '../api/modules/storage'
import { libraryIcon, useLibraries } from '../lib/libraries'
import type { Library, StorageRoot } from '../types'
import { Icon } from './Icon'

export interface MediaFolderGroup {
  library: Pick<Library, 'id' | 'name' | 'kind'>
  folders: StorageRoot[]
}

/** The media folders a new title of ``kind`` can be downloaded into, grouped by library.
 *
 * Only folders in a library count: one in no library is invisible. The first
 * folder of the library on screen is picked to begin with, else the first one.
 */
export function useMediaFolderChoice(kind: Library['kind']) {
  const { libraries } = useLibraries()
  const location = useLocation()
  const [roots, setRoots] = useState<StorageRoot[] | null>(null)
  const [folderId, setFolderId] = useState('')

  useEffect(() => {
    let alive = true
    storageApi.storageRoots()
      .then((rows) => { if (alive) setRoots(rows) })
      // Without the list the download still works: the title joins the default library.
      .catch(() => { if (alive) setRoots([]) })
    return () => { alive = false }
  }, [])

  const groups = useMemo<MediaFolderGroup[]>(() => {
    const usable = (roots ?? []).filter((root) => root.enabled && root.media_type === kind && root.library_id)
    const order = (libraries ?? []).filter((library) => library.kind === kind)
    const known = order.map((library) => ({ library, folders: usable.filter((root) => root.library_id === library.id) }))
    // A library the sidebar has not loaded yet still shows its folders.
    const unknown = usable.filter((root) => !order.some((library) => library.id === root.library_id))
    const extra = [...new Set(unknown.map((root) => root.library_id!))].map((id) => ({
      library: { id, name: unknown.find((root) => root.library_id === id)?.library_name ?? 'Library', kind },
      folders: unknown.filter((root) => root.library_id === id),
    }))
    return [...known, ...extra].filter((group) => group.folders.length)
  }, [roots, libraries, kind])
  const folders = useMemo(() => groups.flatMap((group) => group.folders), [groups])

  const onLibrary = /^\/library\/([^/?#]+)/.exec(location.pathname)?.[1]
  useEffect(() => {
    if (folderId && folders.some((root) => root.id === folderId)) return
    const preferred = folders.find((root) => root.library_id === onLibrary) ?? folders[0]
    setFolderId(preferred?.id ?? '')
  }, [folders, folderId, onLibrary])

  return {
    loading: roots === null,
    groups,
    folders,
    folder: folders.find((root) => root.id === folderId),
    folderId,
    setFolderId,
    /** Step 1 only appears when there is a choice to make. */
    choosable: folders.length > 1,
  }
}

/** Wizard step 1: which media folder, and so which library, the title goes into. */
export function MediaFolderStep({ groups, loading, value, onChange, locked }: {
  groups: MediaFolderGroup[]
  loading: boolean
  value: string
  onChange: (id: string) => void
  locked?: boolean
}) {
  if (loading) return <div className="wizard-box wizard-folder-empty">Loading media folders…</div>
  return <div className="wizard-folders" role="radiogroup" aria-label="Media folder">
    {groups.map((group) => <section className="wizard-folder-group" key={group.library.id}>
      <h3><Icon name={libraryIcon(group.library.kind)} size={14} />{group.library.name}</h3>
      <div className="wizard-box wizard-folder-list">
        {group.folders.map((root) => <button
          key={root.id}
          type="button"
          role="radio"
          aria-checked={value === root.id}
          disabled={locked}
          className={`wizard-folder ${value === root.id ? 'active' : ''}`}
          onClick={() => onChange(root.id)}
        >
          <span className="wizard-folder-dot" aria-hidden="true" />
          <span className="wizard-folder-copy">
            <strong>{root.name}</strong>
            <small>{root.resolved_root_path}</small>
          </span>
          <span className="wizard-folder-category">{root.category ? `Category: ${root.category}` : 'Client default category'}</span>
        </button>)}
      </div>
    </section>)}
  </div>
}
