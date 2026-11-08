import { useEffect, useMemo, useState } from 'react'
import { usePersistentSet } from '../lib/persistentSet'
import { newMediaRoots } from '../lib/newMediaTriage'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { Dropdown } from '../components/Dropdown'
import { Button, EmptyState, Input } from '../components/ui'
import { discoveryApi } from '../api/modules/discovery'
import { systemApi } from '../api/modules/system'
import { moviesApi } from '../api/modules/movies'
import { settingsApi } from '../api/modules/settings'
import type { Job, MediaDiscovery, QualityProfile } from '../types'
import { CUSTOM_EDITION, EditionDraft, MIXED_PROFILE, Page, THEATRICAL_EDITION, TMDBCandidate, defaultProfileLabel, editionChoices, editionUpdate, tmdbPosterUrl, useLiveLibraryRefresh } from './shared'

function movieEditionSlot(edition?: string): string {
  const normalized = (edition ?? '').trim().toLocaleLowerCase()
  return normalized === 'theatrical cut' ? '' : normalized
}

function newMediaFolderName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path
}

function capitalise(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export function NewMediaPage() {
  const [items, setItems] = useState<MediaDiscovery[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [addJob, setAddJob] = useState<Job | null>(null)
  const [rootName, setRootName] = useState('')
  // Folders left out stay left out across reloads and devices.
  const [excluded, setExcluded, pruneExcluded] = usePersistentSet('new-media-excluded')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [editionOptions, setEditionOptions] = useState<string[]>([])
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [profileBusy, setProfileBusy] = useState(false)

  const load = async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const next = await discoveryApi.discoveries()
      setItems(next)
      pruneExcluded(next.map((item) => item.id))
      setError('')
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load New Media.') }
    finally { if (!quiet) setLoading(false) }
  }
  useEffect(() => {
    void load()
    void moviesApi.movieEditionOptions().then(setEditionOptions).catch(() => undefined)
    void settingsApi.qualityProfiles().then(setProfiles).catch(() => undefined)
  }, [])
  useLiveLibraryRefresh(() => load(true))

  // Confirmed-missing directories are not actionable New Media work. The API
  // excludes them too; this guard also prevents an already-loaded stale row
  // from lingering until the next refresh.
  const actionableItems = useMemo(() => items.filter((item) => item.exists), [items])

  useEffect(() => {
    if (!selectedId) return
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('.new-media-match-cell, .new-media-folder-cell, .new-media-alt-pane, .dropdown-menu')) return
      setSelectedId('')
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [selectedId])

  const roots = useMemo(() => newMediaRoots(actionableItems), [actionableItems])
  useEffect(() => {
    if (!roots.length) { setRootName(''); return }
    if (!roots.some((root) => root.name === rootName)) setRootName(roots[0].name)
  }, [roots, rootName])

  const root = roots.find((entry) => entry.name === rootName)
  const rows = useMemo(() => actionableItems.filter((item) => item.rootName === rootName), [actionableItems, rootName])
  const selected = rows.find((item) => item.id === selectedId)
  const addable = rows.filter((item) => !excluded.has(item.id) && item.selected && item.exists)
  const duplicateEditionIds = useMemo(() => {
    const groups = new Map<string, string[]>()
    addable.filter((item) => item.mediaType === 'movies' && item.selected).forEach((item) => {
      const key = `${item.selected!.tmdbId}:${movieEditionSlot(item.edition)}`
      groups.set(key, [...(groups.get(key) ?? []), item.id])
    })
    return new Set([...groups.values()].filter((ids) => ids.length > 1).flat())
  }, [addable])

  const sharedMovieIds = useMemo(() => {
    const groups = new Map<number, string[]>()
    rows.filter((item) => item.mediaType === 'movies' && item.selected).forEach((item) => {
      const tmdbId = item.selected!.tmdbId
      groups.set(tmdbId, [...(groups.get(tmdbId) ?? []), item.id])
    })
    return new Set([...groups.values()].filter((ids) => ids.length > 1).flat())
  }, [rows])
  const conflictingIds = new Set(addable.filter((item) => item.libraryEditionConflict).map((item) => item.id))
  duplicateEditionIds.forEach((id) => conflictingIds.add(id))
  const hasEditionConflicts = conflictingIds.size > 0
  const noun = root?.mediaType === 'shows' ? 'show' : 'movie'

  const toggle = (id: string) => setExcluded((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const setRowProfile = async (item: MediaDiscovery, profileId: string | null) => {
    setProfileBusy(true); setError('')
    try {
      const updated = await discoveryApi.updateDiscoveryQualityProfile(item.id, profileId)
      setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not set the Quality Profile.') }
    finally { setProfileBusy(false) }
  }

  // What the included folders are already set to, or undefined when they differ.
  // Without this the control had nothing to display and needed a placeholder.
  const sharedProfile = useMemo(() => {
    const targets = rows.filter((item) => !excluded.has(item.id))
    if (!targets.length) return undefined
    const first = targets[0].qualityProfileId ?? ''
    return targets.every((item) => (item.qualityProfileId ?? '') === first) ? first : undefined
  }, [rows, excluded])

  // Applies to the folders currently listed and still included, not to the whole
  // queue: the visible selection is what the user believes they are changing.
  const setAllProfiles = async (profileId: string | null) => {
    const targets = rows.filter((item) => !excluded.has(item.id))
    if (!targets.length) return
    setProfileBusy(true); setError(''); setMessage('')
    try {
      const result = await discoveryApi.bulkDiscoveryQualityProfile(targets.map((item) => item.id), profileId)
      await load(true)
      const name = profiles.find((profile) => profile.id === profileId)?.name
      setMessage(`${result.succeeded.length} folder${result.succeeded.length === 1 ? '' : 's'} set to ${name ?? 'no Quality Profile'}.`)
      if (result.failed.length) setError(`${result.failed.length} could not be changed: ${result.failed[0].message}`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not set the Quality Profile.') }
    finally { setProfileBusy(false) }
  }

  // Adding runs on the server, so it carries on when this page is left or
  // closed, and a page opened mid-run shows it instead of offering to add the
  // same folders again.
  const runAdd = async () => {
    const ids = addable.map((item) => item.id)
    if (!ids.length || hasEditionConflicts || addJob) return
    setAdding(true); setError(''); setMessage('')
    try {
      const jobId = await discoveryApi.startAddJob(ids)
      setAddJob(await systemApi.job(jobId))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start adding.') }
    finally { setAdding(false) }
  }

  useEffect(() => {
    let alive = true
    discoveryApi.currentAddJob().then((job) => { if (alive && job) setAddJob(job) }).catch(() => undefined)
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!addJob) return
    let alive = true
    let lastDone = Number(addJob.summary?.done ?? 0)
    const timer = window.setInterval(() => void (async () => {
      try {
        const job = await systemApi.job(addJob.id)
        if (!alive) return
        const done = Number(job.summary?.done ?? 0)
        // Added titles leave the list as each batch finishes.
        if (done !== lastDone) { lastDone = done; void load(true) }
        if (['queued', 'running'].includes(job.state)) { setAddJob(job); return }
        setAddJob(null)
        window.dispatchEvent(new Event('medialogue-new-media-changed'))
        await load(true)
        const added = Number(job.summary?.added ?? 0)
        const failures = Array.isArray(job.summary?.failures) ? job.summary.failures as string[] : []
        const failed = Number(job.summary?.failed ?? 0)
        if (job.state === 'completed') setMessage(`${added} ${added === 1 ? noun : `${noun}s`} added to the library.`)
        if (failed) setError(`${failed} could not be added: ${[...new Set(failures)].join(' ')}`)
        else if (job.state !== 'completed') setError(job.error || 'Adding stopped before it finished; the folders not added are still here.')
      } catch { /* the next tick retries */ }
    })(), 2000)
    return () => { alive = false; window.clearInterval(timer) }
  // Follows one job: a new id restarts the poll; progress updates do not.
  }, [addJob?.id])

  const choose = async (item: MediaDiscovery, candidate: TMDBCandidate) => {
    setError('')
    const previous = item.selected
    setItems((current) => current.map((entry) => entry.id === item.id ? { ...entry, selected: candidate } : entry))
    try {
      const updated = await discoveryApi.selectDiscovery(item.id, candidate)
      setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))
    } catch (reason) {
      // Put the old match back rather than leaving the row claiming a choice
      // that was never recorded.
      setItems((current) => current.map((entry) => entry.id === item.id ? { ...entry, selected: previous } : entry))
      setError(reason instanceof Error ? reason.message : 'Could not change the match.')
    }
  }

  if (loading) return <Page title="New Media" subtitle="Confirm media found during scans before adding it to your library.">
    <div className="new-media-loading">Loading detected media…</div>
  </Page>

  if (!actionableItems.length) return <Page title="New Media" subtitle="Confirm media found during scans before adding it to your library.">
    <EmptyState icon="new-media" title="No new media" detail="Media awaiting approval will appear here after a scan." />
  </Page>

  return <Page title="New Media" subtitle="Confirm media found during scans before adding it to your library.">
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {message && !error && <div className="settings-note note-ok"><Icon name="check" size={16} /><span>{message}</span></div>}

    <div className="new-media-roots-label">Media folders</div>
    <div className="new-media-roots">
      {roots.map((entry) => <button
        type="button"
        key={entry.name}
        className={`new-media-root ${entry.name === rootName ? 'active' : ''}`}
        onClick={() => { setRootName(entry.name) }}
      >
        <span className="new-media-root-name">
          <span className={`kind-box kind-${entry.mediaType === 'movies' ? 'movie' : 'show'}`} aria-label={entry.mediaType === 'movies' ? 'Movies folder' : 'Shows folder'}>
            <Icon name={entry.mediaType === 'movies' ? 'clapper' : 'tv-screen'} size={13} />
          </span>
          <strong>{entry.name}</strong>
        </span>
        <small>{entry.count} {entry.count === 1 ? 'folder' : 'folders'}</small>
      </button>)}
    </div>

    <div className="new-media-workspace">

      <div className="new-media-scroll new-media-combined">
        <span className="new-media-col-head">{noun === 'show' ? 'Show' : 'Movie'} folder and match</span>
        {rows.map((item) => {
            const match = item.selected
            const active = false
            const blocked = !item.exists || !match
            const included = !excluded.has(item.id)
            const editionConflict = included && conflictingIds.has(item.id)
            const state = !match ? { tone: 'needs-match', text: 'No results' } : null
            const folder = newMediaFolderName(item.path)
            return <div className="new-media-row" key={item.id}>
              <div
                className={`new-media-folder-cell new-media-combined-cell ${active ? 'active' : ''} ${included ? '' : 'excluded'}`}
                onClick={() => setSelectedId(active ? '' : item.id)}
              >
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={included && !blocked}
                  aria-label={`Add ${folder} to the library`}
                  className={`new-media-check ${included && !blocked ? 'on' : ''}`}
                  // Including or leaving out a folder is not the same intent as
                  // opening it, so the checkbox must not also select the row.
                  onClick={(event) => { event.stopPropagation(); toggle(item.id) }}
                  disabled={blocked}
                >{included && !blocked && <Icon name="check" size={11} />}</button>
                <span className="new-media-poster">{match?.posterRef
                  ? <img src={tmdbPosterUrl(match.posterRef)} alt="" loading="lazy" referrerPolicy="no-referrer" />
                  : <Icon name={item.mediaType === 'shows' ? 'tv-screen' : 'clapper'} size={16} />}</span>
                {/* The folder as found, then what it was matched to, then the
                    year: three short lines in the height the two columns had. */}
                <span className="new-media-combined-copy">
                  <Tooltip content={<span className="download-path">{item.path}</span>}><code>{folder}</code></Tooltip>
                  {/* Without a match there is no title or year to show, only that
                      the search found nothing; the folder name above is what it searched. */}
                  {state
                    ? <span className={`new-media-state new-media-state-inline ${state.tone}`}>{state.text}</span>
                    : <>
                      <strong>{match?.title}</strong>
                      <small>{match?.year ?? '-'}</small>
                    </>}
                </span>
                {/* Edition over Quality Profile, one width, stacked in the row's height. */}
                <span className="new-media-combined-end">
                  {item.mediaType === 'movies' && <span
                    className={`new-media-edition-cell ${editionConflict ? 'clash' : item.parsedEdition || item.manualEditionOverride || sharedMovieIds.has(item.id) ? '' : 'optional'}`}
                    title={editionConflict ? (item.libraryEditionConflict ? 'Already in your library with this edition' : 'Another copy in this queue has the same movie and edition') : undefined}
                    onClick={(event) => { event.preventDefault(); event.stopPropagation() }}
                  >
                    <NewMediaDiscoveryEdition
                      key={`${item.id}:${item.editionOverrideActive}:${item.manualEditionOverride ?? ''}`}
                      item={item}
                      options={editionOptions}
                      onChanged={(updated) => setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))}
                      onError={setError}
                    />
                  </span>}
                  <span
                    className={`new-media-profile-cell ${item.qualityProfileId ? '' : 'optional'}`}
                    onClick={(event) => { event.preventDefault(); event.stopPropagation() }}
                  >
                    <Dropdown
                      ariaLabel={`Quality Profile for ${folder}`}
                      value={item.qualityProfileId ?? ''}
                      disabled={profileBusy}
                      options={[{ value: '', label: defaultProfileLabel(profiles) }, ...profiles.map((profile) => ({ value: profile.id, label: profile.name }))]}
                      onChange={(value) => void setRowProfile(item, value || null)}
                    />
                  </span>
                </span>
              </div>
            </div>
        })}
      </div>

      <div className="new-media-add">
        {/* Sits with the add action because it describes what is about to be
            added, and it is one write for the whole visible selection. */}
        <span className="new-media-add-profile">
          <span className="field-label">Quality Profile</span>
          {/* A value, not a command: it shows what the included folders are set
              to and changing it sets them. The old placeholder option was an
              action sitting in a list of values, and read as one more choice. */}
          <Dropdown
            ariaLabel="Quality Profile for every included folder"
            value={sharedProfile ?? MIXED_PROFILE}
            disabled={profileBusy || !rows.some((item) => !excluded.has(item.id))}
            options={[
              ...(sharedProfile === undefined ? [{ value: MIXED_PROFILE, label: 'Mixed', disabled: true }] : []),
              { value: '', label: defaultProfileLabel(profiles) },
              ...profiles.map((profile) => ({ value: profile.id, label: profile.name })),
            ]}
            onChange={(value) => void setAllProfiles(value || null)}
          />
        </span>
        <div className="toolbar-spacer" />
        {hasEditionConflicts && <span className="new-media-add-warning">Resolve same-edition copies or leave one out before adding.</span>}
        <Button variant="primary" icon="check" onClick={() => void runAdd()} disabled={adding || Boolean(addJob) || !addable.length || hasEditionConflicts}>
          {addJob
            ? `Adding ${Number(addJob.summary?.done ?? 0).toLocaleString()} of ${Number(addJob.summary?.total ?? 0).toLocaleString()}…`
            : adding ? 'Starting…' : `Add ${addable.length} ${addable.length === 1 ? capitalise(noun) : `${capitalise(noun)}s`}`}
        </Button>
      </div>

      {selected
        ? <NewMediaAlternatives key={selected.id} item={selected} onChoose={(candidate) => void choose(selected, candidate)} onSearched={(updated) => setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))} onError={setError} />
        : <aside className="new-media-alt-pane new-media-alt-idle">
          <div className="new-media-col-head new-media-alt-title">Possible matches</div>
          <div className="new-media-alt-empty">
            <Icon name="search" size={28} />
            <p>Select a detected {noun} to see the alternatives.</p>
          </div>
        </aside>}
    </div>
  </Page>
}

