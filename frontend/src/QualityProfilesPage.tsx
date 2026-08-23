import { useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import { Icon } from './components/Icon'
import { Modal } from './components/Modal'
import { PageTopbar } from './components/Shell'
import { Field } from './components/settings'
import { Badge, Button, EmptyState, Input, Select } from './components/ui'
import { useUrlState } from './lib/urlState'
import type { CustomFormat, CustomFormatSection, QualityDefinition, QualityProfile } from './types'

const scoreLabel = (value: number) => `${value > 0 ? '+' : ''}${value}`

export default function QualityProfilesPage() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [qualities, setQualities] = useState<QualityDefinition[]>([])
  const [formats, setFormats] = useState<CustomFormat[]>([])
  const [sections, setSections] = useState<CustomFormatSection[]>([])
  const [selectedId, setSelectedId] = useUrlState('profile')
  const [editorOpen, setEditorOpen] = useState(false)
  const [name, setName] = useState('')
  const [minimumId, setMinimumId] = useState('')
  const [qualityIds, setQualityIds] = useState<string[]>([])
  const [qualityOrderIds, setQualityOrderIds] = useState<string[]>([])
  const [scores, setScores] = useState<Record<string, number>>({})
  const [savedScores, setSavedScores] = useState<Record<string, number>>({})
  const [editorView, setEditorView] = useState<'details' | 'qualities' | 'formats'>('details')
  const [formatFilter, setFormatFilter] = useState('')
  const [scoreView, setScoreView] = useState<'all' | 'selected'>('all')
  const [draggedQualityId, setDraggedQualityId] = useState('')
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const selected = profiles.find((item) => item.id === selectedId)
  const formatById = useMemo(() => new Map(formats.map((format) => [format.id, format])), [formats])

  const load = async () => {
    try {
      const [profileRows, qualityRows, formatRows, layout] = await Promise.all([api.qualityProfiles(), api.qualityDefinitions(), api.customFormats(), api.customFormatLayout()])
      setProfiles(profileRows); setQualities(qualityRows); setFormats(formatRows); setSections(layout)
      setMessage('')
      // A link naming a profile opens straight onto it; otherwise the grid is
      // the landing view and nothing is selected.
      if (selectedId && profileRows.some((item) => item.id === selectedId)) setEditorOpen(true)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not load Quality Profiles.') }
  }
  useEffect(() => { void load() }, [])

  useEffect(() => {
    if (creating) return
    const current = profiles.find((item) => item.id === selectedId)
    if (!current) return
    setName(current.name)
    setMinimumId(current.minimumQuality?.id ?? '')
    const selectedQualityIds = current.qualities.map((quality) => quality.id)
    setQualityIds(selectedQualityIds)
    setQualityOrderIds([...selectedQualityIds, ...qualities.map((quality) => quality.id).filter((id) => !selectedQualityIds.includes(id))])
    const persisted = Object.fromEntries(current.customFormatScores.map((item) => [item.customFormatId, item.score]))
    setScores(persisted)
    setSavedScores(persisted)
  }, [selectedId, profiles, qualities, creating])

  const dirty = useMemo(() => {
    if (creating) return Boolean(name.trim()) || Boolean(minimumId) || Object.keys(scores).length > 0
    if (!selected) return false
    const original = Object.fromEntries(selected.customFormatScores.map((item) => [item.customFormatId, item.score]))
    const originalQualities = selected.qualities.map((quality) => quality.id)
    return name !== selected.name
      || minimumId !== (selected.minimumQuality?.id ?? '')
      || JSON.stringify(qualityIds) !== JSON.stringify(originalQualities)
      || JSON.stringify(original) !== JSON.stringify(scores)
  }, [creating, name, minimumId, qualityIds, scores, selected])

  const openProfile = (profile: QualityProfile) => {
    setCreating(false)
    setSelectedId(profile.id)
    setName(profile.name)
    setMinimumId(profile.minimumQuality?.id ?? '')
    const selectedQualityIds = profile.qualities.map((quality) => quality.id)
    setQualityIds(selectedQualityIds)
    setQualityOrderIds([...selectedQualityIds, ...qualities.map((quality) => quality.id).filter((id) => !selectedQualityIds.includes(id))])
    const persisted = Object.fromEntries(profile.customFormatScores.map((item) => [item.customFormatId, item.score]))
    setScores(persisted)
    setSavedScores(persisted)
    setEditorView('formats'); setFormatFilter(''); setScoreView('all'); setMessage(''); setEditorOpen(true)
  }
  const beginNew = () => {
    const allQualityIds = qualities.map((quality) => quality.id)
    setCreating(true); setSelectedId(''); setName(''); setMinimumId(''); setQualityIds(allQualityIds); setQualityOrderIds(allQualityIds); setScores({}); setSavedScores({})
    setEditorView('details'); setFormatFilter(''); setScoreView('all'); setMessage(''); setEditorOpen(true)
  }
  // Copying opens an unsaved profile rather than writing one immediately, so a
  // near-duplicate can be renamed and adjusted before it joins the list.
  const duplicateProfile = (source: QualityProfile) => {
    setCreating(true)
    setSelectedId('')
    setName(`${source.name} Copy`)
    setMinimumId(source.minimumQuality?.id ?? '')
    const sourceQualityIds = source.qualities.map((quality) => quality.id)
    setQualityIds(sourceQualityIds)
    setQualityOrderIds([...sourceQualityIds, ...qualities.map((quality) => quality.id).filter((id) => !sourceQualityIds.includes(id))])
    setScores(Object.fromEntries(source.customFormatScores.map((item) => [item.customFormatId, item.score])))
    setSavedScores({})
    setEditorView('details'); setFormatFilter(''); setScoreView('all')
    setMessage(`Copy of ${source.name} created. It is not saved yet.`)
    setEditorOpen(true)
  }

  const closeEditor = () => { setEditorOpen(false); setCreating(false); setSelectedId('') }

  const save = async () => {
    if (!name.trim()) { setMessage('Profile name is required.'); return }
    if (!qualityIds.length) { setMessage('Select at least one quality.'); return }
    setBusy(true); setMessage('Saving profile…')
    const custom_format_scores = Object.entries(scores).map(([custom_format_id, score]) => ({ custom_format_id, score }))
    try {
      if (creating) {
        const created = await api.createQualityProfile({ name: name.trim(), minimum_quality_definition_id: minimumId || null, quality_definition_ids: qualityIds, custom_format_scores })
        setProfiles((items) => [...items, created].sort((a, b) => a.name.localeCompare(b.name)))
        setCreating(false); setSelectedId(created.id); setEditorOpen(false); setMessage('Quality Profile created.')
      } else if (selected) {
        const updated = await api.updateQualityProfile(selected.id, { name: name.trim(), minimum_quality_definition_id: minimumId || null, quality_definition_ids: qualityIds, custom_format_scores, expected_revision: selected.revision })
        setProfiles((items) => items.map((item) => item.id === updated.id ? updated : item).sort((a, b) => a.name.localeCompare(b.name)))
        setEditorOpen(false); setMessage('Quality Profile saved. Assigned release scores were re-evaluated.')
      }
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not save Quality Profile.') }
    finally { setBusy(false) }
  }

  const remove = async () => {
    if (!selected) return
    if (!window.confirm(`Delete Quality Profile “${selected.name}”? Media files and torrents are untouched.`)) return
    setBusy(true)
    try {
      await api.deleteQualityProfile(selected.id)
      setProfiles((items) => items.filter((item) => item.id !== selected.id))
      setSelectedId(''); setEditorOpen(false)
      setMessage('Quality Profile deleted. Media and torrent data were untouched.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not delete Quality Profile.') }
    finally { setBusy(false) }
  }

  const selectedFormatCount = Object.keys(scores).length
  const formatSections = useMemo(() => {
    const term = formatFilter.trim().toLowerCase()
    const seen = new Set<string>()
    const result = sections.map((section) => ({
      ...section,
      formats: section.formatIds
        .map((id) => formatById.get(id))
        .filter((format): format is CustomFormat => Boolean(format))
        .filter((format) => {
          seen.add(format.id)
          if (scoreView === 'selected' && !Object.prototype.hasOwnProperty.call(scores, format.id)) return false
          return !term || format.name.toLowerCase().includes(term) || (format.description ?? '').toLowerCase().includes(term)
        })
        .sort((left, right) => {
          const leftWasSaved = Object.prototype.hasOwnProperty.call(savedScores, left.id)
          const rightWasSaved = Object.prototype.hasOwnProperty.call(savedScores, right.id)
          if (leftWasSaved !== rightWasSaved) return leftWasSaved ? -1 : 1
          if (!leftWasSaved) return 0
          return savedScores[right.id] - savedScores[left.id]
        }),
    }))
    const missing = formats.filter((format) => !seen.has(format.id)).filter((format) => {
      if (scoreView === 'selected' && !Object.prototype.hasOwnProperty.call(scores, format.id)) return false
      return !term || format.name.toLowerCase().includes(term) || (format.description ?? '').toLowerCase().includes(term)
    })
    if (missing.length) result.push({ id: 'unsectioned', name: 'Other', formatIds: missing.map((format) => format.id), formats: missing })
    return result.filter((section) => section.formats.length)
  }, [sections, formats, formatById, formatFilter, scoreView, scores, savedScores])

  const toggleFormat = (formatId: string) => setScores((current) => {
    const next = { ...current }
    if (Object.prototype.hasOwnProperty.call(next, formatId)) delete next[formatId]
    else next[formatId] = 0
    return next
  })

  const setFormatSelection = (formatIds: string[], selected: boolean) => setScores((current) => {
    const next = { ...current }
    for (const formatId of formatIds) {
      if (selected) {
        if (!Object.prototype.hasOwnProperty.call(next, formatId)) next[formatId] = 0
      } else delete next[formatId]
    }
    return next
  })

  const toggleQuality = (qualityId: string) => setQualityIds((current) => {
    if (current.includes(qualityId)) {
      if (minimumId === qualityId) setMinimumId('')
      return current.filter((id) => id !== qualityId)
    }
    const selected = new Set([...current, qualityId])
    return qualityOrderIds.filter((id) => selected.has(id))
  })

  const moveQuality = (qualityId: string, targetId: string) => {
    if (qualityId === targetId) return
    const reordered = qualityOrderIds.filter((id) => id !== qualityId)
    const targetIndex = reordered.indexOf(targetId)
    if (targetIndex < 0) return
    reordered.splice(targetIndex, 0, qualityId)
    setQualityOrderIds(reordered)
    const selected = new Set(qualityIds)
    setQualityIds(reordered.filter((id) => selected.has(id)))
  }

  const moveQualityBy = (qualityId: string, amount: number) => {
    const index = qualityIds.indexOf(qualityId)
    const target = index + amount
    if (index < 0 || target < 0 || target >= qualityIds.length) return
    const next = [...qualityIds]
    ;[next[index], next[target]] = [next[target], next[index]]
    setQualityIds(next)
    setQualityOrderIds((order) => {
      const left = order.indexOf(qualityIds[index])
      const right = order.indexOf(qualityIds[target])
      if (left < 0 || right < 0) return order
      const reordered = [...order]
      ;[reordered[left], reordered[right]] = [reordered[right], reordered[left]]
      return reordered
    })
  }

  const orderedQualities = useMemo(() => {
    const byId = new Map(qualities.map((quality) => [quality.id, quality]))
    const ordered = qualityOrderIds.map((id) => byId.get(id)).filter((quality): quality is QualityDefinition => Boolean(quality))
    const missing = qualities.filter((quality) => !qualityOrderIds.includes(quality.id))
    return [...ordered, ...missing]
  }, [qualities, qualityOrderIds])

  return <main className="page">
    <PageTopbar title="Quality Profiles" subtitle="Set a warning floor and base scores for matching Custom Formats. Rule offsets are added automatically." action={<Button variant="primary" icon="plus" onClick={beginNew}>New profile</Button>} />

    {message && <div className="settings-note"><Icon name="activity" size={15} /><span>{message}</span></div>}

    {!profiles.length
      ? <EmptyState icon="spark" title="No Quality Profiles yet" detail="Create one when you want minimum-quality warnings or Custom Format scoring on your searches." action={<Button variant="primary" icon="plus" onClick={beginNew}>Create the first one</Button>} />
      : <div className="cf-card-grid">
          {profiles.map((profile) => <ProfileCard key={profile.id} profile={profile} formatById={formatById} onOpen={() => openProfile(profile)} onCopy={() => duplicateProfile(profile)} />)}
          <button className="cf-card cf-card-add" onClick={beginNew}>
            <Icon name="plus" size={22} />
            <strong>New profile</strong>
            <span>Score formats for a library</span>
          </button>
        </div>}

    {editorOpen && <Modal
      wide
      className="qp-editor-modal"
      eyebrow={creating ? 'NEW QUALITY PROFILE' : 'EDIT QUALITY PROFILE'}
      title={name || 'Untitled Quality Profile'}
      onClose={closeEditor}
      footer={<>
        {!creating && selected && <Button variant="danger" onClick={() => void remove()} disabled={busy}>Delete</Button>}
        {!creating && selected && <Button variant="ghost" icon="copy" onClick={() => duplicateProfile(selected)} disabled={busy}>Create a Copy</Button>}
        <span className={`footer-state ${dirty ? '' : 'clean'}`}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
        <Button variant="secondary" onClick={closeEditor} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={() => void save()} disabled={busy || !dirty || !name.trim() || !qualityIds.length}>{busy ? 'Saving…' : 'Save profile'}</Button>
      </>}
    >
      <div className="cf-editor-shell qp-editor-shell">
        <aside className="cf-editor-rail">
          <div className="cf-editor-summary">
            <span className={`cf-editor-status ${selectedFormatCount ? 'enabled' : ''}`}><i />{selectedFormatCount ? 'Scoring enabled' : 'Warning only'}</span>
            <strong>{name || 'Untitled profile'}</strong>
            <small>{qualityIds.length} qualit{qualityIds.length === 1 ? 'y' : 'ies'} · {selectedFormatCount} scored format{selectedFormatCount === 1 ? '' : 's'}</small>
          </div>
          <nav aria-label="Quality Profile editor sections">
            <button className={editorView === 'details' ? 'active' : ''} onClick={() => setEditorView('details')}><span>1</span><div><strong>Details</strong><small>Name and warning floor</small></div><Icon name="chevron" size={14} /></button>
            <button className={editorView === 'qualities' ? 'active' : ''} onClick={() => setEditorView('qualities')}><span>2</span><div><strong>Qualities</strong><small>{qualityIds.length} enabled</small></div><Icon name="chevron" size={14} /></button>
            <button className={editorView === 'formats' ? 'active' : ''} onClick={() => setEditorView('formats')}><span>3</span><div><strong>Format scores</strong><small>{selectedFormatCount} selected</small></div><Icon name="chevron" size={14} /></button>
          </nav>
          <div className="qp-editor-rail-note"><Icon name="activity" size={15} /><span>Scores only rank results. Nothing is hidden or blocked.</span></div>
        </aside>

        <div className="cf-editor-content">
          {editorView === 'details' && <section className="cf-editor-panel qp-details-panel">
            <div className="cf-editor-panel-head"><div className="eyebrow">STEP 1 OF 3</div><h3>Profile details</h3><p>Name this ranking policy and optionally choose when a release should receive a quality warning.</p></div>
            <div className="qp-details-fields">
              <Field label="Profile name" help="Shown when assigning this profile to a movie or show.">
                <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Remux first" autoFocus={creating} />
              </Field>
              <Field label="Minimum quality warning" help="Lower qualities remain visible and downloadable; they are only marked with a warning.">
                <Select value={minimumId} onChange={(event) => setMinimumId(event.target.value)}>
                  <option value="">No minimum warning</option>
                  {qualityIds.map((qualityId) => {
                    const quality = qualities.find((item) => item.id === qualityId)
                    return quality ? <option value={quality.id} key={quality.id}>{quality.name}</option> : null
                  })}
                </Select>
              </Field>
            </div>
            <div className="qp-warning-note"><Icon name="alert" size={16} /><div><strong>This profile never blocks a release</strong><span>The minimum is informational, while scores change result ordering only.</span></div></div>
            <div className="cf-editor-next"><Button variant="primary" onClick={() => setEditorView('qualities')} disabled={!name.trim()}>Continue to qualities</Button></div>
          </section>}

          {editorView === 'qualities' && <section className="cf-editor-panel qp-qualities-panel">
            <div className="cf-editor-panel-head cf-editor-panel-head-compact"><div><div className="eyebrow">STEP 2 OF 3</div><h3>Quality order</h3></div><p>Enabled qualities rank from top to bottom. Drag them into your preferred order.</p></div>
            <div className="qp-quality-toolbar">
              <span><strong>{qualityIds.length}</strong> of {qualities.length} enabled</span>
              <Button variant="secondary" onClick={() => setQualityIds(qualityOrderIds)} disabled={qualityIds.length === qualities.length}>Select all</Button>
            </div>
            <div className="qp-quality-list">
              {orderedQualities.map((quality) => {
                const enabled = qualityIds.includes(quality.id)
                const position = qualityIds.indexOf(quality.id)
                return <div
                  className={`qp-quality-row ${enabled ? 'selected' : ''} ${draggedQualityId === quality.id ? 'dragging' : ''}`}
                  key={quality.id}
                  draggable
                  onDragStart={() => setDraggedQualityId(quality.id)}
                  onDragEnd={() => setDraggedQualityId('')}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => { if (draggedQualityId) moveQuality(draggedQualityId, quality.id); setDraggedQualityId('') }}
                >
                  <span className="qp-quality-handle" title="Drag to reorder"><Icon name="menu" size={16} /></span>
                  <label className="qp-format-check"><input type="checkbox" checked={enabled} onChange={() => toggleQuality(quality.id)} /><span /></label>
                  <div className="qp-quality-copy"><strong>{quality.name}</strong></div>
                  {enabled ? <strong className="qp-quality-position">#{position + 1}</strong> : <span className="muted">Not used</span>}
                  <div className="qp-quality-actions">
                    <button className="icon-button qp-move-up" aria-label={`Move ${quality.name} up`} onClick={() => moveQualityBy(quality.id, -1)} disabled={!enabled || position === 0}><Icon name="arrow" size={14} /></button>
                    <button className="icon-button qp-move-down" aria-label={`Move ${quality.name} down`} onClick={() => moveQualityBy(quality.id, 1)} disabled={!enabled || position === qualityIds.length - 1}><Icon name="arrow" size={14} /></button>
                  </div>
                </div>
              })}
            </div>
            {!qualityIds.length && <div className="settings-note error"><Icon name="alert" size={15} /><span>Select at least one quality before saving this profile.</span></div>}
            <div className="cf-editor-next"><Button variant="secondary" onClick={() => setEditorView('details')}>Back to details</Button><Button variant="primary" onClick={() => setEditorView('formats')} disabled={!qualityIds.length}>Continue to format scores</Button></div>
          </section>}

          {editorView === 'formats' && <section className="cf-editor-panel qp-formats-panel">
            <div className="cf-editor-panel-head cf-editor-panel-head-compact"><h3>Format scores</h3><p>Profile base score + matched rule offset = contribution. Saving orders each section from highest to lowest score.</p></div>
            <div className="qp-format-toolbar">
              <div className="search-field"><Icon name="search" size={15} /><Input value={formatFilter} onChange={(event) => setFormatFilter(event.target.value)} placeholder="Filter formats…" /></div>
              <div className="qp-view-switch"><button className={scoreView === 'all' ? 'active' : ''} onClick={() => setScoreView('all')}>All</button><button className={scoreView === 'selected' ? 'active' : ''} onClick={() => setScoreView('selected')}>Selected <span>{selectedFormatCount}</span></button></div>
              <label className="qp-select-all"><input type="checkbox" checked={formats.length > 0 && formats.every((format) => Object.prototype.hasOwnProperty.call(scores, format.id))} onChange={(event) => setFormatSelection(formats.map((format) => format.id), event.target.checked)} /><span>Select all formats</span></label>
            </div>
            <div className="qp-format-catalog">
              {formatSections.map((section) => <section className="qp-format-section" key={section.id}>
                <div className="qp-format-section-head"><strong>{section.name}</strong><label><input type="checkbox" checked={section.formatIds.length > 0 && section.formatIds.every((formatId) => Object.prototype.hasOwnProperty.call(scores, formatId))} onChange={(event) => setFormatSelection(section.formatIds, event.target.checked)} /><span>{section.formatIds.filter((formatId) => Object.prototype.hasOwnProperty.call(scores, formatId)).length}/{section.formatIds.length} selected</span></label></div>
                <div className="qp-format-list">{section.formats.map((format) => {
                  const included = Object.prototype.hasOwnProperty.call(scores, format.id)
                  const score = scores[format.id] ?? 0
                  const configuredOffsets = format.conditions.map((condition) => condition.scoreOffset).filter(Boolean)
                  return <div className={`qp-format-row ${included ? 'selected' : ''}`} key={format.id}>
                    <label className="qp-format-check"><input type="checkbox" checked={included} onChange={() => toggleFormat(format.id)} /><span /></label>
                    <div className="qp-format-copy"><div><strong>{format.name}</strong>{format.builtin && <Badge tone="blue">Built-in</Badge>}{!format.enabled && <Badge tone="neutral">Disabled</Badge>}{configuredOffsets.length > 0 && <Badge tone="neutral">Offset {scoreLabel(Math.max(...configuredOffsets))}</Badge>}</div></div>
                    <label className="qp-format-score"><span>Score</span><Input type="number" value={String(score)} onChange={(event) => setScores((current) => ({ ...current, [format.id]: Number(event.target.value) || 0 }))} /></label>
                    <strong className={score < 0 ? 'score-negative' : score > 0 ? 'score-positive' : 'muted'}>{included ? scoreLabel(score) : '—'}</strong>
                  </div>
                })}</div>
              </section>)}
              {!formatSections.length && <div className="cf-test-placeholder"><Icon name="search" size={18} /><strong>No formats found</strong><span>Change the filter or show all formats.</span></div>}
            </div>
            <div className="cf-editor-next"><Button variant="secondary" onClick={() => setEditorView('qualities')}>Back to qualities</Button></div>
          </section>}
        </div>
      </div>
    </Modal>}
  </main>
}

/** One profile at a glance: its floor, and what it actually rewards or avoids. */
function ProfileCard({ profile, formatById, onOpen, onCopy }: { profile: QualityProfile; formatById: Map<string, CustomFormat>; onOpen: () => void; onCopy: () => void }) {
  // Strongest opinions first — the scores that most change a release's ranking
  // say more about a profile than an alphabetical list would.
  const ranked = [...profile.customFormatScores].sort((a, b) => Math.abs(b.score) - Math.abs(a.score))
  return <article
    className="cf-card"
    role="button"
    tabIndex={0}
    onClick={onOpen}
    onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen() } }}
  >
    <div className="cf-card-head">
      <strong>{profile.name}</strong>
      <span className="cf-card-badges">
        <Badge tone={profile.minimumQuality ? 'blue' : 'neutral'}>{profile.minimumQuality?.name ?? 'No minimum'}</Badge>
        <button
          type="button"
          className="icon-button cf-card-copy"
          aria-label={`Create a copy of ${profile.name}`}
          title="Create a copy"
          onClick={(event) => { event.stopPropagation(); onCopy() }}
        ><Icon name="copy" size={15} /></button>
      </span>
    </div>
    <div className="cf-card-conditions">
      {ranked.slice(0, 5).map((entry) => <span className="cf-condition-chip" key={entry.customFormatId}>
        {formatById.get(entry.customFormatId)?.name ?? 'Deleted format'}
        <code className={entry.score < 0 ? 'score-negative' : entry.score > 0 ? 'score-positive' : undefined}>{scoreLabel(entry.score)}</code>
      </span>)}
      {ranked.length > 5 && <span className="cf-condition-chip cf-condition-more">+{ranked.length - 5} more</span>}
      {!ranked.length && <span className="cf-condition-chip cf-condition-more">No scored formats</span>}
    </div>
    <div className="cf-card-foot">
      <span>{profile.assignedTitles} title{profile.assignedTitles === 1 ? '' : 's'}</span>
      <span>{profile.customFormatScores.length} scored format{profile.customFormatScores.length === 1 ? '' : 's'}</span>
    </div>
  </article>
}
