import { useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import { Icon } from './components/Icon'
import { Badge, Button, EmptyState, Input, Panel, Select } from './components/ui'
import type { CustomFormat, QualityDefinition, QualityProfile } from './types'

function scoreLabel(value: number) { return `${value > 0 ? '+' : ''}${value}` }

export default function QualityProfilesPage() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [qualities, setQualities] = useState<QualityDefinition[]>([])
  const [formats, setFormats] = useState<CustomFormat[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [name, setName] = useState('')
  const [minimumId, setMinimumId] = useState('')
  const [scores, setScores] = useState<Record<string, number>>({})
  const [addingFormat, setAddingFormat] = useState('')
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const selected = profiles.find((item) => item.id === selectedId)
  const load = async () => {
    try {
      const [profileRows, qualityRows, formatRows] = await Promise.all([api.qualityProfiles(), api.qualityDefinitions(), api.customFormats()])
      setProfiles(profileRows); setQualities(qualityRows); setFormats(formatRows)
      if (!selectedId && profileRows[0]) setSelectedId(profileRows[0].id)
      setMessage('')
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

  const beginNew = () => {
    setCreating(true); setSelectedId(''); setName(''); setMinimumId(''); setScores({}); setMessage('')
  }
  const save = async () => {
    if (!name.trim()) { setMessage('Profile name is required.'); return }
    setBusy(true); setMessage('Saving profile…')
    const custom_format_scores = Object.entries(scores).map(([custom_format_id, score]) => ({ custom_format_id, score }))
    try {
      if (creating) {
        const created = await api.createQualityProfile({ name: name.trim(), minimum_quality_definition_id: minimumId || null, custom_format_scores })
        setProfiles((items) => [...items, created].sort((a, b) => a.name.localeCompare(b.name)))
        setCreating(false); setSelectedId(created.id); setMessage('Quality Profile created.')
      } else if (selected) {
        const updated = await api.updateQualityProfile(selected.id, { name: name.trim(), minimum_quality_definition_id: minimumId || null, custom_format_scores, expected_revision: selected.revision })
        setProfiles((items) => items.map((item) => item.id === updated.id ? updated : item).sort((a, b) => a.name.localeCompare(b.name)))
        setMessage('Quality Profile saved. Assigned release scores were re-evaluated.')
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
      const remaining = profiles.filter((item) => item.id !== selected.id)
      setProfiles(remaining); setSelectedId(remaining[0]?.id ?? ''); setMessage('Quality Profile deleted. Media and torrent data were untouched.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : 'Could not delete Quality Profile.') }
    finally { setBusy(false) }
  }
  const availableFormats = useMemo(() => formats.filter((format) => !Object.prototype.hasOwnProperty.call(scores, format.id)), [formats, scores])
  const addFormat = () => {
    if (!addingFormat) return
    setScores((value) => ({ ...value, [addingFormat]: 0 })); setAddingFormat('')
  }
  const formatById = new Map(formats.map((format) => [format.id, format]))

  return <main className="page"><div className="page-header"><div><div className="eyebrow">SCORING</div><h1>Quality Profiles</h1><p>Set a warning-only minimum quality and signed Custom Format scores. Custom Formats themselves never own scores.</p></div><Button variant="primary" icon="plus" onClick={beginNew}>New profile</Button></div>
    {message && <div className="settings-note"><Icon name="activity" size={16} /><span>{message}</span></div>}
    <div className="profile-layout">
      <Panel className="profile-list" title="Profiles" eyebrow={`${profiles.length} PROFILES`}>
        {profiles.map((profile) => <button className={`profile-item ${!creating && profile.id === selectedId ? 'selected' : ''}`} key={profile.id} onClick={() => { setCreating(false); setSelectedId(profile.id) }}><span className="profile-icon"><Icon name="sliders" size={16} /></span><span><strong>{profile.name}</strong><small>{profile.assignedTitles} titles assigned · {profile.customFormatScores.length} scored formats</small></span><Icon name="chevron" size={15} /></button>)}
        {!profiles.length && !creating && <EmptyState title="No Quality Profiles" detail="Create one when you want minimum-quality warnings or Custom Format scoring." />}
      </Panel>
      <Panel className="profile-editor" eyebrow="PROFILE SETTINGS" title={creating ? 'New Quality Profile' : selected?.name ?? 'Select a profile'} action={(creating || selected) ? <div className="detail-actions">{selected && !creating && <Button variant="ghost" onClick={() => void remove()} disabled={busy}>Delete</Button>}<Button variant="primary" onClick={() => void save()} disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</Button></div> : undefined}>
        {(creating || selected) ? <>
          <div className="profile-intro"><div className="profile-hero-icon"><Icon name="sliders" size={21} /></div><div><strong>Manual search remains fully permissive.</strong><span>Below-minimum releases get a warning only. Negative scores never hide or block a result.</span></div></div>
          <div className="field-grid"><label><span className="field-label">Profile name</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label><label><span className="field-label">Minimum quality</span><Select value={minimumId} onChange={(event) => setMinimumId(event.target.value)}><option value="">No minimum warning</option>{qualities.map((quality) => <option value={quality.id} key={quality.id}>{quality.name}</option>)}</Select></label></div>
          <div className="settings-note"><Icon name="shield" size={16} /><span>Quality Definitions are hardcoded by Medialogue. This selector only chooses the warning floor; it does not create an upgrade ladder.</span></div>
          <div className="score-section"><div className="section-title"><div><div className="eyebrow">SCORING</div><h3>Custom Format scores</h3></div><div className="search-client-chooser"><Select value={addingFormat} onChange={(event) => setAddingFormat(event.target.value)}><option value="">Add Custom Format…</option>{availableFormats.map((format) => <option value={format.id} key={format.id}>{format.name}</option>)}</Select><Button variant="ghost" icon="plus" onClick={addFormat} disabled={!addingFormat}>Add</Button></div></div>
            {Object.entries(scores).sort(([left], [right]) => (formatById.get(left)?.name ?? left).localeCompare(formatById.get(right)?.name ?? right)).map(([formatId, score]) => <div className="score-row" key={formatId}><span><strong>{formatById.get(formatId)?.name ?? 'Deleted Custom Format'}</strong><small>{formatById.get(formatId)?.enabled === false ? 'Disabled format' : 'Matched score contribution'}</small></span><Input type="number" value={String(score)} onChange={(event) => setScores((value) => ({ ...value, [formatId]: Number(event.target.value) || 0 }))} /><strong className={score < 0 ? 'score-negative' : 'score-positive'}>{scoreLabel(score)}</strong><button className="icon-button" onClick={() => setScores((value) => { const next = { ...value }; delete next[formatId]; return next })}><Icon name="close" size={14} /></button></div>)}
            {!Object.keys(scores).length && <EmptyState title="No scored formats" detail="A matching Custom Format with no profile score contributes 0 and is still shown in search evidence." />}
          </div>
        </> : <EmptyState title="Select a profile" detail="Choose an existing Quality Profile or create a new one." />}
      </Panel>
    </div>
  </main>
}