function NewMediaAlternatives({ item, onChoose, onSearched, onError }: {
  item: MediaDiscovery
  onChoose: (candidate: TMDBCandidate) => void
  onSearched: (item: MediaDiscovery) => void
  onError: (message: string) => void
}) {
  const [query, setQuery] = useState(item.parsedTitle ?? item.selected?.title ?? '')
  const [searching, setSearching] = useState(false)
  const matches = item.candidates

  const search = async (term?: string) => {
    setSearching(true)
    try { onSearched(await discoveryApi.searchDiscoveryCandidates(item.id, term)) }
    catch (reason) { onError(reason instanceof Error ? reason.message : 'TMDB lookup failed.') }
    finally { setSearching(false) }
  }

  return <aside className="new-media-alt-pane">
    <div className="new-media-col-head new-media-alt-title">Possible matches</div>
    <div className="new-media-alt-head">
      <div className="search-field">
        <Icon name="search" size={14} />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') void search(query) }}
          disabled={searching}
          placeholder={`Search TMDB for a ${item.mediaType === 'shows' ? 'show' : 'movie'}…`}
        />
      </div>
    </div>
    <div className="new-media-alt-list">
      {searching && <p className="new-media-alt-note">Searching…</p>}
      {!searching && !matches.length && <p className="new-media-alt-note">No candidates. Search above.</p>}
      {matches.map((candidate) => {
        const chosen = item.selected?.tmdbId === candidate.tmdbId
        const yearMatches = item.parsedYear !== undefined && candidate.year === item.parsedYear
        return <button
          type="button"
          key={candidate.tmdbId}
          className={`new-media-candidate ${chosen ? 'chosen' : ''}`}
          disabled={chosen}
          onClick={() => onChoose(candidate)}
        >
          <span className="new-media-poster">{candidate.posterRef
            ? <img src={tmdbPosterUrl(candidate.posterRef)} alt="" loading="lazy" referrerPolicy="no-referrer" />
            : <Icon name={item.mediaType === 'shows' ? 'tv-screen' : 'clapper'} size={16} />}</span>
          <span className="new-media-candidate-copy">
            <strong>{candidate.title}</strong>
            <small>{candidate.year ?? 'Year unknown'}{yearMatches ? ' · matches folder' : ''}</small>
          </span>
          {chosen && <Icon name="radio-selected" size={20} />}
        </button>
      })}
    </div>
  </aside>
}

