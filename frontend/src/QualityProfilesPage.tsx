import { useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import { Icon } from './components/Icon'
import { Modal } from './components/Modal'
import { Field } from './components/settings'
import { Badge, Button, EmptyState, Input, Select } from './components/ui'
import { useUrlState } from './lib/urlState'
import type { CustomFormat, QualityDefinition, QualityProfile } from './types'

const scoreLabel = (value: number) => `${value > 0 ? '+' : ''}${value}`

export default function QualityProfilesPage() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [qualities, setQualities] = useState<QualityDefinition[]>([])
  const [formats, setFormats] = useState<CustomFormat[]>([])
  const [selectedId, setSelectedId] = useUrlState('profile')
  const [editorOpen, setEditorOpen] = useState(false)
  const [name, setName] = useState('')
  const [minimumId, setMinimumId] = useState('')
  const [scores, setScores] = useState<Record<string, number>>({})
  const [addingFormat, setAddingFormat] = useState('')
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const selected = profiles.find((item) => item.id === selectedId)
  const formatById = useMemo(() => new Map(formats.map((format) => [format.id, format])), [formats])

  const load = async () => {
    try {
      const [profileRows, qualityRows, formatRows] = await Promise.all([api.qualityProfiles(), api.qualityDefinitions(), api.customFormats()])
      setProfiles(profileRows); setQualities(qualityRows); setFormats(formatRows)
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
    setScores(Object.fromEntries(current.customFormatScores.map((item) => [item.customFormatId, item.score])))
  }, [selectedId, profiles, creating])

  const dirty = useMemo(() => {
    if (creating) return Boolean(name.trim()) || Boolean(minimumId) || Object.keys(scores).length > 0
    if (!selected) return false
    const original = Object.fromEntries(selected.customFormatScores.map((item) => [item.customFormatId, item.score]))
    return name !== selected.name
      || minimumId !== (selected.minimumQuality?.id ?? '')
      || JSON.stringify(original) !== JSON.stringify(scores)
  }, [creating, name, minimumId, scores, selected])

  const openProfile = (profile: QualityProfile) => {
    setCreating(false)
    setSelectedId(profile.id)
    setName(profile.name)
    setMinimumId(profile.minimumQuality?.id ?? '')
    setScores(Object.fromEntries(profile.customFormatScores.map((item) => [item.customFormatId, item.score])))
    setAddingFormat(''); setMessage(''); setEditorOpen(true)
  }
  const beginNew = () => {
    setCreating(true); setSelectedId(''); setName(''); setMinimumId(''); setScores({})
    setAddingFormat(''); setMessage(''); setEditorOpen(true)
  }
  const closeEditor = () => { setEditorOpen(false); setCreating(false); setSelectedId('') }

  const save = async () => {
    if (!name.trim()) { setMessage('Profile name is required.'); return }
    setBusy(true); setMessage('Saving profile…')
    const custom_format_scores = Object.entries(scores).map(([custom_format_id, score]) => ({ custom_format_id, score }))
    try {
      if (creating) {
        const created = await api.createQualityProfile({ name: name.trim(), minimum_quality_definition_id: minimumId || null, custom_format_scores })
        setProfiles((items) => [...items, created].sort((a, b) => a.name.localeCompare(b.name)))
        setCreating(false); setSelectedId(created.id); setEditorOpen(false); setMessage('Quality Profile created.')
      } else if (selected) {
        const updated = await api.updateQualityProfile(selected.id, { name: name.trim(), minimum_quality_definition_id: minimumId || null, custom_format_scores, expected_revision: selected.revision })
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

  const availableFormats = useMemo(
    () => formats.filter((format) => !Object.prototype.hasOwnProperty.call(scores, format.id)),
    [formats, scores])
  const addFormat = () => {
    if (!addingFormat) return
    setScores((value) => ({ ...value, [addingFormat]: 0 })); setAddingFormat('')
  }

  const scoreRows = Object.entries(scores)
    .sort(([left], [right]) => (formatById.get(left)?.name ?? left).localeCompare(formatById.get(right)?.name ?? right))

  return <main className="page">
    <div className="page-heading">
      <div className="heading-copy">
        <div className="eyebrow">MEDIALOGUE / QUALITY PROFILES</div>
        <h1>Quality Profiles</h1>
        <p>A warning-only minimum quality and signed Custom Format scores. Formats never carry a score themselves — a profile decides what each one is worth.</p>
      </div>
      <div className="heading-actions"><Button variant="primary" icon="plus" onClick={beginNew}>New profile</Button></div>
    </div>

    {message && <div className="settings-note"><Icon name="activity" size={15} /><span>{message}</span></div>}

    {!profiles.length
      ? <EmptyState icon="spark" title="No Quality Profiles yet" detail="Create one when you want minimum-quality warnings or Custom Format scoring on your searches." action={<Button variant="primary" icon="plus" onClick={beginNew}>Create the first one</Button>} />
      : <div className="cf-card-grid">
          {profiles.map((profile) => <ProfileCard key={profile.id} profile={profile} formatById={formatById} onOpen={() => openProfile(profile)} />)}
          <button className="cf-card cf-card-add" onClick={beginNew}>
            <Icon name="plus" size={22} />
            <strong>New profile</strong>
            <span>Score formats for a library</span>
          </button>
        </div>}

    {editorOpen && <Modal
      wide
      eyebrow={creating ? 'NEW QUALITY PROFILE' : 'EDIT QUALITY PROFILE'}
      title={name || 'Untitled Quality Profile'}
      onClose={closeEditor}
      footer={<>
        {!creating && selected && <Button variant="danger" onClick={() => void remove()} disabled={busy}>Delete</Button>}
        <span className={`footer-state ${dirty ? '' : 'clean'}`}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
        <Button variant="secondary" onClick={closeEditor} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={() => void save()} disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save profile'}</Button>
      </>}
    >
      <div className="cf-modal-grid">
        <Field label="Profile name" help="Shown wherever this profile is assigned to a movie or show.">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Remux first" />
        </Field>
        <Field label="Minimum quality" help="Releases below this are flagged with a warning. They are never hidden, and nothing is blocked from being grabbed.">
          <Select value={minimumId} onChange={(event) => setMinimumId(event.target.value)}>
            <option value="">No minimum warning</option>
            {qualities.map((quality) => <option value={quality.id} key={quality.id}>{quality.name}</option>)}
          </Select>
        </Field>
      </div>

      <div className="cf-modal-section">
        <div className="section-title">
          <h3>Custom Format scores</h3>
          <div className="search-client-chooser">
            <Select value={addingFormat} onChange={(event) => setAddingFormat(event.target.value)}>
              <option value="">Add Custom Format…</option>
              {availableFormats.map((format) => <option value={format.id} key={format.id}>{format.name}</option>)}
            </Select>
            <Button variant="secondary" icon="plus" onClick={addFormat} disabled={!addingFormat}>Add</Button>
          </div>
        </div>
        <p className="cf-modal-note">
          A release scores the sum of every format it matches. Positive scores prefer a release and negative ones push it down the list — <strong>nothing is ever hidden or blocked</strong>. A matching format with no score here contributes 0 and still appears in search evidence.
        </p>

        {scoreRows.length ? <div className="qp-score-list">
          {scoreRows.map(([formatId, score]) => {
            const format = formatById.get(formatId)
            return <div className="qp-score-row" key={formatId}>
              <span className="qp-score-name">
                <strong>{format?.name ?? 'Deleted Custom Format'}</strong>
                <small>{!format ? 'This format no longer exists' : format.enabled === false ? 'Format is disabled' : `${format.conditionCount} condition${format.conditionCount === 1 ? '' : 's'}`}</small>
              </span>
              <Input type="number" value={String(score)} onChange={(event) => setScores((value) => ({ ...value, [formatId]: Number(event.target.value) || 0 }))} />
              <strong className={score < 0 ? 'score-negative' : score > 0 ? 'score-positive' : 'muted'}>{scoreLabel(score)}</strong>
              <button className="icon-button" aria-label={`Remove ${format?.name ?? 'format'}`} onClick={() => setScores((value) => { const next = { ...value }; delete next[formatId]; return next })}><Icon name="close" size={15} /></button>
            </div>
          })}
        </div> : <div className="cf-test-placeholder">
          <Icon name="sliders" size={18} />
          <strong>No scored formats</strong>
          <span>This profile only warns on minimum quality. Add a Custom Format above to start ranking releases.</span>
        </div>}
      </div>
    </Modal>}
  </main>
}

/** One profile at a glance: its floor, and what it actually rewards or avoids. */
function ProfileCard({ profile, formatById, onOpen }: { profile: QualityProfile; formatById: Map<string, CustomFormat>; onOpen: () => void }) {
  // Strongest opinions first — the scores that most change a release's ranking
  // say more about a profile than an alphabetical list would.
  const ranked = [...profile.customFormatScores].sort((a, b) => Math.abs(b.score) - Math.abs(a.score))
  return <button className="cf-card" onClick={onOpen}>
    <div className="cf-card-head">
      <strong>{profile.name}</strong>
      <Badge tone={profile.minimumQuality ? 'blue' : 'neutral'}>{profile.minimumQuality?.name ?? 'No minimum'}</Badge>
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
  </button>
}
