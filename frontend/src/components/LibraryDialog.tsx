import { useEffect, useMemo, useState } from 'react'
import { librariesApi } from '../api/modules/libraries'
import { storageApi } from '../api/modules/storage'
import { libraryIcon } from '../lib/libraries'
import type { Library, StorageRoot } from '../types'
import { Icon } from './Icon'
import { Modal } from './Modal'
import { SelectCheck } from './SelectionBar'
import { Field, SettingsGroup } from './settings'
import { Tooltip } from './Tooltip'
import { Button, Input, Segmented } from './ui'

type Tab = 'general' | 'folders' | 'advanced'

const kindOptions = [
  { value: 'movies' as const, label: 'Movies', tone: 'movie' as const },
  { value: 'shows' as const, label: 'Shows', tone: 'show' as const },
]

/** Add or edit a library: its name and type, and which media folders it holds. */
export function LibraryDialog({ library, libraries, onClose, onSaved }: {
  /** Undefined: add a new library. */
  library?: Library
  libraries: Library[]
  onClose: () => void
  onSaved: (library: Library) => void
}) {
  const [tab, setTab] = useState<Tab>('general')
  const [name, setName] = useState(library?.name ?? '')
  const [kind, setKind] = useState<Library['kind']>(library?.kind ?? 'movies')
  const [roots, setRoots] = useState<StorageRoot[] | null>(null)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    storageApi.storageRoots()
      .then((items) => {
        if (!alive) return
        setRoots(items)
        setChecked(new Set(items.filter((root) => library && root.library_id === library.id).map((root) => root.id)))
      })
      .catch((reason) => { if (alive) { setRoots([]); setError(reason instanceof Error ? reason.message : 'Could not load media folders.') } })
    return () => { alive = false }
  }, [library])

  // The type is fixed once anything is in the library: its folders and titles
  // are of that type.
  const kindLocked = Boolean(library && (library.folderCount > 0 || library.titleCount > 0))
  const lastOfKind = Boolean(library && !libraries.some((item) => item.id !== library.id && item.kind === library.kind))
  const ofKind = useMemo(() => (roots ?? []).filter((root) => root.media_type === kind), [roots, kind])
  const moving = ofKind.filter((root) => checked.has(root.id) && root.library_id && root.library_id !== library?.id)
  const movingTitles = moving.reduce((sum, root) => sum + root.title_count, 0)
  const trimmed = name.trim()
  const duplicate = libraries.some((item) => item.id !== library?.id && item.name.toLocaleLowerCase() === trimmed.toLocaleLowerCase())

  const toggle = (root: StorageRoot) => setChecked((current) => {
    const next = new Set(current)
    if (next.has(root.id)) next.delete(root.id); else next.add(root.id)
    return next
  })

  const changeKind = (next: Library['kind']) => {
    setKind(next)
    // Folders of the other type cannot join; drop them rather than send them.
    setChecked((current) => new Set([...current].filter((id) => roots?.find((root) => root.id === id)?.media_type === next)))
  }

  const save = async () => {
    if (!trimmed || duplicate) { setTab('general'); return }
    setSaving(true); setError('')
    try {
      let saved = library
        ? (trimmed !== library.name || kind !== library.kind
          ? await librariesApi.update(library.id, { ...(trimmed !== library.name ? { name: trimmed } : {}), ...(kind !== library.kind ? { kind } : {}) })
          : library)
        : await librariesApi.create(trimmed, kind)
      const wanted = ofKind.filter((root) => checked.has(root.id)).map((root) => root.id)
      const current = (roots ?? []).filter((root) => library && root.library_id === library.id).map((root) => root.id)
      const changed = wanted.length !== current.length || wanted.some((id) => !current.includes(id))
      if (changed) {
        try { saved = await librariesApi.setFolders(saved.id, wanted) }
        catch (reason) {
          // A new library already exists by now; keep it and say what failed.
          onSaved(saved)
          setError(reason instanceof Error ? reason.message : 'Could not change the media folders.')
          setTab('folders')
          return
        }
      }
      onSaved(saved)
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save the library.')
    } finally { setSaving(false) }
  }

  const tabs: { value: Tab; label: string }[] = [
    { value: 'general', label: 'General' },
    { value: 'folders', label: 'Folders' },
    { value: 'advanced', label: 'Advanced' },
  ]

  return <Modal
    title={library ? library.name : 'Add library'}
    icon={libraryIcon(kind)}
    onClose={onClose}
    className="library-dialog"
    footer={<>
      {moving.length ? <span className="footer-state">Moves {moving.length} {moving.length === 1 ? 'folder' : 'folders'}{movingTitles ? ` and ${movingTitles} ${movingTitles === 1 ? 'title' : 'titles'}` : ''} here</span>
        : <span className="modal-foot-spacer" />}
      <Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button variant="primary" onClick={() => void save()} disabled={saving || !trimmed || duplicate || roots === null}>
        {saving ? 'Saving…' : library ? 'Save library' : 'Add library'}
      </Button>
    </>}
  >
    <div className="library-dialog-tabs filter-tabs" role="tablist" aria-label="Library section">
      {tabs.map((item) => <button key={item.value} type="button" role="tab" aria-selected={tab === item.value}
        className={tab === item.value ? 'active' : ''} onClick={() => setTab(item.value)}>{item.label}</button>)}
    </div>
    <div className="provider-modal-editor">
      {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
      <div className={`settings-form scope-${kind}`}>
        {tab === 'general' && <SettingsGroup legend="Library">
          <Field label="Name" help="Shown in the sidebar." error={duplicate ? 'Another library already has this name.' : undefined}>
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder={kind === 'movies' ? 'Cartoon Movies' : 'Cartoon Shows'} autoFocus maxLength={256}
              onKeyDown={(event) => { if (event.key === 'Enter') void save() }} />
          </Field>
          <Field label="Type" help={kindLocked
            ? 'Fixed while the library has media folders or titles.'
            : lastOfKind ? `The only ${library?.kind} library, so it stays one.`
            : 'Which media folders it can hold.'}>
            <Segmented ariaLabel="Library type" value={kind} onChange={changeKind} options={kindOptions} disabled={kindLocked || lastOfKind} />
          </Field>
        </SettingsGroup>}

        {tab === 'folders' && <SettingsGroup legend={`${kind === 'movies' ? 'Movie' : 'Show'} media folders`}>
          {roots === null ? <div className="library-folder-empty">Loading media folders…</div>
            : !ofKind.length ? <div className="library-folder-empty">No {kind === 'movies' ? 'movie' : 'show'} media folders yet. Add them in Settings → Media.</div>
            : ofKind.map((root) => {
              const here = Boolean(library && root.library_id === library.id)
              const isChecked = checked.has(root.id)
              // Leaving every library would hide its titles; it moves by being
              // checked in another library instead.
              const locked = here && isChecked && root.title_count > 0
              const elsewhere = root.library_id && !here ? root.library_name : undefined
              const row = <div className={`library-folder-row ${isChecked ? 'on' : ''} ${locked ? 'locked' : ''}`} onClick={() => { if (!locked) toggle(root) }}>
                <SelectCheck inline checked={isChecked} label={root.name} onToggle={() => { if (!locked) toggle(root) }} />
                <span className="library-folder-copy">
                  <strong>{root.name}</strong>
                  <small>{root.resolved_root_path}</small>
                </span>
                <span className={`library-folder-home ${elsewhere && isChecked ? 'moving' : ''}`}>
                  {elsewhere ? (isChecked ? `Moves from ${elsewhere}` : elsewhere) : here ? '' : 'No library'}
                </span>
              </div>
              return locked
                ? <Tooltip key={root.id} asChild content={`Holds ${root.title_count} ${root.title_count === 1 ? 'title' : 'titles'}. Check it in another library to move it.`}>{row}</Tooltip>
                : <div key={root.id} className="library-folder-item">{row}</div>
            })}
        </SettingsGroup>}
        {tab === 'folders' && <p className="library-dialog-note">A media folder is in one library at most. Checking one that is in another library moves it here, with its titles.</p>}

        {tab === 'advanced' && <SettingsGroup legend="Advanced">
          <div className="library-folder-empty">Nothing here yet.</div>
        </SettingsGroup>}
      </div>
    </div>
  </Modal>
}