function initialDiscoveryEdition(item: MediaDiscovery, options: string[]): EditionDraft {
  const edition = item.manualEditionOverride ?? item.parsedEdition ?? THEATRICAL_EDITION
  if (edition === THEATRICAL_EDITION || options.includes(edition)) return { selection: edition, custom: '' }
  return { selection: CUSTOM_EDITION, custom: edition }
}

function NewMediaDiscoveryEdition({ item, options, onChanged, onError }: { item: MediaDiscovery; options: string[]; onChanged: (item: MediaDiscovery) => void; onError: (message: string) => void }) {
  const [draft, setDraft] = useState<EditionDraft>(() => initialDiscoveryEdition(item, options))
  const [saving, setSaving] = useState(false)
  const persist = async (next: EditionDraft) => {
    const update = editionUpdate(item.id, next)
    if (!update.edition?.trim()) return
    setSaving(true); onError('')
    try { onChanged(await discoveryApi.updateDiscoveryEdition(item.id, { mode: update.mode, edition: update.edition })) }
    catch (reason) { onError(reason instanceof Error ? reason.message : 'Could not save the edition.') }
    finally { setSaving(false) }
  }
  return <div className="new-media-edition-picker">
    <Dropdown
      ariaLabel="Edition"
      value={draft.selection}
      disabled={saving}
      options={[...editionChoices(options).map((edition) => ({ value: edition, label: edition })), { value: CUSTOM_EDITION, label: 'Other…' }]}
      onChange={(value) => {
        const next = { ...draft, selection: value }
        setDraft(next)
        if (next.selection !== CUSTOM_EDITION) void persist(next)
      }}
    />
    {draft.selection === CUSTOM_EDITION && <Input value={draft.custom} maxLength={128} disabled={saving} onChange={(event) => setDraft((current) => ({ ...current, custom: event.target.value }))} onBlur={() => {
      const original = initialDiscoveryEdition(item, options)
      if (draft.custom.trim() && JSON.stringify(editionUpdate(item.id, draft)) !== JSON.stringify(editionUpdate(item.id, original))) void persist(draft)
    }} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} placeholder="Custom edition" />}
  </div>
}
